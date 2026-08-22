from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .adapters import AdapterRegistry
from .discovery import DiscoveryResult, freeze_sources
from .model import ParseResult, SourceFile, UnifiedEvent, display_path, stable_hash
from .render import compatibility_markdown, render_knowledge
from .sanitize import Sanitizer, actor_kind, classify_flags, evidence_grade


BOUNDARY_BYTES = 65536
STREAM_CHUNK_BYTES = 8 * 1024 * 1024


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def _read_exact(path: Path, start: int, end: int) -> bytes:
    if end < start:
        raise ValueError("invalid source byte range")
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read(end - start)
    if len(data) != end - start:
        raise OSError(f"source shorter than frozen range: wanted {end - start}, got {len(data)}")
    return data


def _merge_parse_results(total: ParseResult, part: ParseResult) -> None:
    total.events.extend(part.events)
    total.excluded.extend(part.excluded)
    total.errors.extend(part.errors)
    total.records_seen += part.records_seen
    total.context = dict(part.context)
    total.last_complete_offset = max(total.last_complete_offset, part.last_complete_offset)


def _parse_frozen_source(
    adapter: Any,
    source: SourceFile,
    start_offset: int,
    context: dict[str, Any],
) -> tuple[ParseResult, int]:
    """Parse append-only JSONL in bounded chunks while honoring the frozen byte end."""
    byte_count = source.frozen_size - start_offset
    if byte_count < 0:
        raise ValueError("source frozen size is smaller than the requested start offset")
    if not adapter.append_only:
        data = _read_exact(source.path, start_offset, source.frozen_size)
        parse_context = dict(context)
        parse_context["_start_offset"] = start_offset
        return adapter.parse(data, source, context=parse_context), len(data)

    total = ParseResult(context=dict(context), last_complete_offset=start_offset)
    buffer = b""
    buffer_start = start_offset
    position = start_offset
    with source.path.open("rb") as handle:
        handle.seek(start_offset)
        while position < source.frozen_size:
            wanted = min(STREAM_CHUNK_BYTES, source.frozen_size - position)
            chunk = handle.read(wanted)
            if len(chunk) != wanted:
                raise OSError(f"source shorter than frozen range: wanted {wanted}, got {len(chunk)}")
            position += len(chunk)
            buffer += chunk
            newline = buffer.rfind(b"\n")
            if newline < 0:
                continue
            complete = buffer[: newline + 1]
            parse_context = dict(total.context)
            parse_context["_start_offset"] = buffer_start
            part = adapter.parse(complete, source, context=parse_context)
            _merge_parse_results(total, part)
            buffer = buffer[newline + 1 :]
            buffer_start += len(complete)
        if buffer:
            parse_context = dict(total.context)
            parse_context["_start_offset"] = buffer_start
            part = adapter.parse(buffer, source, context=parse_context)
            _merge_parse_results(total, part)
    return total, byte_count


def _hash_range(path: Path, start: int, length: int) -> str:
    if length <= 0:
        return sha256(b"").hexdigest()
    return sha256(_read_exact(path, start, start + length)).hexdigest()


def _boundary_state(path: Path, frozen_size: int) -> dict[str, Any]:
    head_length = min(BOUNDARY_BYTES, frozen_size)
    tail_length = min(BOUNDARY_BYTES, frozen_size)
    tail_start = max(0, frozen_size - tail_length)
    return {
        "head_length": head_length,
        "head_sha256": _hash_range(path, 0, head_length),
        "tail_start": tail_start,
        "tail_length": tail_length,
        "tail_sha256": _hash_range(path, tail_start, tail_length),
    }


def _append_verified(path: Path, source: SourceFile, previous: dict[str, Any]) -> bool:
    old_size = int(previous.get("frozen_size", -1))
    if old_size < 0 or source.frozen_size < old_size:
        return False
    old_inode = int(previous.get("inode", 0))
    if old_inode and source.inode and old_inode != source.inode:
        return False
    if source.frozen_size == old_size:
        return source.mtime_ns == int(previous.get("mtime_ns", -1))
    try:
        head_length = int(previous.get("head_length", 0))
        tail_start = int(previous.get("tail_start", 0))
        tail_length = int(previous.get("tail_length", 0))
        return (
            _hash_range(path, 0, head_length) == previous.get("head_sha256")
            and _hash_range(path, tail_start, tail_length) == previous.get("tail_sha256")
        )
    except (OSError, ValueError):
        return False


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n" for value in values)
    _atomic_write_text(path, text)


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(path: Path) -> list[UnifiedEvent]:
    if not path.is_file():
        return []
    events: list[UnifiedEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                events.append(UnifiedEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid previous events at line {line_number}: {exc}") from exc
    return events


def _load_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path} line {line_number}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _display_working_dir(raw: str | None, sanitizer: Sanitizer) -> str | None:
    if not raw:
        return None
    try:
        return sanitizer.sanitize_text(display_path(Path(raw)))
    except (OSError, ValueError):
        return sanitizer.sanitize_text(raw)


def _project_identity(raw_project_id: str | None, working_dir: str | None, agent: str, session_id: str) -> tuple[str, str]:
    if working_dir and working_dir not in {"~", "/", ".", "unknown"}:
        label = Path(working_dir).name or working_dir
        return "cwd:" + stable_hash(working_dir, length=24), label
    if raw_project_id:
        return "project:" + stable_hash(agent, raw_project_id, length=24), str(raw_project_id)
    return "", f"Session {session_id[:12]}"


def _normalize_raw_event(raw: Any, source: SourceFile, sanitizer: Sanitizer) -> tuple[UnifiedEvent | None, str | None]:
    content = sanitizer.sanitize_text(raw.content)
    flags = classify_flags(content, raw.flags)
    if "[BINARY_REMOVED" in content:
        flags = sorted(set(flags) | {"binary_removed"})
    if "[REDACTED:" in content:
        flags = sorted(set(flags) | {"private_data_redacted"})
    metadata = sanitizer.sanitize_mapping(raw.metadata)
    actor = actor_kind(raw.role, flags, raw.actor_hint)
    quarantine_reasons = (
        ("runtime_injection", "runtime_injection"),
        ("compacted_summary", "compacted_summary"),
        ("imported_transcript", "imported_transcript"),
        ("nested_approval", "nested_approval_transcript"),
        ("delegated_transcript", "delegated_transcript"),
        ("synthetic_fixture", "synthetic_fixture"),
    )
    for flag, reason in quarantine_reasons:
        if flag in flags:
            return None, reason
    working_dir = _display_working_dir(raw.working_dir, sanitizer)
    project_key, project_label = _project_identity(raw.project_id, working_dir, source.agent_name, raw.session_id)
    content_hash = sha256(content.encode("utf-8")).hexdigest()
    provisional_id = "evt-" + stable_hash(
        source.source_file_id,
        raw.record_locator,
        raw.role,
        raw.event_type,
        content_hash,
        raw.call_id,
        length=32,
    )
    event = UnifiedEvent(
        event_id=provisional_id,
        source_agent=source.agent_name,
        adapter=source.adapter,
        source_file_id=source.source_file_id,
        source_relpath=sanitizer.sanitize_text(source.relative_path),
        session_id=raw.session_id,
        parent_session_id=raw.parent_session_id,
        logical_session_id="pending",
        record_locator=raw.record_locator,
        sequence=raw.sequence,
        timestamp=raw.timestamp,
        role=raw.role,
        actor_kind=actor,
        event_type=raw.event_type,
        evidence_grade=evidence_grade(raw.role, raw.event_type, actor),
        content=content,
        content_sha256=content_hash,
        flags=flags,
        call_id=raw.call_id,
        tool_name=sanitizer.sanitize_text(raw.tool_name) if raw.tool_name else None,
        project_key=project_key or None,
        project_label=sanitizer.sanitize_text(project_label),
        session_title=sanitizer.sanitize_text(raw.session_title) if raw.session_title else None,
        working_dir=working_dir,
        metadata=metadata if isinstance(metadata, dict) else {},
    )
    return event, None


def _assign_logical_sessions(events: list[UnifiedEvent]) -> None:
    union = UnionFind()
    session_nodes: dict[tuple[str, str], str] = {}
    for event in events:
        node = f"{event.source_agent}:{event.session_id}"
        session_nodes[(event.source_agent, event.session_id)] = node
        union.add(node)
        if event.parent_session_id:
            union.union(node, f"{event.source_agent}:{event.parent_session_id}")
        if event.source_agent == "Clacky":
            base = re.sub(r"-chunk-\d+(?=\.md$)", "", event.source_relpath, flags=re.IGNORECASE)
            base = re.sub(r"\.(?:json|md)$", "", base, flags=re.IGNORECASE)
            alias = "Clacky:file:" + base
            union.union(node, alias)
    members: dict[str, set[str]] = defaultdict(set)
    for node in list(union.parent):
        if ":file:" in node:
            continue
        members[union.find(node)].add(node)
    logical: dict[str, str] = {}
    for root, nodes in members.items():
        logical[root] = "session-" + stable_hash("|".join(sorted(nodes)), length=24)
    for event in events:
        node = session_nodes[(event.source_agent, event.session_id)]
        event.logical_session_id = logical[union.find(node)]
        if not event.project_key or event.project_key.startswith("session:"):
            event.project_key = "session:" + event.logical_session_id
            event.project_label = event.session_title or f"Session chain {event.logical_session_id[-12:]}"
        event.event_id = "evt-" + stable_hash(
            event.source_file_id,
            event.record_locator,
            event.role,
            event.event_type,
            event.content_sha256,
            event.call_id,
            length=32,
        )


def _semantic_key(event: UnifiedEvent) -> tuple[str, ...]:
    normalized_content = re.sub(r"\s+", " ", event.content).strip()
    content_hash = sha256(normalized_content.encode("utf-8")).hexdigest()
    linkage = event.call_id or "" if event.event_type not in {"message", "attachment"} else ""
    return (
        event.logical_session_id,
        event.role,
        event.event_type,
        event.tool_name or "",
        linkage,
        content_hash,
    )


def _lane_priority(event: UnifiedEvent) -> tuple[int, int, str]:
    lane = str(event.metadata.get("transport_lane") or "")
    priority = {
        "codex-event-message": 100,
        "clacky-json": 90,
        "claude-code": 80,
        "workbuddy": 80,
        "codex-response-item": 60,
        "clacky-chunk": 50,
    }.get(lane, 70)
    actor_bonus = 10 if event.actor_kind == "primary_user" else 0
    return priority + actor_bonus, -event.sequence, event.event_id


def _deduplicate(events: list[UnifiedEvent]) -> tuple[list[UnifiedEvent], list[tuple[UnifiedEvent, UnifiedEvent, str]]]:
    """Remove transport mirrors while preserving genuine repeated statements."""
    by_id: dict[str, UnifiedEvent] = {}
    dropped: list[tuple[UnifiedEvent, UnifiedEvent, str]] = []
    for event in events:
        prior = by_id.get(event.event_id)
        if prior is None:
            by_id[event.event_id] = event
        else:
            dropped.append((event, prior, "same_source_record_replay"))
    unique = list(by_id.values())

    groups: dict[tuple[str, ...], list[UnifiedEvent]] = defaultdict(list)
    for event in unique:
        groups[_semantic_key(event)].append(event)
    retained: list[UnifiedEvent] = []
    mirror_pairs = {
        frozenset({"codex-event-message", "codex-response-item"}),
        frozenset({"clacky-json", "clacky-chunk"}),
    }
    for group in groups.values():
        lanes: dict[str, list[UnifiedEvent]] = defaultdict(list)
        for event in group:
            lanes[str(event.metadata.get("transport_lane") or "unknown")].append(event)
        known_mirror = any(pair.issubset(lanes) for pair in mirror_pairs)
        if not known_mirror:
            retained.extend(group)
            continue
        preferred_lane = max(lanes, key=lambda lane: max(_lane_priority(event) for event in lanes[lane]))
        preferred = sorted(lanes[preferred_lane], key=lambda event: (str(event.timestamp or ""), event.sequence, event.event_id))
        retained.extend(preferred)
        keep_count = len(preferred)
        for lane, lane_events in lanes.items():
            if lane == preferred_lane:
                continue
            ordered = sorted(lane_events, key=lambda event: (str(event.timestamp or ""), event.sequence, event.event_id))
            for index, event in enumerate(ordered):
                if index < keep_count:
                    target = preferred[min(index, keep_count - 1)]
                    dropped.append((event, target, "transport_mirror"))
                else:
                    retained.append(event)
    retained.sort(key=lambda item: (str(item.timestamp or ""), item.logical_session_id, item.sequence, item.event_id))
    return retained, dropped


def _canonical_record_locator(locator: str) -> str:
    if locator.startswith("byte:"):
        return locator.split(".", 1)[0]
    match = re.match(r"messages\[\d+\]", locator)
    if match:
        return match.group(0)
    if locator.startswith("section:"):
        return locator.split(".", 1)[0]
    return locator


def _transport_disposition(
    source_file_id: str,
    locator: str,
    disposition: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "stage": "transport",
        "source_file_id": source_file_id,
        "record_locator": locator,
        "canonical_record_locator": _canonical_record_locator(locator),
        "disposition": disposition,
        **extra,
    }


def _self_reconstruction_sessions(events: list[UnifiedEvent], output_dir: Path) -> set[tuple[str, str]]:
    try:
        absolute = str(output_dir.expanduser().resolve())
    except (OSError, RuntimeError):
        absolute = str(output_dir.expanduser())
    markers = {absolute, display_path(output_dir)}
    workflow_markers = ("session_kb.py", "audit/events.jsonl", "completion-report.json", "review/review.json", "review-init", "validate-review")
    sessions: set[tuple[str, str]] = set()
    for event in events:
        content = event.content
        if any(marker and marker in content for marker in markers) and any(marker in content for marker in workflow_markers):
            sessions.add((event.source_agent, event.session_id))
    return sessions


def _sensitive_count(stats: Counter[str]) -> int:
    return sum(
        stats[key]
        for key in (
            "credential_redactions",
            "credential_fields_redacted",
            "email_redactions",
            "phone_redactions",
        )
    )


def build_knowledge_base(
    registry: AdapterRegistry,
    sources: list[SourceFile],
    output_dir: Path,
    discovery: DiscoveryResult | None = None,
    snapshot: dict[str, Any] | None = None,
    incremental: bool = False,
    dry_run: bool = False,
    verified_adapters: set[str] | None = None,
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    if output_dir.exists() and any(output_dir.iterdir()) and not incremental and not dry_run:
        raise ValueError("output directory is not empty; use --incremental or choose a new output")
    snapshot_value = snapshot or freeze_sources(sources)
    snapshot_sources = {
        str(item.get("source_file_id")): item
        for item in snapshot_value.get("sources", [])
        if isinstance(item, dict) and item.get("source_file_id")
    }
    previous_state = _load_json(output_dir / "audit" / "state.json", {"sources": {}}) if incremental else {"sources": {}}
    previous_events = _load_events(output_dir / "audit" / "events.jsonl") if incremental else []
    source_state: dict[str, Any] = {}
    retained_previous = list(previous_events)
    new_events: list[UnifiedEvent] = []
    excluded: list[dict[str, Any]] = _load_jsonl_dicts(output_dir / "audit" / "excluded.jsonl") if incremental else []
    errors: list[dict[str, Any]] = _load_jsonl_dicts(output_dir / "audit" / "errors.jsonl") if incremental else []
    dispositions: list[dict[str, Any]] = _load_jsonl_dicts(output_dir / "audit" / "dispositions.jsonl") if incremental else []
    counters: Counter[str] = Counter()
    sanitizer = Sanitizer()
    old_memory_calls: set[tuple[str, str]] = set()

    for source in sources:
        counters["source_files"] += 1
        frozen_manifest = snapshot_sources.get(source.source_file_id, {})
        if frozen_manifest.get("head_sha256"):
            try:
                head_ok = _hash_range(source.path, 0, int(frozen_manifest.get("head_length", 0))) == frozen_manifest.get("head_sha256")
                tail_ok = _hash_range(
                    source.path,
                    int(frozen_manifest.get("tail_start", 0)),
                    int(frozen_manifest.get("tail_length", 0)),
                ) == frozen_manifest.get("tail_sha256")
            except (OSError, ValueError):
                head_ok = tail_ok = False
            if not head_ok or not tail_ok:
                errors.append(
                    {
                        "source_file_id": source.source_file_id,
                        "path": display_path(source.path),
                        "error": "source_boundary_changed_before_read",
                    }
                )
                dispositions.append(_transport_disposition(source.source_file_id, "source", "parse_error", error="source_boundary_changed_before_read"))
                counters["parse_errors"] += 1
                continue
        previous = previous_state.get("sources", {}).get(source.source_file_id)
        adapter = registry.get(source.adapter)
        start_offset = 0
        full_reparse = True
        if incremental and previous:
            if adapter.append_only and _append_verified(source.path, source, previous):
                start_offset = int(previous.get("last_complete_offset", 0))
                full_reparse = False
                counters["append_files_verified"] += 1
            elif not adapter.append_only and source.frozen_size == int(previous.get("frozen_size", -1)) and source.mtime_ns == int(previous.get("mtime_ns", -2)):
                source_state[source.source_file_id] = previous
                counters["unchanged_files_skipped"] += 1
                continue
            else:
                counters["reparsed_files"] += 1
                retained_previous = [event for event in retained_previous if event.source_file_id != source.source_file_id]
                excluded = [item for item in excluded if item.get("source_file_id") != source.source_file_id]
                errors = [item for item in errors if item.get("source_file_id") != source.source_file_id]
                dispositions = [item for item in dispositions if item.get("source_file_id") != source.source_file_id]
                errors.append(
                    {
                        "source_file_id": source.source_file_id,
                        "path": display_path(source.path),
                        "error": "incremental_fallback_full_reparse",
                    }
                )
        try:
            context = dict(previous.get("context", {})) if previous and not full_reparse else {}
            parsed, parsed_bytes = _parse_frozen_source(adapter, source, start_offset, context)
            counters["bytes_read"] += parsed_bytes
            if start_offset:
                counters["incremental_bytes_read"] += parsed_bytes
            elif incremental:
                counters["full_reparse_bytes_read"] += parsed_bytes
        except (OSError, ValueError) as exc:
            errors.append({"source_file_id": source.source_file_id, "path": display_path(source.path), "error": "source_read_error", "detail": str(exc)})
            dispositions.append(_transport_disposition(source.source_file_id, "source", "parse_error", error="source_read_error"))
            counters["parse_errors"] += 1
            continue
        counters["parsed_files"] += 1
        counters["records_seen"] += parsed.records_seen
        counters["excluded_records"] += len(parsed.excluded)
        counters["parse_errors"] += len(parsed.errors)
        for item in parsed.excluded:
            excluded.append({"source_file_id": source.source_file_id, "adapter": source.adapter, **item})
            dispositions.append(
                _transport_disposition(
                    source.source_file_id,
                    str(item.get("record_locator") or "unknown"),
                    "excluded",
                    reason=item.get("reason"),
                )
            )
        for item in parsed.errors:
            errors.append({"source_file_id": source.source_file_id, "adapter": source.adapter, **item})
            dispositions.append(
                _transport_disposition(
                    source.source_file_id,
                    str(item.get("record_locator") or "unknown"),
                    "parse_error",
                    error=item.get("error"),
                )
            )
        for raw in parsed.events:
            event, exclusion_reason = _normalize_raw_event(raw, source, sanitizer)
            if exclusion_reason:
                excluded.append({"source_file_id": source.source_file_id, "record_locator": raw.record_locator, "reason": exclusion_reason})
                dispositions.append(
                    _transport_disposition(
                        source.source_file_id,
                        raw.record_locator,
                        "quarantined",
                        reason=exclusion_reason,
                    )
                )
                counters["excluded_records"] += 1
                continue
            assert event is not None
            if "old_memory_reference" in event.flags and event.event_type in {"tool_call", "patch"}:
                if event.call_id:
                    old_memory_calls.add((source.source_file_id, event.call_id))
                excluded.append({"source_file_id": source.source_file_id, "record_locator": raw.record_locator, "reason": "old_memory_read"})
                dispositions.append(_transport_disposition(source.source_file_id, raw.record_locator, "quarantined", reason="old_memory_read"))
                counters["excluded_records"] += 1
                continue
            if event.call_id and (source.source_file_id, event.call_id) in old_memory_calls and event.event_type == "tool_result":
                excluded.append({"source_file_id": source.source_file_id, "record_locator": raw.record_locator, "reason": "old_memory_read_result"})
                dispositions.append(_transport_disposition(source.source_file_id, raw.record_locator, "quarantined", reason="old_memory_read_result"))
                counters["excluded_records"] += 1
                continue
            new_events.append(event)
        try:
            boundary = _boundary_state(source.path, source.frozen_size)
        except OSError as exc:
            boundary = {}
            errors.append({"source_file_id": source.source_file_id, "error": "boundary_hash_error", "detail": str(exc)})
        safe_context = sanitizer.sanitize_mapping(parsed.context)
        source_state[source.source_file_id] = {
            "path": display_path(source.path),
            "adapter": source.adapter,
            "agent_name": source.agent_name,
            "frozen_size": source.frozen_size,
            "mtime_ns": source.mtime_ns,
            "inode": source.inode,
            "last_complete_offset": parsed.last_complete_offset,
            "context": safe_context,
            **boundary,
        }
        try:
            after = source.path.stat()
            if after.st_size != source.frozen_size or after.st_mtime_ns != source.mtime_ns:
                counters["sources_changed_after_freeze"] += 1
                errors.append({"source_file_id": source.source_file_id, "error": "source_changed_after_freeze", "frozen_size": source.frozen_size, "current_size": after.st_size})
        except OSError:
            errors.append({"source_file_id": source.source_file_id, "error": "source_missing_after_read"})

    current_source_ids = {source.source_file_id for source in sources}
    if incremental:
        for previous_id, previous in previous_state.get("sources", {}).items():
            if previous_id in current_source_ids:
                continue
            source_state[previous_id] = previous
            counters["missing_previous_sources_preserved"] += 1
            errors.append(
                {
                    "source_file_id": previous_id,
                    "path": previous.get("path"),
                    "error": "previous_source_missing_events_preserved",
                }
            )

    combined = retained_previous + new_events
    self_sessions = _self_reconstruction_sessions(combined, output_dir)
    if self_sessions:
        filtered: list[UnifiedEvent] = []
        for event in combined:
            if (event.source_agent, event.session_id) not in self_sessions:
                filtered.append(event)
                continue
            excluded.append(
                {
                    "source_file_id": event.source_file_id,
                    "record_locator": event.record_locator,
                    "reason": "self_reconstruction_session",
                }
            )
            dispositions.append(
                _transport_disposition(
                    event.source_file_id,
                    event.record_locator,
                    "quarantined",
                    reason="self_reconstruction_session",
                )
            )
            counters["self_reconstruction_events_excluded"] += 1
        combined = filtered
    _assign_logical_sessions(combined)
    events, duplicate_rows = _deduplicate(combined)
    counters["duplicates_removed"] = len(duplicate_rows)
    retained_ids = {event.event_id for event in events}
    for event in events:
        transforms = [flag for flag in event.flags if flag in {"binary_removed", "private_data_redacted"}]
        dispositions.append(
            _transport_disposition(
                event.source_file_id,
                event.record_locator,
                "evidence_candidate",
                event_id=event.event_id,
                actor_kind=event.actor_kind,
                event_type=event.event_type,
                transforms=transforms,
            )
        )
    for duplicate, target, reason in duplicate_rows:
        dispositions.append(
            _transport_disposition(
                duplicate.source_file_id,
                duplicate.record_locator,
                "transport_duplicate",
                duplicate_event_id=duplicate.event_id,
                retained_event_id=target.event_id if target.event_id in retained_ids else None,
                reason=reason,
            )
        )
    counters["retained_events"] = len(events)
    counters.update(sanitizer.stats)
    counters["sensitive_redactions"] = _sensitive_count(sanitizer.stats)
    accounted_records = {
        (item["source_file_id"], item["canonical_record_locator"])
        for item in dispositions
        if item.get("canonical_record_locator") not in {"source", "document", "preamble", "unknown"}
    }
    counters["transport_records_accounted"] = len(accounted_records)
    counters["transport_records_unaccounted"] = max(0, counters["records_seen"] - len(accounted_records))

    def unique_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen_rows: set[str] = set()
        for row in rows:
            marker = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            if marker in seen_rows:
                continue
            seen_rows.add(marker)
            unique.append(row)
        return unique

    excluded = unique_dicts(excluded)
    errors = unique_dicts(errors)
    dispositions = unique_dicts(dispositions)
    parser_warning_types = {
        "incremental_fallback_full_reparse",
        "source_changed_after_freeze",
        "previous_source_missing_events_preserved",
        "boundary_hash_error",
        "source_missing_after_read",
    }
    counters["parse_errors"] = sum(str(item.get("error") or "") not in parser_warning_types for item in errors)

    adapter_counts = Counter(source.adapter for source in sources)
    previous_event_ids = {event.event_id for event in previous_events}
    current_event_ids = {event.event_id for event in events}
    added_event_ids = sorted(current_event_ids - previous_event_ids)
    removed_event_ids = sorted(previous_event_ids - current_event_ids)
    added_event_set = set(added_event_ids)
    removed_event_set = set(removed_event_ids)
    affected_projects = sorted(
        {
            event.project_key
            for event in events
            if event.event_id in added_event_set and event.project_key
        }
        | {
            event.project_key
            for event in previous_events
            if event.event_id in removed_event_set and event.project_key
        }
    )
    impact = {
        "impact_version": 1,
        "run_id": run_id,
        "incremental": incremental,
        "added_event_count": len(added_event_ids),
        "removed_event_count": len(removed_event_ids),
        "affected_project_keys": affected_projects,
        "added_event_ids": added_event_ids,
        "removed_event_ids": removed_event_ids,
        "review_required": bool(added_event_ids or removed_event_ids or not incremental),
    }
    stats = dict(sorted(counters.items()))
    stats.update(
        {
            "run_id": run_id,
            "incremental": incremental,
            "dry_run": dry_run,
            "adapter_counts": dict(sorted(adapter_counts.items())),
            "previous_events_loaded": len(previous_events),
            "new_events_parsed": len(new_events),
        }
    )
    knowledge_documents, _ = render_knowledge(events, stats, run_id)
    unsupported = discovery.unsupported if discovery else []
    coverage_gaps = discovery.coverage_gaps if discovery else []
    matrix = compatibility_markdown(dict(adapter_counts), verified_adapters=verified_adapters)
    state = {"state_version": 1, "run_id": run_id, "sources": source_state}
    completion = {
        "report_version": 1,
        "run_id": run_id,
        "status": "needs_semantic_review",
        "gates": {
            "frozen_snapshot": bool(snapshot_value.get("sources")),
            "transport_accounted": counters["transport_records_unaccounted"] == 0,
            "parse_clean": counters["parse_errors"] == 0,
            "discovery_coverage_complete": not coverage_gaps,
            "unsupported_formats_clear": not unsupported,
            "semantic_review_complete": False,
            "knowledge_graph_complete": False,
            "published_knowledge": False,
        },
        "notes": [
            "Rebuild completed the deterministic evidence layer only.",
            "Run review-init, read each project chain, then distill a matching reviewed file before treating knowledge as confirmed.",
        ],
    }

    result = {
        "run_id": run_id,
        "stats": stats,
        "unsupported": unsupported,
        "coverage_gaps": coverage_gaps,
        "errors": errors,
        "excluded": excluded,
        "event_count": len(events),
        "completion": completion,
        "impact": impact,
        "would_write": [] if dry_run else ["knowledge", "audit"],
    }
    if dry_run:
        return result

    audit_dir = output_dir / "audit"
    knowledge_dir = output_dir / "knowledge"
    audit_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(audit_dir / "events.jsonl", [event.to_dict() for event in events])
    _write_jsonl(audit_dir / "dispositions.jsonl", dispositions)
    _write_jsonl(audit_dir / "excluded.jsonl", excluded)
    _write_jsonl(audit_dir / "errors.jsonl", errors)
    _write_json(audit_dir / "stats.json", stats)
    _write_json(audit_dir / "unsupported-formats.json", unsupported)
    _write_json(audit_dir / "coverage-gaps.json", coverage_gaps)
    _write_json(audit_dir / "state.json", state)
    _write_json(audit_dir / "snapshot.json", snapshot_value)
    _write_json(audit_dir / "completion-report.json", completion)
    _write_json(audit_dir / "impact-report.json", impact)
    _write_json(audit_dir / "snapshots" / f"{run_id}.json", snapshot_value)
    _atomic_write_text(audit_dir / "compatibility-matrix.md", matrix)
    if discovery:
        _write_json(audit_dir / "discovery.json", discovery.to_dict())
    for relative, content in knowledge_documents.items():
        _atomic_write_text(knowledge_dir / relative, content)
    return result
