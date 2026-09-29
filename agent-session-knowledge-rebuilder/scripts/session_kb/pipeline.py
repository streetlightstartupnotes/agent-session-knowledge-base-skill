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

from . import SCHEMA_VERSION
from .adapters import AdapterRegistry
from .discovery import DiscoveryResult, freeze_sources, validate_snapshot
from .model import ParseResult, SourceFile, UnifiedEvent, display_path, stable_hash
from .render import compatibility_markdown, render_knowledge
from .sanitize import SANITIZER_POLICY_VERSION, Sanitizer, actor_kind, classify_flags, evidence_grade
from .verification import publication_manifest, require_publication_prerequisites, require_verified_retrieval_report


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
) -> tuple[ParseResult, int, str]:
    """Parse append-only JSONL in bounded chunks while honoring the frozen byte end."""
    byte_count = source.frozen_size - start_offset
    if byte_count < 0:
        raise ValueError("source frozen size is smaller than the requested start offset")
    if not adapter.append_only:
        frozen_data = _read_exact(source.path, 0, source.frozen_size)
        data = frozen_data[start_offset:]
        parse_context = dict(context)
        parse_context["_start_offset"] = start_offset
        return adapter.parse(data, source, context=parse_context), len(data), sha256(frozen_data).hexdigest()

    total = ParseResult(context=dict(context), last_complete_offset=start_offset)
    frozen_digest = sha256()
    buffer = b""
    buffer_start = start_offset
    position = start_offset
    with source.path.open("rb") as handle:
        remaining_prefix = start_offset
        while remaining_prefix:
            prefix_chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining_prefix))
            if not prefix_chunk:
                raise OSError("source shorter than the verified incremental prefix")
            frozen_digest.update(prefix_chunk)
            remaining_prefix -= len(prefix_chunk)
        while position < source.frozen_size:
            wanted = min(STREAM_CHUNK_BYTES, source.frozen_size - position)
            chunk = handle.read(wanted)
            if len(chunk) != wanted:
                raise OSError(f"source shorter than frozen range: wanted {wanted}, got {len(chunk)}")
            frozen_digest.update(chunk)
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
    return total, byte_count, frozen_digest.hexdigest()


def _hash_range(path: Path, start: int, length: int) -> str:
    if length <= 0:
        return sha256(b"").hexdigest()
    return sha256(_read_exact(path, start, start + length)).hexdigest()


def _append_verified(path: Path, source: SourceFile, previous: dict[str, Any]) -> bool:
    old_size = int(previous.get("frozen_size", -1))
    if old_size < 0 or source.frozen_size < old_size:
        return False
    old_inode = int(previous.get("inode", 0))
    if old_inode and source.inode and old_inode != source.inode:
        return False
    try:
        expected = str(previous.get("frozen_sha256") or "")
        return bool(expected) and _hash_range(path, 0, old_size) == expected
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


def _event_semantic_fingerprint(event: UnifiedEvent) -> str:
    payload = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


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
    # Session metadata may have been exported from another machine. Resolving
    # that path on the current host can follow unrelated symlinks and produce a
    # misleading or partially redacted location. Sanitize the recorded value
    # directly; use its raw value only as an in-memory project-key hash input.
    return sanitizer.sanitize_text(raw)


def _project_identity(
    raw_project_id: str | None,
    raw_working_dir: str | None,
    display_working_dir: str | None,
    agent: str,
    session_id: str,
) -> tuple[str, str]:
    if raw_working_dir and raw_working_dir not in {"~", "/", ".", "unknown"}:
        canonical = re.sub(r"[\\/]+", "/", str(raw_working_dir).strip()).rstrip("/")
        if re.match(r"^[A-Za-z]:/", canonical):
            canonical = canonical[0].lower() + canonical[1:]
        display_value = display_working_dir or "private-project"
        label = re.split(r"[\\/]", display_value.rstrip("/\\"))[-1] or display_value
        # Hash the unsanitized identity without ever persisting it. Hashing the
        # redacted display path would merge different accounts such as two
        # exported /home/<account>/work trees into one project proposal.
        return "cwd:" + stable_hash(canonical, length=24), label
    if raw_project_id:
        return "project:" + stable_hash(agent, raw_project_id, length=24), str(raw_project_id)
    return "", f"Session {session_id[:12]}"


def _normalize_raw_event(
    raw: Any,
    source: SourceFile,
    sanitizer: Sanitizer,
    project_identities: dict[str, dict[str, str]] | None = None,
) -> tuple[UnifiedEvent | None, str | None]:
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
    identity_marker = stable_hash("source-session-project", source.source_file_id, raw.session_id, length=32)
    prior_identity = (project_identities or {}).get(identity_marker)
    if prior_identity:
        project_key = str(prior_identity.get("project_key") or "")
        project_label = str(prior_identity.get("project_label") or working_dir or f"Session {raw.session_id[:12]}")
    else:
        project_key, project_label = _project_identity(raw.project_id, raw.working_dir, working_dir, source.agent_name, raw.session_id)
        if project_identities is not None:
            project_identities[identity_marker] = {"project_key": project_key, "project_label": project_label}
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


def _mentions_self_reconstruction(content: str, output_dir: Path) -> bool:
    try:
        absolute = str(output_dir.expanduser().resolve())
    except (OSError, RuntimeError):
        absolute = str(output_dir.expanduser())
    markers = {absolute, display_path(output_dir)}
    workflow_markers = ("session_kb.py", "audit/events.jsonl", "completion-report.json", "review/review.json", "review-init", "validate-review")
    return any(marker and marker in content for marker in markers) and any(marker in content for marker in workflow_markers)


def _self_reconstruction_sessions(events: list[UnifiedEvent], output_dir: Path) -> set[tuple[str, str]]:
    sessions: set[tuple[str, str]] = set()
    for event in events:
        if _mentions_self_reconstruction(event.content, output_dir):
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
            "private_ip_redactions",
            "home_path_redactions",
        )
    )


def _lifecycle_forget_hashes(output_dir: Path) -> dict[str, set[str]]:
    result = {key: set() for key in ("sources", "events", "projects", "event_locators")}
    for row in _load_jsonl_dicts(output_dir / "audit" / "lifecycle-tombstones.jsonl"):
        if row.get("action") != "forget":
            continue
        values = row.get("affected_identifier_sha256")
        if not isinstance(values, dict):
            continue
        for key in result:
            result[key].update(str(item) for item in values.get(key) or [] if isinstance(item, str))
    return result


def _lifecycle_hash(value: object) -> str:
    return sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def _forgotten_by_lifecycle(event: UnifiedEvent, hashes: dict[str, set[str]]) -> bool:
    locator = _lifecycle_hash(f"{event.source_file_id}\0{event.record_locator}")
    return bool(
        _lifecycle_hash(event.source_file_id) in hashes["sources"]
        or _lifecycle_hash(event.event_id) in hashes["events"]
        or _lifecycle_hash(event.project_key or "") in hashes["projects"]
        or locator in hashes["event_locators"]
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
    if (output_dir.expanduser() / "audit" / "lifecycle-transaction.json").exists():
        raise ValueError("a lifecycle transaction is pending; resume it before rebuilding the knowledge base")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    if output_dir.exists() and any(output_dir.iterdir()) and not incremental and not dry_run:
        raise ValueError("output directory is not empty; use --incremental or choose a new output")
    snapshot_value = snapshot or freeze_sources(sources)
    validate_snapshot(snapshot_value, sources)
    snapshot_sources = {
        str(item.get("source_file_id")): item
        for item in snapshot_value.get("sources", [])
        if isinstance(item, dict) and item.get("source_file_id")
    }
    previous_state = _load_json(output_dir / "audit" / "state.json", {"sources": {}}) if incremental else {"sources": {}}
    previous_events = _load_events(output_dir / "audit" / "events.jsonl") if incremental else []
    previous_completion = _load_json(output_dir / "audit" / "completion-report.json", {}) if incremental else {}
    previous_index = _load_json(output_dir / "knowledge" / "knowledge-index.json", {}) if incremental else {}
    previous_unsupported = _load_json(output_dir / "audit" / "unsupported-formats.json", []) if incremental else []
    previous_coverage_gaps = _load_json(output_dir / "audit" / "coverage-gaps.json", []) if incremental else []
    schema_policy_changed = bool(
        incremental
        and previous_state.get("sources")
        and (
            previous_state.get("schema_version") != SCHEMA_VERSION
            or previous_state.get("sanitizer_policy_version") != SANITIZER_POLICY_VERSION
        )
    )
    source_state: dict[str, Any] = {}
    retained_previous = [] if schema_policy_changed else list(previous_events)
    new_events: list[UnifiedEvent] = []
    raw_self_reconstruction_sessions: set[tuple[str, str]] = set()
    excluded: list[dict[str, Any]] = _load_jsonl_dicts(output_dir / "audit" / "excluded.jsonl") if incremental and not schema_policy_changed else []
    errors: list[dict[str, Any]] = _load_jsonl_dicts(output_dir / "audit" / "errors.jsonl") if incremental and not schema_policy_changed else []
    dispositions: list[dict[str, Any]] = _load_jsonl_dicts(output_dir / "audit" / "dispositions.jsonl") if incremental and not schema_policy_changed else []
    counters: Counter[str] = Counter()
    sanitizer = Sanitizer()
    if schema_policy_changed:
        counters["schema_or_sanitizer_policy_reparse"] = 1

    for source in sources:
        counters["source_files"] += 1
        previous = None if schema_policy_changed else previous_state.get("sources", {}).get(source.source_file_id)
        prior_source_events = [] if schema_policy_changed else [event for event in retained_previous if event.source_file_id == source.source_file_id]
        prior_source_excluded = [] if schema_policy_changed else [item for item in excluded if item.get("source_file_id") == source.source_file_id]
        prior_source_errors = [] if schema_policy_changed else [item for item in errors if item.get("source_file_id") == source.source_file_id]
        prior_source_dispositions = [] if schema_policy_changed else [item for item in dispositions if item.get("source_file_id") == source.source_file_id]
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
                # Keep the last verified boundary and parser context. Without this,
                # the following incremental run cannot identify the retained events
                # as belonging to a source that must be replaced by a full reparse.
                if previous:
                    source_state[source.source_file_id] = previous
                    counters["stale_source_states_preserved"] += 1
                continue
        adapter = registry.get(source.adapter)
        start_offset = 0
        full_reparse = True
        if incremental and previous:
            if adapter.append_only and _append_verified(source.path, source, previous):
                start_offset = int(previous.get("last_complete_offset", 0))
                full_reparse = False
                counters["append_files_verified"] += 1
            elif not adapter.append_only and source.frozen_size == int(previous.get("frozen_size", -1)) and _append_verified(source.path, source, previous):
                source_state[source.source_file_id] = previous
                counters["unchanged_files_skipped"] += 1
                continue
            else:
                counters["reparsed_files"] += 1
        if incremental and full_reparse:
            # Clean by source id even when an older failed run lost its source
            # state. This prevents retained old events from being combined with a
            # successful recovery parse on the next run.
            had_retained_source = any(event.source_file_id == source.source_file_id for event in retained_previous)
            retained_previous = [event for event in retained_previous if event.source_file_id != source.source_file_id]
            excluded = [item for item in excluded if item.get("source_file_id") != source.source_file_id]
            errors = [item for item in errors if item.get("source_file_id") != source.source_file_id]
            dispositions = [item for item in dispositions if item.get("source_file_id") != source.source_file_id]
            if previous or had_retained_source:
                errors.append(
                    {
                        "source_file_id": source.source_file_id,
                        "path": display_path(source.path),
                        "error": "incremental_fallback_full_reparse",
                    }
                )
        isolated_call_hashes = {
            str(value)
            for value in ((previous or {}).get("isolated_call_hashes") or [])
            if not full_reparse and isinstance(value, str)
        }
        project_identities = {
            str(key): {"project_key": str(item.get("project_key") or ""), "project_label": str(item.get("project_label") or "")}
            for key, item in ((previous or {}).get("project_identities") or {}).items()
            if not full_reparse and isinstance(item, dict)
        }
        try:
            context = dict(previous.get("context", {})) if previous and not full_reparse else {}
            parsed, parsed_bytes, parsed_frozen_digest = _parse_frozen_source(adapter, source, start_offset, context)
            integrity_bytes = (source.frozen_size - parsed_bytes) + (start_offset if start_offset else 0)
            counters["bytes_parsed"] += parsed_bytes
            counters["bytes_read"] += parsed_bytes + integrity_bytes
            counters["integrity_bytes_read"] += integrity_bytes
            if start_offset:
                counters["incremental_bytes_parsed"] += parsed_bytes
                counters["incremental_bytes_read"] += parsed_bytes + integrity_bytes
                counters["incremental_integrity_bytes_read"] += integrity_bytes
                counters["append_verification_bytes_read"] += start_offset
                counters["parsed_prefix_integrity_bytes_read"] += start_offset
            elif incremental:
                counters["full_reparse_bytes_parsed"] += parsed_bytes
                counters["full_reparse_bytes_read"] += parsed_bytes
        except (OSError, ValueError) as exc:
            errors.append({"source_file_id": source.source_file_id, "path": display_path(source.path), "error": "source_read_error", "detail_type": type(exc).__name__})
            dispositions.append(_transport_disposition(source.source_file_id, "source", "parse_error", error="source_read_error"))
            counters["parse_errors"] += 1
            continue
        if parsed_frozen_digest != str(frozen_manifest.get("frozen_sha256") or ""):
            retained_previous = [event for event in retained_previous if event.source_file_id != source.source_file_id] + prior_source_events
            excluded = [item for item in excluded if item.get("source_file_id") != source.source_file_id] + prior_source_excluded
            errors = [item for item in errors if item.get("source_file_id") != source.source_file_id] + prior_source_errors
            dispositions = [item for item in dispositions if item.get("source_file_id") != source.source_file_id] + prior_source_dispositions
            errors.append({"source_file_id": source.source_file_id, "path": display_path(source.path), "error": "source_boundary_changed_during_read"})
            dispositions.append(_transport_disposition(source.source_file_id, "source", "parse_error", error="source_boundary_changed_during_read"))
            counters["parse_errors"] += 1
            if previous:
                source_state[source.source_file_id] = previous
                counters["stale_source_states_preserved"] += 1
            continue
        counters["parsed_files"] += 1
        counters["records_seen"] += parsed.records_seen
        parsed_accounted_locators = {
            _canonical_record_locator(str(item.record_locator)) for item in parsed.events
        } | {
            _canonical_record_locator(str(item.get("record_locator") or "unknown"))
            for item in (list(parsed.excluded) + list(parsed.errors))
            if str(item.get("record_locator") or "unknown") not in {"document", "preamble", "unknown"}
        }
        batch_transport_seen = parsed.records_seen
        batch_transport_accounted = min(parsed.records_seen, len(parsed_accounted_locators))
        batch_transport_unaccounted = max(0, parsed.records_seen - len(parsed_accounted_locators))
        previous_transport_seen = int((previous or {}).get("transport_records_seen", 0)) if not full_reparse else 0
        previous_transport_accounted = int((previous or {}).get("transport_records_accounted", 0)) if not full_reparse else 0
        previous_transport_unaccounted = int((previous or {}).get("transport_records_unaccounted", 0)) if not full_reparse else 0
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
            if _mentions_self_reconstruction(str(raw.content), output_dir):
                raw_self_reconstruction_sessions.add((source.agent_name, raw.session_id))
            event, exclusion_reason = _normalize_raw_event(raw, source, sanitizer, project_identities)
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
                    isolated_call_hashes.add(stable_hash("old-memory-call", source.source_file_id, event.call_id))
                excluded.append({"source_file_id": source.source_file_id, "record_locator": raw.record_locator, "reason": "old_memory_read"})
                dispositions.append(_transport_disposition(source.source_file_id, raw.record_locator, "quarantined", reason="old_memory_read"))
                counters["excluded_records"] += 1
                continue
            if (
                event.call_id
                and stable_hash("old-memory-call", source.source_file_id, event.call_id) in isolated_call_hashes
                and event.event_type == "tool_result"
            ):
                excluded.append({"source_file_id": source.source_file_id, "record_locator": raw.record_locator, "reason": "old_memory_read_result"})
                dispositions.append(_transport_disposition(source.source_file_id, raw.record_locator, "quarantined", reason="old_memory_read_result"))
                counters["excluded_records"] += 1
                continue
            new_events.append(event)
        boundary = {
            field: frozen_manifest.get(field)
            for field in ("head_length", "head_sha256", "tail_start", "tail_length", "tail_sha256", "frozen_sha256")
        }
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
            # Store only irreversible call-id hashes. This lets an append-only
            # tail quarantine a tool result whose old-memory call appeared in a
            # prior run without retaining the raw identifier.
            "isolated_call_hashes": sorted(isolated_call_hashes),
            "project_identities": project_identities,
            "transport_records_seen": previous_transport_seen + batch_transport_seen,
            "transport_records_accounted": previous_transport_accounted + batch_transport_accounted,
            "transport_records_unaccounted": previous_transport_unaccounted + batch_transport_unaccounted,
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

    counters["transport_records_seen"] = sum(int(item.get("transport_records_seen", 0)) for item in source_state.values())
    counters["transport_records_accounted"] = sum(int(item.get("transport_records_accounted", 0)) for item in source_state.values())
    counters["transport_records_unaccounted"] = sum(int(item.get("transport_records_unaccounted", 0)) for item in source_state.values())

    combined = retained_previous + new_events
    self_sessions = raw_self_reconstruction_sessions | _self_reconstruction_sessions(combined, output_dir)
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
    lifecycle_forget_hashes = _lifecycle_forget_hashes(output_dir) if incremental else {
        "sources": set(),
        "events": set(),
        "projects": set(),
        "event_locators": set(),
    }
    if any(lifecycle_forget_hashes.values()):
        retained_after_forget: list[UnifiedEvent] = []
        for event in events:
            if not _forgotten_by_lifecycle(event, lifecycle_forget_hashes):
                retained_after_forget.append(event)
                continue
            event_hash = _lifecycle_hash(event.event_id)
            excluded.append(
                {
                    "source_file_id": "[FORGOTTEN]",
                    "record_locator": "[FORGOTTEN]",
                    "reason": "lifecycle_forget_tombstone",
                    "event_id_sha256": event_hash,
                }
            )
            dispositions.append(
                {
                    "stage": "transport",
                    "source_file_id": "[FORGOTTEN]",
                    "record_locator": "[FORGOTTEN]",
                    "disposition": "quarantined",
                    "reason": "lifecycle_forget_tombstone",
                    "event_id_sha256": event_hash,
                }
            )
            counters["lifecycle_forgotten_events_excluded"] += 1
        events = retained_after_forget
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
    previous_by_id = {event.event_id: event for event in previous_events}
    current_by_id = {event.event_id: event for event in events}
    modified_event_ids = sorted(
        event_id
        for event_id in previous_event_ids & current_event_ids
        if _event_semantic_fingerprint(previous_by_id[event_id]) != _event_semantic_fingerprint(current_by_id[event_id])
    )
    added_event_set = set(added_event_ids)
    removed_event_set = set(removed_event_ids)
    modified_event_set = set(modified_event_ids)
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
        | {
            event.project_key
            for event in events + previous_events
            if event.event_id in modified_event_set and event.project_key
        }
    )
    if schema_policy_changed:
        affected_projects = sorted({event.project_key for event in events if event.project_key})
    impact = {
        "impact_version": 1,
        "run_id": run_id,
        "incremental": incremental,
        "added_event_count": len(added_event_ids),
        "removed_event_count": len(removed_event_ids),
        "modified_event_count": len(modified_event_ids),
        "affected_project_keys": affected_projects,
        "added_event_ids": added_event_ids,
        "removed_event_ids": removed_event_ids,
        "modified_event_ids": modified_event_ids,
        "review_required": bool(added_event_ids or removed_event_ids or modified_event_ids or schema_policy_changed or not incremental),
        "schema_or_sanitizer_policy_changed": schema_policy_changed,
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
    unsupported = discovery.unsupported if discovery else []
    coverage_gaps = discovery.coverage_gaps if discovery else []
    discovery_errors = discovery.errors if discovery else []
    boundary_fields = ("frozen_size", "frozen_sha256", "head_sha256", "head_length", "tail_sha256", "tail_start", "tail_length", "last_complete_offset")
    previous_source_signatures = {
        str(source_id): tuple(value.get(field) for field in boundary_fields)
        for source_id, value in (previous_state.get("sources") or {}).items()
        if isinstance(value, dict)
    }
    current_source_signatures = {
        str(source_id): tuple(value.get(field) for field in boundary_fields)
        for source_id, value in source_state.items()
        if isinstance(value, dict)
    }
    source_denominator_changed = bool(incremental and previous_source_signatures != current_source_signatures)
    impact["source_denominator_changed"] = source_denominator_changed
    if source_denominator_changed:
        impact["review_required"] = True
    prior_gates = previous_completion.get("gates") if isinstance(previous_completion.get("gates"), dict) else {}
    prior_retrieval_contract = int(previous_completion.get("retrieval_contract_version") or 1)
    previous_prerequisites_valid = False
    try:
        require_publication_prerequisites(previous_completion)
        previous_prerequisites_valid = True
    except ValueError:
        previous_prerequisites_valid = False
    previous_retrieval_audit_valid = prior_retrieval_contract < 2
    if prior_retrieval_contract >= 2:
        try:
            require_verified_retrieval_report(output_dir, previous_completion)
            previous_retrieval_audit_valid = True
        except (OSError, ValueError, json.JSONDecodeError):
            previous_retrieval_audit_valid = False
    prior_retrieval_verified = bool(
        prior_gates.get("retrieval_related_match") is True
        and prior_gates.get("retrieval_unrelated_no_match") is True
        and (
            prior_retrieval_contract < 2
            or (
                prior_gates.get("retrieval_related_suite") is True
                and prior_gates.get("retrieval_hard_negative_suite") is True
                and bool(previous_completion.get("retrieval_suite_sha256"))
                and previous_retrieval_audit_valid
            )
        )
    )
    previous_manifest_valid = False
    expected_manifest = str(previous_completion.get("publication_manifest_sha256") or "")
    if expected_manifest and previous_index.get("semantic_status") == "published":
        try:
            previous_manifest_valid = publication_manifest(output_dir, previous_index)["sha256"] == expected_manifest
        except (OSError, ValueError, json.JSONDecodeError):
            previous_manifest_valid = False
    preserve_published = bool(
        incremental
        and not added_event_ids
        and not removed_event_ids
        and not modified_event_ids
        and not schema_policy_changed
        and not source_denominator_changed
        and counters["parse_errors"] == 0
        and counters["missing_previous_sources_preserved"] == 0
        and not discovery_errors
        and previous_index.get("semantic_status") == "published"
        and previous_index.get("run_id") == previous_completion.get("run_id")
        and previous_completion.get("status") in {"complete", "complete_with_unsupported_formats"}
        and prior_gates.get("published_knowledge") is True
        and previous_prerequisites_valid
        and prior_retrieval_verified
        and previous_manifest_valid
        and previous_unsupported == unsupported
        and previous_coverage_gaps == coverage_gaps
    )
    impact["publication_preserved"] = preserve_published
    impact["previous_publication_manifest_valid"] = previous_manifest_valid
    impact["previous_retrieval_audit_valid"] = previous_retrieval_audit_valid
    impact["published_run_id"] = previous_completion.get("run_id") if preserve_published else None
    knowledge_documents: dict[str, str] = {}
    if not preserve_published:
        knowledge_documents, _ = render_knowledge(events, stats, run_id)
    matrix = compatibility_markdown(dict(adapter_counts), verified_adapters=verified_adapters)
    state = {
        "state_version": 2,
        "schema_version": SCHEMA_VERSION,
        "sanitizer_policy_version": SANITIZER_POLICY_VERSION,
        "run_id": run_id,
        "sources": source_state,
    }
    completion = previous_completion if preserve_published else {
        "report_version": 2,
        "retrieval_contract_version": 3,
        "run_id": run_id,
        "status": "needs_semantic_review",
        "gates": {
            "frozen_snapshot": bool(snapshot_value.get("sources")),
            "transport_accounted": counters["transport_records_unaccounted"] == 0,
            "parse_clean": counters["parse_errors"] == 0,
            "discovery_coverage_complete": not coverage_gaps and not discovery_errors,
            "unsupported_formats_clear": not unsupported,
            "semantic_review_complete": False,
            "knowledge_graph_complete": False,
            "published_knowledge": False,
            "retrieval_related_match": False,
            "retrieval_unrelated_no_match": False,
            "retrieval_related_suite": False,
            "retrieval_hard_negative_suite": False,
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
        "would_write": [] if dry_run else (["audit"] if preserve_published else ["knowledge", "audit"]),
        "publication_preserved": preserve_published,
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
    if not preserve_published:
        _write_json(audit_dir / "completion-report.json", completion)
    _write_json(audit_dir / "impact-report.json", impact)
    _write_json(audit_dir / "snapshots" / f"{run_id}.json", snapshot_value)
    _atomic_write_text(audit_dir / "compatibility-matrix.md", matrix)
    if discovery:
        _write_json(audit_dir / "discovery.json", discovery.to_dict())
    if not preserve_published:
        for relative, content in knowledge_documents.items():
            _atomic_write_text(knowledge_dir / relative, content)
    return result
