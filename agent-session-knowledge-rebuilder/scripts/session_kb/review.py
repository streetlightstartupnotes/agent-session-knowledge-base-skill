from __future__ import annotations

import json
import os
import posixpath
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .evolution_ops import behavior_evidence_order_errors
from .model import UnifiedEvent
from .render import _keywords, _slug
from .sanitize import Sanitizer


HISTORY_SECTIONS = (
    "objective",
    "changes_and_corrections",
    "actions_and_artifacts",
    "validation_and_observations",
    "failures_and_fallbacks",
    "delivery_and_state",
    "remaining_work",
)
CLAIM_TYPES = {"evidence_rule", "identity", "direction", "collaboration", "expression"}
CLAIM_STATUSES = {"confirmed", "disputed", "retracted", "stale"}
RULE_CLAIM_DERIVATIONS = {"direct-explicit-rule", "repeated-context", "feedback-promotion"}
HISTORY_ITEM_STATUSES = {"observed", "agent-reported", "inferred", "disputed", "stale", "retracted", "unverified"}
PROJECT_STATUSES = {"reviewed", "reviewed-no-knowledge"}
SEMANTIC_DISPOSITIONS = {"supports_claim", "project_context", "non_knowledge", "ambiguous", "quarantined"}
LINK_ANALYSIS_STATUSES = {"linked", "intentional-isolate", "not-published"}
RELATIONSHIP_STATUSES = {"confirmed", "uncertain", "rejected"}
RELATIONSHIP_DIRECTIONS = {"directed", "symmetric"}
RELATIONSHIP_BASES = {
    "explicit-user-intent",
    "continuation-lineage",
    "shared-artifact",
    "dependency",
    "correction",
    "contradiction",
    "observed-handoff",
}
ATTRIBUTION_SCOPES = {"project-user-lane", "event"}
ATTRIBUTION_ACTORS = {"primary_user", "unknown_user", "customer", "third_party", "test_actor", "orchestrator", "subagent"}
ATTRIBUTION_BASES = {
    "native-user-lane",
    "explicit-self-reference",
    "session-owner-confirmation",
    "cross-event-corroboration",
    "role-context-review",
}
FEEDBACK_KINDS = {"positive", "negative", "gap", "outcome"}
FEEDBACK_OBJECTS = {"fact", "judgment", "method", "structure", "expression", "risk", "format", "completion", "other"}
FEEDBACK_SCOPES = {"artifact", "project", "task-type", "global"}
FEEDBACK_STATUSES = {"observed", "disputed", "retracted"}
EVOLUTION_STATUSES = {"candidate", "approved", "validated", "rejected"}
EVOLUTION_VALIDATION_RESULTS = {"pending", "passed", "failed", "mixed"}
COMPLETION_LEVELS = {
    "unreviewed",
    "not-published",
    "unknown",
    "requested",
    "designed",
    "implemented",
    "artifact-created",
    "installed",
    "enabled",
    "invoked",
    "automated-tests-passed",
    "real-interaction-observed",
    "user-accepted",
    "submitted",
    "merged",
    "remotely-published",
    "publicly-reachable",
}
COMPLETION_STATUSES = {"observed", "agent-reported", "unverified", "disputed"}
STABLE_ID_RE = re.compile(r"rel-[A-Za-z0-9][A-Za-z0-9._:-]{0,91}")
CLAIM_ID_RE = re.compile(r"claim-[A-Za-z0-9][A-Za-z0-9._:-]{0,89}")
ATTRIBUTION_ID_RE = re.compile(r"actor-[A-Za-z0-9][A-Za-z0-9._:-]{0,89}")
FEEDBACK_ID_RE = re.compile(r"feedback-[A-Za-z0-9][A-Za-z0-9._:-]{0,86}")
EVOLUTION_ID_RE = re.compile(r"evolution-[A-Za-z0-9][A-Za-z0-9._:-]{0,85}")
MEMBERSHIP_ID_RE = re.compile(r"membership-[A-Za-z0-9][A-Za-z0-9._:-]{0,84}")
PROJECT_KEY_RE = re.compile(r"(?:project|session):[A-Za-z0-9][A-Za-z0-9._:-]{0,119}")
CHUNK_ID_RE = re.compile(r"chunk-[0-9a-f]{24}")
MEMBERSHIP_OPERATIONS = {"merge", "split", "reassign"}
MEMBERSHIP_EVIDENCE_BASES = {
    "explicit-user-intent",
    "continuation-lineage",
    "objective-boundary",
    "shared-artifact",
    "observed-handoff",
    "correction",
    "role-context-review",
}


def _atomic_text(path: Path, text: str) -> None:
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


def _json_write(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def _jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    _atomic_text(path, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows))


def _archive_stale_project_documents(root: Path, current_paths: set[str], run_id: str) -> list[dict[str, Any]]:
    prior_manifest_path = root / "audit" / "published-files.json"
    if not prior_manifest_path.is_file():
        return []
    prior = json.loads(prior_manifest_path.read_text(encoding="utf-8"))
    prior_paths = prior.get("project_paths") if isinstance(prior, dict) and isinstance(prior.get("project_paths"), list) else []
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(run_id or "next"))
    archived: list[dict[str, Any]] = []
    for value in sorted({str(item) for item in prior_paths} - current_paths):
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "projects":
            raise ValueError(f"unsafe prior generated project path: {relative}")
        knowledge_root = (root / "knowledge").resolve()
        candidate = knowledge_root / relative
        if candidate.is_symlink():
            raise ValueError(f"stale generated project path is a symlink: {relative}")
        source = candidate.resolve()
        try:
            source.relative_to(knowledge_root)
        except ValueError as exc:
            raise ValueError(f"prior generated project path escapes knowledge root: {relative}") from exc
        if not source.exists():
            archived.append({"from": relative.as_posix(), "to": None, "status": "already-missing"})
            continue
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"stale generated project path is not a regular file: {relative}")
        archive_root = knowledge_root / "archive"
        archive_run_root = archive_root / safe_run_id
        for parent in (archive_root, archive_run_root):
            if parent.is_symlink():
                raise ValueError(f"archive path contains a symlink: {parent.relative_to(knowledge_root)}")
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"archive path is not a directory: {parent.relative_to(knowledge_root)}")
        archive_run_root.mkdir(parents=True, exist_ok=True)
        resolved_archive_root = archive_run_root.resolve()
        try:
            resolved_archive_root.relative_to(knowledge_root)
        except ValueError as exc:
            raise ValueError("archive destination escapes knowledge root") from exc
        destination = resolved_archive_root / relative.name
        if destination.is_symlink():
            raise ValueError(f"archive destination is a symlink: {destination.name}")
        if destination.exists():
            base_stem = destination.stem + "-" + sha256(value.encode("utf-8")).hexdigest()[:10]
            counter = 1
            while destination.exists() or destination.is_symlink():
                destination = resolved_archive_root / f"{base_stem}-{counter}{relative.suffix}"
                counter += 1
        try:
            destination.resolve().relative_to(knowledge_root)
        except ValueError as exc:
            raise ValueError("archive destination escapes knowledge root") from exc
        os.replace(source, destination)
        archived.append(
            {
                "from": relative.as_posix(),
                "to": destination.relative_to(knowledge_root).as_posix(),
                "status": "archived",
                "reason": "not present in the current reviewed publication",
            }
        )
    return archived


def _kb_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if (root / "audit" / "events.jsonl").is_file():
        return root
    raise ValueError(f"audit/events.jsonl not found under {root}")


def _events(root: Path) -> list[UnifiedEvent]:
    result: list[UnifiedEvent] = []
    with (root / "audit" / "events.jsonl").open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                result.append(UnifiedEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid event record at line {line_number}: {exc}") from exc
    return result


def _hash_ids(event_ids: list[str]) -> str:
    digest = sha256()
    for event_id in event_ids:
        digest.update(event_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _hash_events(events: list[UnifiedEvent]) -> str:
    digest = sha256()
    for event in events:
        payload = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _project_groups(events: list[UnifiedEvent]) -> dict[str, list[UnifiedEvent]]:
    groups: dict[str, list[UnifiedEvent]] = defaultdict(list)
    for event in events:
        groups[event.project_key or f"session:{event.logical_session_id}"].append(event)
    for group in groups.values():
        group.sort(key=lambda event: (str(event.timestamp or ""), event.logical_session_id, event.sequence, event.event_id))
    return dict(groups)


def _project_manifest(groups: dict[str, list[UnifiedEvent]]) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        event_ids = [event.event_id for event in group]
        projects.append(
            {
                "project_key": key,
                "event_count": len(group),
                "event_ids_sha256": _hash_ids(event_ids),
                "event_set_sha256": _hash_events(group),
            }
        )
    return {
        "event_count": sum(len(group) for group in groups.values()),
        "project_count": len(projects),
        "projects": projects,
    }


def _assignment_partition_sha256(events: list[UnifiedEvent]) -> str:
    digest = sha256()
    for event in sorted(events, key=lambda item: item.event_id):
        digest.update(_event_project_key(event).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _derived_membership_operations(proposed: list[UnifiedEvent], effective: list[UnifiedEvent]) -> list[dict[str, Any]]:
    proposed_key = {event.event_id: _event_project_key(event) for event in proposed}
    effective_key = {event.event_id: _event_project_key(event) for event in effective}
    event_ids = sorted(proposed_key)
    operations: list[dict[str, Any]] = []
    by_target: dict[str, set[str]] = defaultdict(set)
    by_source: dict[str, set[str]] = defaultdict(set)
    for event_id in event_ids:
        by_target[effective_key[event_id]].add(proposed_key[event_id])
        by_source[proposed_key[event_id]].add(effective_key[event_id])
    for target, sources in sorted(by_target.items()):
        if len(sources) >= 2:
            operations.append(
                {
                    "operation": "merge",
                    "source_project_keys": sorted(sources),
                    "target_project_keys": [target],
                    "event_ids": [event_id for event_id in event_ids if effective_key[event_id] == target],
                }
            )
    for source, targets in sorted(by_source.items()):
        if len(targets) >= 2:
            operations.append(
                {
                    "operation": "split",
                    "source_project_keys": [source],
                    "target_project_keys": sorted(targets),
                    "event_ids": [event_id for event_id in event_ids if proposed_key[event_id] == source],
                }
            )
    reassign_pairs = sorted(
        {
            (proposed_key[event_id], effective_key[event_id])
            for event_id in event_ids
            if proposed_key[event_id] != effective_key[event_id]
        }
    )
    for source, target in reassign_pairs:
        operations.append(
            {
                "operation": "reassign",
                "source_project_keys": [source],
                "target_project_keys": [target],
                "event_ids": [
                    event_id
                    for event_id in event_ids
                    if proposed_key[event_id] == source and effective_key[event_id] == target
                ],
            }
        )
    return operations


def _membership_plan(
    events: list[UnifiedEvent],
    membership_overrides: dict[str, Any] | None,
    *,
    require_audit: bool = False,
) -> tuple[list[UnifiedEvent], dict[str, Any]]:
    """Apply an evidence-bound review-layer project partition without changing audit/events.jsonl.

    The supplied plan assigns only explicitly listed events. Unlisted events keep
    their deterministic proposal. The returned audit object is canonical and
    records both partitions plus the exact per-event diff.
    """
    supplied = membership_overrides if membership_overrides is not None else {}
    if not isinstance(supplied, dict):
        raise ValueError("project_membership must be an object")
    override_version = supplied.get("override_version", 1)
    if override_version != 1:
        raise ValueError("project_membership.override_version must be 1")
    raw_hash = _hash_events(events)
    supplied_hash = str(supplied.get("basis_event_set_sha256") or "")
    assignments = supplied.get("assignments", [])
    if not isinstance(assignments, list):
        raise ValueError("project_membership.assignments must be a list")
    if assignments and supplied_hash != raw_hash:
        raise ValueError("project_membership basis_event_set_sha256 is stale or missing")
    if supplied_hash and supplied_hash != raw_hash:
        raise ValueError("project_membership basis_event_set_sha256 is stale")

    proposed_groups = _project_groups(events)
    proposed_by_id = {event.event_id: event for event in events}
    if len(proposed_by_id) != len(events):
        raise ValueError("project membership cannot operate on duplicate event ids")
    effective_key_by_id = {event.event_id: _event_project_key(event) for event in events}
    seen_override_ids: set[str] = set()
    assigned_event_ids: set[str] = set()
    normalized_assignments: list[dict[str, Any]] = []

    for assignment_index, assignment in enumerate(assignments):
        location = f"project_membership.assignments[{assignment_index}]"
        if not isinstance(assignment, dict):
            raise ValueError(f"{location} must be an object")
        override_id = str(assignment.get("override_id") or "")
        if not MEMBERSHIP_ID_RE.fullmatch(override_id) or override_id in seen_override_ids:
            raise ValueError(f"{location}.override_id must be a unique membership-* stable id")
        seen_override_ids.add(override_id)
        target_key = str(assignment.get("target_project_key") or "")
        if not PROJECT_KEY_RE.fullmatch(target_key):
            raise ValueError(f"{location}.target_project_key must be a stable project:* or session:* key")
        rationale = str(assignment.get("rationale") or "").strip()
        if not rationale:
            raise ValueError(f"{location}.rationale is required")
        operation = str(assignment.get("operation") or "")
        if operation not in MEMBERSHIP_OPERATIONS:
            raise ValueError(f"{location}.operation must be merge, split, or reassign")
        evidence_basis = assignment.get("evidence_basis")
        if (
            not isinstance(evidence_basis, list)
            or not evidence_basis
            or any(str(item) not in MEMBERSHIP_EVIDENCE_BASES for item in evidence_basis)
        ):
            raise ValueError(f"{location}.evidence_basis is missing or unsupported")
        sources = assignment.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"{location}.sources must be a non-empty list")
        normalized_sources: list[dict[str, Any]] = []
        assignment_event_ids: list[str] = []
        source_keys: set[str] = set()
        has_partial_source = False
        for source_index, source in enumerate(sources):
            source_location = f"{location}.sources[{source_index}]"
            if not isinstance(source, dict):
                raise ValueError(f"{source_location} must be an object")
            proposed_key = str(source.get("proposed_project_key") or "")
            proposed_group = proposed_groups.get(proposed_key)
            if proposed_group is None:
                raise ValueError(f"{source_location}.proposed_project_key is unknown")
            if source.get("proposed_project_event_set_sha256") != _hash_events(proposed_group):
                raise ValueError(f"{source_location}.proposed_project_event_set_sha256 is stale")
            event_ids = source.get("event_ids")
            if not isinstance(event_ids, list) or not event_ids or any(not isinstance(item, str) or not item for item in event_ids):
                raise ValueError(f"{source_location}.event_ids must be a non-empty string list")
            if len(set(event_ids)) != len(event_ids):
                raise ValueError(f"{source_location}.event_ids contains duplicates")
            source_order = {event.event_id: index for index, event in enumerate(proposed_group)}
            if any(event_id not in source_order for event_id in event_ids):
                raise ValueError(f"{source_location}.event_ids contains an event outside the proposed project")
            if event_ids != sorted(event_ids, key=source_order.__getitem__):
                raise ValueError(f"{source_location}.event_ids must preserve proposed project order")
            selected = [proposed_by_id[event_id] for event_id in event_ids]
            source_keys.add(proposed_key)
            assignment_event_ids.extend(event_ids)
            has_partial_source = has_partial_source or len(event_ids) < len(proposed_group)
            if source.get("event_ids_sha256") != _hash_ids(event_ids):
                raise ValueError(f"{source_location}.event_ids_sha256 is stale")
            if source.get("event_set_sha256") != _hash_events(selected):
                raise ValueError(f"{source_location}.event_set_sha256 is stale")
            duplicates = assigned_event_ids.intersection(event_ids)
            if duplicates:
                raise ValueError(f"{source_location} reassigns an event already assigned by another override")
            assigned_event_ids.update(event_ids)
            for event_id in event_ids:
                effective_key_by_id[event_id] = target_key
            normalized_sources.append(
                {
                    "proposed_project_key": proposed_key,
                    "proposed_project_event_set_sha256": _hash_events(proposed_group),
                    "event_ids": list(event_ids),
                    "event_ids_sha256": _hash_ids(event_ids),
                    "event_set_sha256": _hash_events(selected),
                }
            )
        if operation == "merge" and len(source_keys) < 2:
            raise ValueError(f"{location}.operation=merge needs at least two proposed source projects")
        if operation == "split" and not has_partial_source:
            raise ValueError(f"{location}.operation=split needs a strict subset of a proposed project")
        if operation == "reassign" and (len(source_keys) != 1 or target_key in source_keys):
            raise ValueError(f"{location}.operation=reassign needs one differently named proposed source project")
        evidence_event_ids = assignment.get("evidence_event_ids")
        if (
            not isinstance(evidence_event_ids, list)
            or not evidence_event_ids
            or len(set(str(item) for item in evidence_event_ids)) != len(evidence_event_ids)
            or any(str(item) not in set(assignment_event_ids) for item in evidence_event_ids)
        ):
            raise ValueError(f"{location}.evidence_event_ids must be unique events inside this exact assignment")
        evidence_events = [proposed_by_id[str(event_id)] for event_id in evidence_event_ids]
        if assignment.get("evidence_event_set_sha256") != _hash_events(evidence_events):
            raise ValueError(f"{location}.evidence_event_set_sha256 is stale")
        if not any(event.role == "user" or event.evidence_grade in {"A", "B"} for event in evidence_events):
            raise ValueError(f"{location}.evidence_event_ids need user-intent or observable grade-A/B evidence")
        normalized_assignments.append(
            {
                "override_id": override_id,
                "operation": operation,
                "target_project_key": target_key,
                "rationale": rationale,
                "evidence_basis": [str(item) for item in evidence_basis],
                "evidence_event_ids": [str(item) for item in evidence_event_ids],
                "evidence_event_set_sha256": _hash_events(evidence_events),
                "sources": normalized_sources,
            }
        )

    effective_events = [UnifiedEvent.from_dict(event.to_dict()) for event in events]
    for event in effective_events:
        event.project_key = effective_key_by_id[event.event_id]
    effective_groups = _project_groups(effective_events)
    effective_ids = [event.event_id for group in effective_groups.values() for event in group]
    if len(effective_ids) != len(events) or set(effective_ids) != set(proposed_by_id):
        raise ValueError("project membership override lost or duplicated events")
    diff = [
        {
            "event_id": event.event_id,
            "event_set_sha256": _hash_events([event]),
            "from_project_key": _event_project_key(event),
            "to_project_key": effective_key_by_id[event.event_id],
        }
        for event in events
        if _event_project_key(event) != effective_key_by_id[event.event_id]
    ]
    canonical = {
        "override_version": 1,
        "basis_event_set_sha256": raw_hash,
        "proposal_assignment_sha256": _assignment_partition_sha256(events),
        "result_assignment_sha256": _assignment_partition_sha256(effective_events),
        "assignments": normalized_assignments,
        "proposal": _project_manifest(proposed_groups),
        "result": _project_manifest(effective_groups),
        "diff": diff,
        "operations": _derived_membership_operations(events, effective_events),
    }
    if require_audit and supplied != canonical:
        raise ValueError("project_membership audit/diff is malformed or stale; regenerate it with review-init")
    return effective_events, canonical


def _rebase_prior_membership(events: list[UnifiedEvent], prior_membership: Any) -> dict[str, Any] | None:
    """Rebind an unchanged explicit assignment to a new run without guessing about new events."""
    if not isinstance(prior_membership, dict) or not prior_membership.get("assignments"):
        return None
    if prior_membership.get("override_version") != 1:
        raise ValueError("prior project_membership has an unsupported override_version")
    groups = _project_groups(events)
    event_by_id = {event.event_id: event for event in events}
    rebased_assignments: list[dict[str, Any]] = []
    for assignment_index, assignment in enumerate(prior_membership.get("assignments") or []):
        if not isinstance(assignment, dict):
            raise ValueError(f"prior project_membership.assignments[{assignment_index}] is malformed")
        rebased = {
            key: _clone(assignment.get(key))
            for key in (
                "override_id",
                "operation",
                "target_project_key",
                "rationale",
                "evidence_basis",
                "evidence_event_ids",
            )
        }
        evidence_ids = [str(item) for item in assignment.get("evidence_event_ids") or []]
        if any(event_id not in event_by_id for event_id in evidence_ids):
            raise ValueError("prior project_membership evidence event is missing in the current frozen run")
        evidence_events = [event_by_id[event_id] for event_id in evidence_ids]
        if assignment.get("evidence_event_set_sha256") != _hash_events(evidence_events):
            raise ValueError("prior project_membership evidence changed; explicit regrouping review is required")
        rebased["evidence_event_set_sha256"] = _hash_events(evidence_events)
        rebased_sources: list[dict[str, Any]] = []
        for source_index, source in enumerate(assignment.get("sources") or []):
            if not isinstance(source, dict):
                raise ValueError(f"prior project_membership source {source_index} is malformed")
            proposed_key = str(source.get("proposed_project_key") or "")
            group = groups.get(proposed_key)
            event_ids = [str(item) for item in source.get("event_ids") or []]
            if group is None or not event_ids:
                raise ValueError("prior project_membership source project no longer exists")
            current_by_id = {event.event_id: event for event in group}
            if any(event_id not in current_by_id for event_id in event_ids):
                raise ValueError("prior project_membership assigned event moved or disappeared")
            selected = [current_by_id[event_id] for event_id in event_ids]
            if source.get("event_set_sha256") != _hash_events(selected):
                raise ValueError("prior project_membership assigned event changed; explicit regrouping review is required")
            rebased_sources.append(
                {
                    "proposed_project_key": proposed_key,
                    "proposed_project_event_set_sha256": _hash_events(group),
                    "event_ids": event_ids,
                    "event_ids_sha256": _hash_ids(event_ids),
                    "event_set_sha256": _hash_events(selected),
                }
            )
        rebased["sources"] = rebased_sources
        rebased_assignments.append(rebased)
    return {
        "override_version": 1,
        "basis_event_set_sha256": _hash_events(events),
        "assignments": rebased_assignments,
    }


def _range_receipt(
    project_key: str,
    group: list[UnifiedEvent],
    start_event: int,
    end_event: int,
    *,
    reading_mode: str = "fresh",
    semantic_chunk_id: str | None = None,
    carried_forward_from_run_id: str | None = None,
) -> dict[str, Any]:
    selected = group[start_event:end_event]
    selected_ids = [event.event_id for event in selected]
    boundary = group[max(0, start_event - 1) : min(len(group), end_event + 1)]
    receipt = {
        "receipt_version": 3,
        "project_key": project_key,
        "project_event_set_sha256": _hash_events(group),
        "start_event": start_event,
        "end_event_exclusive": end_event,
        "slice_event_ids_sha256": _hash_ids(selected_ids),
        "slice_event_set_sha256": _hash_events(selected),
        "boundary_event_set_sha256": _hash_events(boundary),
        "first_event_id": selected[0].event_id,
        "last_event_id": selected[-1].event_id,
        "previous_event_id": group[start_event - 1].event_id if start_event else None,
        "next_event_id": group[end_event].event_id if end_event < len(group) else None,
        "reading_mode": reading_mode,
    }
    if semantic_chunk_id:
        receipt["semantic_chunk_id"] = semantic_chunk_id
    if carried_forward_from_run_id:
        receipt["carried_forward_from_run_id"] = carried_forward_from_run_id
    return receipt


def semantic_note_sha256(note: dict[str, Any]) -> str:
    payload = {key: value for key, value in note.items() if key != "source_note_sha256"}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _semantic_chunk_id(project_key: str, start: int, end: int, selected_hash: str, boundary_hash: str) -> str:
    return "chunk-" + sha256(
        "\x00".join((project_key, str(start), str(end), selected_hash, boundary_hash)).encode("utf-8")
    ).hexdigest()[:24]


def _reopened_ranges(length: int, covered: list[tuple[int, int]]) -> list[dict[str, int]]:
    reopened: list[dict[str, int]] = []
    cursor = 0
    for start, end in sorted(covered):
        if start > cursor:
            reopened.append({"start_event": cursor, "end_event_exclusive": start})
        cursor = max(cursor, end)
    if cursor < length:
        reopened.append({"start_event": cursor, "end_event_exclusive": length})
    return reopened


def project_synthesis_sha256(project: dict[str, Any]) -> str:
    """Bind a post-cache full-project synthesis to every publishable project conclusion."""
    payload = {
        field: project.get(field)
        for field in (
            "project_key",
            "event_count",
            "event_ids_sha256",
            "event_set_sha256",
            "title",
            "aliases",
            "semantic_status",
            "default_disposition",
            "rationale",
            "event_exceptions",
            "reading_receipts",
            "semantic_chunks",
            "link_analysis",
            "completion",
            "history",
        )
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


def cross_project_state_sha256(review: dict[str, Any]) -> str:
    payload = {
        "project_membership": review.get("project_membership"),
        "projects": review.get("projects"),
        "actor_attributions": review.get("actor_attributions"),
        "base_claims": review.get("base_claims"),
        "feedback_signals": review.get("feedback_signals"),
        "rule_evolutions": review.get("rule_evolutions"),
        "project_relationships": review.get("project_relationships"),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _event_project_key(event: UnifiedEvent) -> str:
    return event.project_key or f"session:{event.logical_session_id}"


def _seed_review_from_prior(template: dict[str, Any], prior_path: Path, events: list[UnifiedEvent]) -> dict[str, Any]:
    prior = json.loads(prior_path.expanduser().read_text(encoding="utf-8"))
    if prior.get("review_version") not in {2, 3, 4}:
        raise ValueError("prior review has an unsupported review_version")
    prior_reviewer = prior.get("reviewer") if isinstance(prior.get("reviewer"), dict) else {}
    if not str(prior_reviewer.get("attestation") or "").strip():
        raise ValueError("prior review has no reviewer attestation and cannot be carried forward")

    current_projects = {str(item["project_key"]): item for item in template["projects"]}
    prior_projects = {
        str(item.get("project_key")): item
        for item in prior.get("projects") or []
        if isinstance(item, dict) and item.get("project_key")
    }
    current_groups = _project_groups(events)
    carried_keys: set[str] = set()
    partially_reused_keys: set[str] = set()
    carried_chunk_count = 0
    for key, current in current_projects.items():
        previous = prior_projects.get(key)
        if not previous or previous.get("semantic_status") not in PROJECT_STATUSES:
            continue
        if prior.get("review_version") != 4:
            # v2/v3 remains directly validatable, but its id-only packet
            # receipts cannot be silently upgraded to v4 semantic receipts.
            continue
        if not isinstance(previous.get("completion"), dict):
            # Review v3 introduced a machine-checked completion ladder. A
            # legacy project can be consulted, but must be reopened once to
            # establish that boundary instead of being silently upgraded.
            continue
        if previous.get("event_count") != current.get("event_count") or previous.get("event_set_sha256") != current.get("event_set_sha256"):
            continue
        for field in (
            "title",
            "aliases",
            "semantic_status",
            "default_disposition",
            "rationale",
            "event_exceptions",
            "reading_receipts",
            "semantic_chunks",
            "project_synthesis",
            "link_analysis",
            "completion",
            "history",
        ):
            if field in previous:
                current[field] = _clone(previous[field])
        current["carried_forward_from_run_id"] = prior.get("run_id")
        carried_keys.add(key)

    if prior.get("review_version") == 4:
        prior_run_id = str(prior.get("run_id") or "")
        for key, current in current_projects.items():
            if key in carried_keys:
                continue
            previous = prior_projects.get(key)
            group = current_groups.get(key) or []
            if not previous or previous.get("semantic_status") not in PROJECT_STATUSES or not group:
                continue
            reusable: list[dict[str, Any]] = []
            occupied: list[tuple[int, int]] = []
            for chunk in sorted(
                (item for item in previous.get("semantic_chunks") or [] if isinstance(item, dict)),
                key=lambda item: (int(item.get("start_event", -1)), int(item.get("end_event_exclusive", -1))),
            ):
                start = chunk.get("start_event")
                end = chunk.get("end_event_exclusive")
                chunk_id = str(chunk.get("chunk_id") or "")
                if (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                    or end > len(group)
                    or not CHUNK_ID_RE.fullmatch(chunk_id)
                    or any(start < old_end and end > old_start for old_start, old_end in occupied)
                ):
                    continue
                selected = group[start:end]
                selected_ids = [event.event_id for event in selected]
                boundary_hash = _hash_events(group[max(0, start - 1) : min(len(group), end + 1)])
                notes = chunk.get("notes")
                if (
                    chunk.get("event_ids_sha256") != _hash_ids(selected_ids)
                    or chunk.get("event_set_sha256") != _hash_events(selected)
                    or chunk.get("boundary_event_set_sha256") != boundary_hash
                    or chunk_id != _semantic_chunk_id(key, start, end, _hash_events(selected), boundary_hash)
                    or chunk.get("first_event_id") != selected[0].event_id
                    or chunk.get("last_event_id") != selected[-1].event_id
                    or chunk.get("previous_event_id") != (group[start - 1].event_id if start else None)
                    or chunk.get("next_event_id") != (group[end].event_id if end < len(group) else None)
                    or not isinstance(notes, list)
                    or not notes
                    or not str(chunk.get("reviewed_at") or "").strip()
                    or not str(chunk.get("attestation") or "").strip()
                ):
                    continue
                selected_id_set = set(selected_ids)
                valid_notes = True
                for note in notes:
                    evidence_ids = note.get("evidence_event_ids") if isinstance(note, dict) else None
                    if (
                        not isinstance(note, dict)
                        or not str(note.get("text") or "").strip()
                        or note.get("status") not in HISTORY_ITEM_STATUSES
                        or not isinstance(evidence_ids, list)
                        or not evidence_ids
                        or any(str(event_id) not in selected_id_set for event_id in evidence_ids)
                        or note.get("source_note_sha256") != semantic_note_sha256(note)
                    ):
                        valid_notes = False
                        break
                if not valid_notes:
                    continue
                carried = _clone(chunk)
                carried["reading_mode"] = "carried-semantic-chunk"
                carried["carried_forward_from_run_id"] = prior_run_id
                reusable.append(carried)
                occupied.append((start, end))
            if not reusable:
                continue
            current["semantic_chunks"] = reusable
            current["reading_receipts"] = [
                _range_receipt(
                    key,
                    group,
                    int(chunk["start_event"]),
                    int(chunk["end_event_exclusive"]),
                    reading_mode="carried-semantic-chunk",
                    semantic_chunk_id=str(chunk["chunk_id"]),
                    carried_forward_from_run_id=prior_run_id,
                )
                for chunk in reusable
            ]
            current["project_synthesis"] = {
                "status": "pending",
                "mode": "fresh",
                "project_event_set_sha256": _hash_events(group),
                "synthesis_sha256": "",
                "reviewed_at": "",
                "attestation": "",
                "rationale": "",
                "reused_chunk_ids": [str(chunk["chunk_id"]) for chunk in reusable],
                "reopened_ranges": _reopened_ranges(len(group), occupied),
            }
            partially_reused_keys.add(key)
            carried_chunk_count += len(reusable)

    event_by_id = {event.event_id: event for event in events}

    def evidence_is_carried(item: dict[str, Any], *fields: str) -> bool:
        ids = [str(event_id) for field in fields for event_id in item.get(field) or []]
        return bool(ids) and all(event_id in event_by_id and _event_project_key(event_by_id[event_id]) in carried_keys for event_id in ids)

    actor_attributions: list[dict[str, Any]] = []
    for attribution in prior.get("actor_attributions") or []:
        if not isinstance(attribution, dict):
            continue
        scope = attribution.get("scope")
        project_key = str(attribution.get("project_key") or "")
        event_id = str(attribution.get("event_id") or "")
        evidence_ids = [str(item) for item in attribution.get("evidence_event_ids") or []]
        if project_key not in carried_keys or not evidence_ids or any(item not in event_by_id for item in evidence_ids):
            continue
        if scope == "event" and event_id not in event_by_id:
            continue
        actor_attributions.append(_clone(attribution))
    template["actor_attributions"] = actor_attributions

    claims = [
        _clone(claim)
        for claim in prior.get("base_claims") or []
        if isinstance(claim, dict) and evidence_is_carried(claim, "evidence_event_ids")
    ]
    template["base_claims"] = claims
    carried_claim_ids = {str(claim.get("claim_id")) for claim in claims}

    feedback_signals = [
        _clone(signal)
        for signal in prior.get("feedback_signals") or []
        if isinstance(signal, dict)
        and str(signal.get("project_key") or "") in carried_keys
        and evidence_is_carried(signal, "evidence_event_ids")
    ]
    template["feedback_signals"] = feedback_signals
    carried_feedback_ids = {str(signal.get("feedback_id")) for signal in feedback_signals}

    relationships: list[dict[str, Any]] = []
    for relationship in prior.get("project_relationships") or []:
        if not isinstance(relationship, dict):
            continue
        if str(relationship.get("source_project_key") or "") not in carried_keys or str(relationship.get("target_project_key") or "") not in carried_keys:
            continue
        if evidence_is_carried(relationship, "source_evidence_event_ids", "target_evidence_event_ids"):
            relationships.append(_clone(relationship))
    template["project_relationships"] = relationships

    evolutions: list[dict[str, Any]] = []
    for evolution in prior.get("rule_evolutions") or []:
        if not isinstance(evolution, dict):
            continue
        feedback_ids = {str(item) for item in evolution.get("feedback_ids") or []}
        promoted_claim_id = str(evolution.get("promoted_claim_id") or "")
        if not feedback_ids or not feedback_ids.issubset(carried_feedback_ids):
            continue
        if promoted_claim_id and promoted_claim_id not in carried_claim_ids:
            continue
        evidence_ids = [
            str(item)
            for field in ("approval_event_ids", "baseline_event_ids", "validation_event_ids")
            for item in evolution.get(field) or []
        ]
        if any(item not in event_by_id or _event_project_key(event_by_id[item]) not in carried_keys for item in evidence_ids):
            continue
        evolutions.append(_clone(evolution))
    template["rule_evolutions"] = evolutions
    template["carry_forward"] = {
        "source_run_id": prior.get("run_id"),
        "project_count": len(carried_keys),
        "claim_count": len(claims),
        "relationship_count": len(relationships),
        "feedback_signal_count": len(feedback_signals),
        "rule_evolution_count": len(evolutions),
        "chunk_count": carried_chunk_count,
        "partially_reused_project_keys": sorted(partially_reused_keys),
        "requires_cross_project_recheck": bool(carried_keys or partially_reused_keys),
        "invalidated_project_keys": sorted(set(current_projects) - carried_keys),
    }
    return template["carry_forward"]


def create_review_template(
    kb: Path,
    destination: Path | None = None,
    prior_review: Path | None = None,
    membership_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _kb_root(kb)
    completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    raw_events = _events(root)
    if membership_overrides is None and prior_review is not None:
        prior_value = json.loads(prior_review.expanduser().read_text(encoding="utf-8"))
        membership_overrides = _rebase_prior_membership(raw_events, prior_value.get("project_membership"))
    events, project_membership = _membership_plan(raw_events, membership_overrides)
    groups = _project_groups(events)
    projects: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        event_ids = [event.event_id for event in group]
        label = next((event.project_label for event in group if event.project_label), None) or key
        projects.append(
            {
                "project_key": key,
                "title": label,
                "aliases": [],
                "event_count": len(group),
                "event_ids_sha256": _hash_ids(event_ids),
                "event_set_sha256": _hash_events(group),
                "semantic_status": "unreviewed",
                "default_disposition": "project_context",
                "rationale": "",
                "event_exceptions": [],
                "reading_receipts": [],
                "semantic_chunks": [],
                "project_synthesis": {
                    "status": "not-required",
                    "mode": "fresh",
                    "project_event_set_sha256": _hash_events(group),
                    "synthesis_sha256": "",
                    "reviewed_at": "",
                    "attestation": "",
                    "rationale": "",
                    "reused_chunk_ids": [],
                    "reopened_ranges": [],
                },
                "link_analysis": {"status": "unreviewed", "rationale": ""},
                "completion": {"level": "unreviewed", "status": "unverified", "evidence_event_ids": [], "rationale": ""},
                "history": {section: [] for section in HISTORY_SECTIONS},
            }
        )
    template = {
        "review_version": 4,
        "run_id": completion.get("run_id"),
        "event_count": len(raw_events),
        "event_set_sha256": _hash_events(raw_events),
        "project_membership": project_membership,
        "reviewer": {
            "id": "",
            "reviewed_at": "",
            "attestation": "",
        },
        "unsupported_formats_acknowledged": False,
        "actor_attributions": [],
        "base_claims": [],
        "feedback_signals": [],
        "rule_evolutions": [],
        "project_relationships": [],
        "projects": projects,
        "carry_forward": {
            "source_run_id": None,
            "project_count": 0,
            "claim_count": 0,
            "relationship_count": 0,
            "feedback_signal_count": 0,
            "rule_evolution_count": 0,
            "chunk_count": 0,
            "partially_reused_project_keys": [],
            "requires_cross_project_recheck": False,
            "invalidated_project_keys": sorted(groups),
        },
        "cross_project_recheck": {
            "required": bool(project_membership["diff"]),
            "completed": False,
            "reviewed_at": "",
            "rationale": "",
            "checked_state_sha256": "",
        },
        "instructions": {
            "semantic_status": "Set every project to reviewed or reviewed-no-knowledge after reading its complete ordered event chain.",
            "history_item": {"text": "A bounded assertion", "evidence_event_ids": ["evt-..."], "status": "observed"},
            "reading_receipt": "Copy the receipt object from every contiguous review-packet range. Receipts must cover 0..event_count without gaps or overlap.",
            "semantic_chunk": {
                "chunk_id": "Copy the stable chunk id from review-packet",
                "start_event": 0,
                "end_event_exclusive": 10,
                "event_ids_sha256": "hash from review-packet",
                "event_set_sha256": "full semantic hash from review-packet",
                "first_event_id": "evt-...",
                "last_event_id": "evt-...",
                "notes": [{"text": "Evidence-bound observation from this exact range", "evidence_event_ids": ["evt-..."], "status": "observed", "source_note_sha256": "semantic_note_sha256(note)"}],
                "reviewed_at": "timestamp",
                "attestation": "This exact range was read semantically",
            },
            "project_synthesis": "When any semantic chunk is carried, reread every reopened range, synthesize the complete current chain, and set status=completed with the current project hash, project_synthesis_sha256, date, attestation, and rationale.",
            "base_claim": {
                "claim_id": "claim-stable-id",
                "knowledge_type": "identity|direction|collaboration|expression|evidence_rule",
                "subject": "primary_user",
                "statement": "A bounded claim",
                "status": "confirmed|disputed|retracted|stale",
                "confidence": "high|medium|low",
                "evidence_event_ids": ["evt-..."],
                "observed_at": "date or range",
                "applies_to": "scope",
                "rule_scope": "artifact|project|task-type|global (required for confirmed collaboration/expression claims)",
                "derivation": "direct-explicit-rule|repeated-context|feedback-promotion (required for confirmed collaboration/expression claims)",
                "conflicts": [],
                "supersedes": [],
            },
            "actor_attribution": {
                "attribution_id": "actor-stable-id",
                "scope": "project-user-lane|event",
                "project_key": "project key",
                "event_id": "required only for event scope",
                "actor_kind": "primary_user|unknown_user|customer|third_party|test_actor|orchestrator|subagent",
                "basis": ["native-user-lane|explicit-self-reference|session-owner-confirmation|cross-event-corroboration|role-context-review"],
                "evidence_event_ids": ["evt-..."],
                "rationale": "Why this serialized speaker lane belongs to this actor",
            },
            "feedback_signal": {
                "feedback_id": "feedback-stable-id",
                "project_key": "project key",
                "kind": "positive|negative|gap|outcome",
                "object": "fact|judgment|method|structure|expression|risk|format|completion|other",
                "scope": "artifact|project|task-type|global",
                "statement": "What the feedback actually establishes",
                "status": "observed|disputed|retracted",
                "evidence_event_ids": ["evt-..."],
                "applies_to": "bounded target",
            },
            "completion": {
                "level": "requested|designed|implemented|artifact-created|installed|enabled|invoked|automated-tests-passed|real-interaction-observed|user-accepted|submitted|merged|remotely-published|publicly-reachable|unknown",
                "status": "observed|agent-reported|unverified|disputed",
                "evidence_event_ids": ["evt-..."],
                "rationale": "Why this is the highest evidence-supported level and what it does not prove",
            },
            "rule_evolution": {
                "evolution_id": "evolution-stable-id",
                "status": "candidate|approved|validated|rejected",
                "scope": "artifact|project|task-type|global",
                "rule_version": 1,
                "feedback_ids": ["feedback-..."],
                "before_rule": "prior rule or empty when none existed",
                "proposed_rule": "bounded proposed rule",
                "rationale": "Why this change follows from the feedback",
                "expected_behavior_change": "observable next-run difference",
                "promoted_claim_id": "required for approved or validated changes",
                "approval_event_ids": [],
                "explicit_global_approval": False,
                "baseline_event_ids": [],
                "validation_result": "pending|passed|failed|mixed",
                "validation_event_ids": [],
                "observed_behavior_change": "required only when validated",
            },
            "project_relationship": {
                "relationship_id": "rel-stable-id",
                "source_project_key": "project key",
                "target_project_key": "project key",
                "relation": "continued-as|caused|depends-on|shares-artifact|contradicts|other precise label",
                "evidence_basis": ["explicit-user-intent|continuation-lineage|shared-artifact|dependency|correction|contradiction|observed-handoff"],
                "direction": "directed|symmetric",
                "status": "confirmed|uncertain|rejected",
                "confidence": "high|medium|low",
                "source_evidence_event_ids": ["evt-..."],
                "target_evidence_event_ids": ["evt-..."],
                "rationale": "Why the original records and user intent support or fail to support this relation",
            },
            "link_analysis": "For every reviewed project set linked or intentional-isolate. Do not infer links from filenames, broad types, or keyword overlap alone.",
            "cross_project_recheck": "After carrying unchanged project reviews, compare every carried claim and relationship against affected/new chains; set completed=true with a date and rationale before publication.",
        },
    }
    carry_summary = template["carry_forward"]
    if prior_review is not None:
        carry_summary = _seed_review_from_prior(template, prior_review, events)
        if carry_summary.get("project_count") or carry_summary.get("chunk_count"):
            template["cross_project_recheck"]["required"] = True
    target = destination.expanduser() if destination else root / "review" / "review.json"
    if destination is None and target.exists():
        safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(template["run_id"] or "next"))
        target = root / "review" / f"review-{safe_run_id}.json"
    if target.exists():
        raise ValueError(f"review file already exists: {target}")
    _json_write(target, template)
    return {
        "review_file": str(target.resolve()),
        "projects": len(projects),
        "events": len(events),
        "run_id": template["run_id"],
        "project_membership": {
            "changed_events": len(project_membership["diff"]),
            "proposed_projects": project_membership["proposal"]["project_count"],
            "effective_projects": project_membership["result"]["project_count"],
        },
        "carried_forward": carry_summary,
    }


def create_review_packet(
    kb: Path,
    project_key: str,
    start_event: int = 0,
    max_events: int | None = None,
    membership_overrides: dict[str, Any] | None = None,
    review_path: Path | None = None,
) -> dict[str, Any]:
    """Return a complete chain or one hash-bound contiguous range for semantic reading."""
    root = _kb_root(kb)
    raw_events = _events(root)
    if membership_overrides is not None and review_path is not None:
        raise ValueError("supply membership_overrides or review_path, not both")
    require_audit = False
    if review_path is not None:
        review = json.loads(review_path.expanduser().read_text(encoding="utf-8"))
        membership_overrides = review.get("project_membership")
        require_audit = True
    events, project_membership = _membership_plan(raw_events, membership_overrides, require_audit=require_audit)
    groups = _project_groups(events)
    key = str(project_key).strip()
    if key not in groups:
        raise ValueError(f"unknown project_key: {key}")
    group = groups[key]
    if start_event < 0 or start_event >= len(group):
        raise ValueError("start_event must identify an event inside the project chain")
    if max_events is not None and max_events <= 0:
        raise ValueError("max_events must be positive when supplied")
    end_event = len(group) if max_events is None else min(len(group), start_event + max_events)
    selected = group[start_event:end_event]
    event_ids = [event.event_id for event in group]
    selected_ids = [event.event_id for event in selected]
    receipt = _range_receipt(key, group, start_event, end_event)
    boundary_hash = _hash_events(group[max(0, start_event - 1) : min(len(group), end_event + 1)])
    chunk_id = _semantic_chunk_id(key, start_event, end_event, _hash_events(selected), boundary_hash)
    proposed_key_by_id = {event.event_id: _event_project_key(event) for event in raw_events}
    return {
        "packet_version": 3,
        "project_key": key,
        "project_label": next((event.project_label for event in group if event.project_label), None) or key,
        "event_count": len(group),
        "event_ids_sha256": _hash_ids(event_ids),
        "event_set_sha256": _hash_events(group),
        "project_membership_basis_sha256": project_membership["basis_event_set_sha256"],
        "ordered_events": [
            {**event.to_dict(), "proposed_project_key": proposed_key_by_id[event.event_id]}
            for event in selected
        ],
        "receipt": receipt,
        "semantic_chunk": {
            "chunk_id": chunk_id,
            "start_event": start_event,
            "end_event_exclusive": end_event,
            "event_ids_sha256": _hash_ids(selected_ids),
            "event_set_sha256": _hash_events(selected),
            "boundary_event_set_sha256": boundary_hash,
            "first_event_id": selected[0].event_id,
            "last_event_id": selected[-1].event_id,
            "previous_event_id": group[start_event - 1].event_id if start_event else None,
            "next_event_id": group[end_event].event_id if end_event < len(group) else None,
            "notes": [],
            "reviewed_at": "",
            "attestation": "",
            "reading_mode": "fresh",
        },
        "range": {
            "start_event": start_event,
            "end_event_exclusive": end_event,
            "returned_event_count": len(selected),
            "slice_event_ids_sha256": _hash_ids(selected_ids),
            "slice_event_set_sha256": _hash_events(selected),
            "boundary_event_set_sha256": boundary_hash,
            "previous_event_id": group[start_event - 1].event_id if start_event else None,
            "first_event_id": selected[0].event_id if selected else None,
            "last_event_id": selected[-1].event_id if selected else None,
            "next_event_id": group[end_event].event_id if end_event < len(group) else None,
            "next_start_event": end_event if end_event < len(group) else None,
            "complete_project": start_event == 0 and end_event == len(group),
        },
        "review_focus": list(HISTORY_SECTIONS),
        "checkpoint_rule": (
            "Ranges must be contiguous and non-overlapping. Resume at next_start_event, verify previous/next boundary ids, "
            "record evidence-bound semantic_chunk notes, and attest only after cumulative returned_event_count equals event_count and both id and semantic hashes match."
        ),
        "relationship_rule": (
            "After this chain is reviewed, compare its objective, corrections, artifacts, dependencies, handoffs, and contradictions with other reviewed chains. "
            "Before confirming a relationship, reopen both packets and cite event ids from both sides. Filename, title, broad project type, or keyword overlap alone is insufficient."
        ),
    }


def _sensitive_review_text(value: Any) -> bool:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    # Review files legitimately contain many cryptographic event/project
    # identifiers. Neutralize those structural hashes before contact scanning
    # so an accidental digit run cannot be mistaken for a phone number.
    scan_text = re.sub(r"(?i)\b[0-9a-f]{24,}\b", "[HASH]", serialized)
    sanitizer = Sanitizer()
    sanitized = sanitizer.sanitize_text(scan_text)
    return scan_text != sanitized and bool(sanitizer.stats)


def _validate_history_item(
    item: Any,
    project_key: str,
    event_by_id: dict[str, UnifiedEvent],
    errors: list[str],
    location: str,
) -> None:
    if not isinstance(item, dict) or not str(item.get("text") or "").strip():
        errors.append(f"{location}: history item needs non-empty text")
        return
    if item.get("status") not in HISTORY_ITEM_STATUSES:
        errors.append(f"{location}: invalid history item status")
    evidence_ids = item.get("evidence_event_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        errors.append(f"{location}: history item needs evidence_event_ids")
        return
    evidence_events: list[UnifiedEvent] = []
    for event_id in evidence_ids:
        event = event_by_id.get(str(event_id))
        if event is None:
            errors.append(f"{location}: unknown evidence event {event_id}")
        elif (event.project_key or f"session:{event.logical_session_id}") != project_key:
            errors.append(f"{location}: evidence event {event_id} belongs to another project")
        else:
            evidence_events.append(event)
    if item.get("status") == "observed" and evidence_events and all(event.evidence_grade == "C" for event in evidence_events):
        errors.append(f"{location}: Agent-only grade-C evidence must be labeled agent-reported, not observed")


def _effective_actor(
    event: UnifiedEvent,
    project_lane_attribution: dict[str, str],
    event_attribution: dict[str, str],
) -> str:
    if event.event_id in event_attribution:
        return event_attribution[event.event_id]
    key = _event_project_key(event)
    if event.actor_kind in {"native_user", "unknown_user"} and key in project_lane_attribution:
        return project_lane_attribution[key]
    return event.actor_kind


def validate_review(
    kb: Path,
    review_path: Path,
    *,
    allow_pending_cross_project_recheck: bool = False,
) -> tuple[dict[str, Any], list[UnifiedEvent], list[str]]:
    root = _kb_root(kb)
    review = json.loads(review_path.expanduser().read_text(encoding="utf-8"))
    raw_events = _events(root)
    completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    review_version = review.get("review_version")
    if review_version not in {2, 3, 4}:
        errors.append("unsupported review_version")
    if review_version == 4:
        try:
            events, membership_audit = _membership_plan(raw_events, review.get("project_membership"), require_audit=True)
        except ValueError as exc:
            errors.append(str(exc))
            events = [UnifiedEvent.from_dict(event.to_dict()) for event in raw_events]
            membership_audit = {"diff": []}
    else:
        if review.get("project_membership"):
            errors.append("project_membership requires review_version 4")
        events = [UnifiedEvent.from_dict(event.to_dict()) for event in raw_events]
        membership_audit = {"diff": []}
    membership_changed = bool(membership_audit.get("diff"))
    event_by_id = {event.event_id: event for event in events}
    groups = _project_groups(events)
    if review.get("run_id") != completion.get("run_id"):
        errors.append("review run_id does not match the frozen evidence run")
    expected_event_hash = _hash_events(raw_events) if review_version in {3, 4} else _hash_ids([event.event_id for event in raw_events])
    if review.get("event_count") != len(raw_events) or review.get("event_set_sha256") != expected_event_hash:
        errors.append("review event set does not match audit/events.jsonl")
    for gate in ("frozen_snapshot", "transport_accounted", "parse_clean", "discovery_coverage_complete"):
        if not completion.get("gates", {}).get(gate):
            errors.append(f"deterministic completion gate failed: {gate}")
    if not completion.get("gates", {}).get("unsupported_formats_clear") and review.get("unsupported_formats_acknowledged") is not True:
        errors.append("unsupported formats exist; inspect audit/unsupported-formats.json and set unsupported_formats_acknowledged=true")
    reviewer = review.get("reviewer") if isinstance(review.get("reviewer"), dict) else {}
    for field in ("id", "reviewed_at", "attestation"):
        if not str(reviewer.get(field) or "").strip():
            errors.append(f"reviewer.{field} is required")
    carry_forward = review.get("carry_forward") if isinstance(review.get("carry_forward"), dict) else {}
    cross_project_recheck = review.get("cross_project_recheck") if isinstance(review.get("cross_project_recheck"), dict) else {}
    projects = review.get("projects") if isinstance(review.get("projects"), list) else []
    project_by_key: dict[str, dict[str, Any]] = {}
    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            errors.append(f"projects[{index}] is not an object")
            continue
        key = str(project.get("project_key") or "")
        if not key or key in project_by_key:
            errors.append(f"projects[{index}] has missing or duplicate project_key")
            continue
        project_by_key[key] = project
    if set(project_by_key) != set(groups):
        missing = sorted(set(groups) - set(project_by_key))
        extra = sorted(set(project_by_key) - set(groups))
        errors.append(f"project set mismatch; missing={missing} extra={extra}")
    carried_projects = {
        key: str(project.get("carried_forward_from_run_id") or "")
        for key, project in project_by_key.items()
        if str(project.get("carried_forward_from_run_id") or "").strip()
    }
    declared_carried_count = int(carry_forward.get("project_count") or 0)
    if declared_carried_count != len(carried_projects):
        errors.append("carry_forward.project_count does not match actual carried project markers")
    partial_chunks: list[tuple[str, dict[str, Any]]] = []
    for key, project in project_by_key.items():
        if key in carried_projects:
            continue
        for chunk in project.get("semantic_chunks") or []:
            if isinstance(chunk, dict) and chunk.get("reading_mode") == "carried-semantic-chunk":
                partial_chunks.append((key, chunk))
    partially_reused_keys = {key for key, _ in partial_chunks}
    declared_chunk_count = int(carry_forward.get("chunk_count") or 0)
    if review_version == 4 and declared_chunk_count != len(partial_chunks):
        errors.append("carry_forward.chunk_count does not match actual carried semantic chunks")
    if review_version == 4 and sorted(str(item) for item in carry_forward.get("partially_reused_project_keys") or []) != sorted(partially_reused_keys):
        errors.append("carry_forward.partially_reused_project_keys does not match actual chunk reuse")
    carried_source_runs = set(carried_projects.values()) | {
        str(chunk.get("carried_forward_from_run_id") or "") for _, chunk in partial_chunks
    }
    has_reuse = bool(carried_projects or partial_chunks)
    if has_reuse:
        if len(carried_source_runs) != 1 or str(carry_forward.get("source_run_id") or "") not in carried_source_runs:
            errors.append("carry_forward.source_run_id does not match actual carried project/chunk markers")
        if carry_forward.get("requires_cross_project_recheck") is not True:
            errors.append("carry_forward summary must require cross-project recheck")
        expected_invalidated = sorted(set(groups) - set(carried_projects))
        if sorted(str(item) for item in carry_forward.get("invalidated_project_keys") or []) != expected_invalidated:
            errors.append("carry_forward.invalidated_project_keys does not match the current carried set")
    if has_reuse or membership_changed:
        if cross_project_recheck.get("required") is not True:
            errors.append("carried-forward review requires a cross_project_recheck")
        recheck_pending = cross_project_recheck.get("completed") is not True
        if recheck_pending:
            pending_mutations = cross_project_recheck.get("pending_evolution_mutations")
            valid_pending_mutations = isinstance(pending_mutations, list) and bool(pending_mutations)
            if valid_pending_mutations:
                for item in pending_mutations:
                    if (
                        not isinstance(item, dict)
                        or item.get("operation") not in {"evolution-propose", "evolution-decide", "evolution-evaluate"}
                        or not EVOLUTION_ID_RE.fullmatch(str(item.get("evolution_id") or ""))
                        or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("state_sha256") or ""))
                    ):
                        valid_pending_mutations = False
                        break
            valid_reopen = (
                cross_project_recheck.get("reopen_reason") == "evolution-state-changed"
                and isinstance(cross_project_recheck.get("reopen_count"), int)
                and not isinstance(cross_project_recheck.get("reopen_count"), bool)
                and int(cross_project_recheck.get("reopen_count") or 0) > 0
                and valid_pending_mutations
                and str(pending_mutations[-1].get("state_sha256") or "") == cross_project_state_sha256(review)
                and not str(cross_project_recheck.get("reviewed_at") or "").strip()
                and not str(cross_project_recheck.get("rationale") or "").strip()
                and not str(cross_project_recheck.get("checked_state_sha256") or "").strip()
            )
            if not allow_pending_cross_project_recheck or not valid_reopen:
                errors.append("carried-forward review requires a completed cross_project_recheck")
            if allow_pending_cross_project_recheck and not valid_reopen:
                errors.append("pending cross_project_recheck lacks a valid evolution-mutation reopen record")
        else:
            if not str(cross_project_recheck.get("reviewed_at") or "").strip() or not str(cross_project_recheck.get("rationale") or "").strip():
                errors.append("cross_project_recheck requires reviewed_at and rationale")
            if cross_project_recheck.get("checked_state_sha256") != cross_project_state_sha256(review):
                errors.append("cross_project_recheck.checked_state_sha256 does not bind the current reviewed state")
    elif carry_forward.get("requires_cross_project_recheck") is True or cross_project_recheck.get("required") is True:
        errors.append("cross-project recheck cannot be required without actual carried projects or semantic chunks")
    for key, group in groups.items():
        project = project_by_key.get(key)
        if project is None:
            continue
        event_ids = [event.event_id for event in group]
        project_hash_matches = (
            project.get("event_set_sha256") == _hash_events(group)
            if review_version in {3, 4}
            else project.get("event_ids_sha256") == _hash_ids(event_ids)
        )
        if project.get("event_count") != len(group) or not project_hash_matches:
            errors.append(f"project {key}: event set hash/count mismatch")
        if review_version in {3, 4} and project.get("event_ids_sha256") != _hash_ids(event_ids):
            errors.append(f"project {key}: ordered event-id hash mismatch")
        if review_version in {3, 4}:
            receipts = project.get("reading_receipts") if isinstance(project.get("reading_receipts"), list) else []
            if not receipts:
                errors.append(f"project {key}: reading_receipts must cover the complete project chain")
            cursor = 0
            carried_receipt_ids: set[str] = set()
            for receipt_index, receipt in enumerate(
                sorted(
                    receipts,
                    key=lambda item: (
                        item.get("start_event")
                        if isinstance(item, dict) and isinstance(item.get("start_event"), int) and not isinstance(item.get("start_event"), bool)
                        else -1
                    ),
                )
            ):
                location = f"project {key} reading_receipts[{receipt_index}]"
                if not isinstance(receipt, dict):
                    errors.append(f"{location}: receipt is not an object")
                    continue
                start = receipt.get("start_event")
                end = receipt.get("end_event_exclusive")
                if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
                    errors.append(f"{location}: range bounds must be integers")
                    continue
                if start != cursor or end <= start or end > len(group):
                    errors.append(f"{location}: ranges must be contiguous, non-overlapping, and inside the project chain")
                    continue
                selected = group[start:end]
                selected_ids = [event.event_id for event in selected]
                if receipt.get("project_key") != key or receipt.get("project_event_set_sha256") != _hash_events(group):
                    errors.append(f"{location}: receipt belongs to another project event set")
                if receipt.get("slice_event_ids_sha256") != _hash_ids(selected_ids):
                    errors.append(f"{location}: slice hash mismatch")
                if review_version == 4 and receipt.get("slice_event_set_sha256") != _hash_events(selected):
                    errors.append(f"{location}: semantic slice hash mismatch")
                if review_version == 4 and receipt.get("boundary_event_set_sha256") != _hash_events(
                    group[max(0, start - 1) : min(len(group), end + 1)]
                ):
                    errors.append(f"{location}: semantic boundary hash mismatch")
                if receipt.get("first_event_id") != selected[0].event_id or receipt.get("last_event_id") != selected[-1].event_id:
                    errors.append(f"{location}: boundary event ids do not match")
                if review_version == 4 and (
                    receipt.get("previous_event_id") != (group[start - 1].event_id if start else None)
                    or receipt.get("next_event_id") != (group[end].event_id if end < len(group) else None)
                ):
                    errors.append(f"{location}: adjacent boundary event ids do not match")
                if review_version == 4:
                    reading_mode = receipt.get("reading_mode")
                    if reading_mode not in {"fresh", "carried-semantic-chunk"}:
                        errors.append(f"{location}: reading_mode must be fresh or carried-semantic-chunk")
                    if reading_mode == "carried-semantic-chunk":
                        semantic_chunk_id = str(receipt.get("semantic_chunk_id") or "")
                        if not CHUNK_ID_RE.fullmatch(semantic_chunk_id) or not str(receipt.get("carried_forward_from_run_id") or "").strip():
                            errors.append(f"{location}: carried receipt needs semantic_chunk_id and source run id")
                        else:
                            carried_receipt_ids.add(semantic_chunk_id)
                    elif receipt.get("semantic_chunk_id") or receipt.get("carried_forward_from_run_id"):
                        errors.append(f"{location}: fresh receipt must not claim carried chunk metadata")
                cursor = end
            if receipts and cursor != len(group):
                errors.append(f"project {key}: reading_receipts do not reach the final event")
        if review_version == 4:
            semantic_chunks = project.get("semantic_chunks") if isinstance(project.get("semantic_chunks"), list) else []
            seen_chunk_ids: set[str] = set()
            chunk_ranges: list[tuple[int, int]] = []
            carried_ids: set[str] = set()
            carried_ranges: list[tuple[int, int]] = []
            for chunk_index, chunk in enumerate(semantic_chunks):
                location = f"project {key} semantic_chunks[{chunk_index}]"
                if not isinstance(chunk, dict):
                    errors.append(f"{location}: chunk is not an object")
                    continue
                chunk_id = str(chunk.get("chunk_id") or "")
                if not CHUNK_ID_RE.fullmatch(chunk_id) or chunk_id in seen_chunk_ids:
                    errors.append(f"{location}: chunk_id must be a unique stable chunk hash")
                seen_chunk_ids.add(chunk_id)
                start = chunk.get("start_event")
                end = chunk.get("end_event_exclusive")
                if (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                    or end > len(group)
                ):
                    errors.append(f"{location}: invalid contiguous range")
                    continue
                if any(start < old_end and end > old_start for old_start, old_end in chunk_ranges):
                    errors.append(f"{location}: semantic chunk ranges overlap")
                chunk_ranges.append((start, end))
                selected = group[start:end]
                selected_ids = [event.event_id for event in selected]
                boundary_hash = _hash_events(group[max(0, start - 1) : min(len(group), end + 1)])
                if chunk.get("event_ids_sha256") != _hash_ids(selected_ids) or chunk.get("event_set_sha256") != _hash_events(selected):
                    errors.append(f"{location}: chunk hashes are stale")
                if chunk.get("boundary_event_set_sha256") != boundary_hash:
                    errors.append(f"{location}: chunk boundary hash is stale")
                if chunk_id != _semantic_chunk_id(key, start, end, _hash_events(selected), boundary_hash):
                    errors.append(f"{location}: chunk_id does not bind the current range and boundaries")
                if chunk.get("first_event_id") != selected[0].event_id or chunk.get("last_event_id") != selected[-1].event_id:
                    errors.append(f"{location}: chunk boundaries are stale")
                if (
                    chunk.get("previous_event_id") != (group[start - 1].event_id if start else None)
                    or chunk.get("next_event_id") != (group[end].event_id if end < len(group) else None)
                ):
                    errors.append(f"{location}: chunk adjacent boundaries are stale")
                notes = chunk.get("notes")
                if not isinstance(notes, list) or not notes:
                    errors.append(f"{location}: semantic chunk needs evidence-bound notes")
                    notes = []
                selected_id_set = set(selected_ids)
                for note_index, note in enumerate(notes):
                    note_location = f"{location}.notes[{note_index}]"
                    if not isinstance(note, dict) or not str(note.get("text") or "").strip():
                        errors.append(f"{note_location}: note needs non-empty text")
                        continue
                    if note.get("status") not in HISTORY_ITEM_STATUSES:
                        errors.append(f"{note_location}: invalid note status")
                    evidence_ids = note.get("evidence_event_ids")
                    if not isinstance(evidence_ids, list) or not evidence_ids or any(str(item) not in selected_id_set for item in evidence_ids):
                        errors.append(f"{note_location}: evidence must stay inside this exact chunk")
                    if note.get("source_note_sha256") != semantic_note_sha256(note):
                        errors.append(f"{note_location}: source_note_sha256 does not bind the note")
                if not str(chunk.get("reviewed_at") or "").strip() or not str(chunk.get("attestation") or "").strip():
                    errors.append(f"{location}: reviewed_at and attestation are required")
                if chunk.get("reading_mode") == "carried-semantic-chunk":
                    if not str(chunk.get("carried_forward_from_run_id") or "").strip():
                        errors.append(f"{location}: carried chunk needs a source run id")
                    carried_ids.add(chunk_id)
                    carried_ranges.append((start, end))
            if carried_ids or carried_receipt_ids:
                if carried_receipt_ids != carried_ids:
                    errors.append(f"project {key}: carried chunks and carried reading receipts do not match")
            if carried_ids:
                synthesis = project.get("project_synthesis") if isinstance(project.get("project_synthesis"), dict) else {}
                if synthesis.get("status") != "completed":
                    errors.append(f"project {key}: carried chunks require completed full project_synthesis")
                if synthesis.get("mode") != "fresh":
                    errors.append(f"project {key}: project_synthesis.mode must be fresh after chunk reuse")
                if synthesis.get("project_event_set_sha256") != _hash_events(group):
                    errors.append(f"project {key}: project_synthesis hash is stale")
                if sorted(str(item) for item in synthesis.get("reused_chunk_ids") or []) != sorted(carried_ids):
                    errors.append(f"project {key}: project_synthesis.reused_chunk_ids does not match actual carried chunks")
                if synthesis.get("reopened_ranges") != _reopened_ranges(len(group), carried_ranges):
                    errors.append(f"project {key}: project_synthesis.reopened_ranges does not match the exact cache complement")
                if synthesis.get("synthesis_sha256") != project_synthesis_sha256(project):
                    errors.append(f"project {key}: project_synthesis.synthesis_sha256 does not bind final project conclusions")
                for field in ("reviewed_at", "attestation", "rationale"):
                    if not str(synthesis.get(field) or "").strip():
                        errors.append(f"project {key}: project_synthesis.{field} is required after chunk reuse")
        status = project.get("semantic_status")
        if status not in PROJECT_STATUSES:
            errors.append(f"project {key}: semantic_status must be reviewed or reviewed-no-knowledge")
        disposition = project.get("default_disposition")
        if disposition not in SEMANTIC_DISPOSITIONS:
            errors.append(f"project {key}: invalid default_disposition")
        if status == "reviewed-no-knowledge" and not str(project.get("rationale") or "").strip():
            errors.append(f"project {key}: reviewed-no-knowledge requires rationale")
        link_analysis = project.get("link_analysis") if isinstance(project.get("link_analysis"), dict) else {}
        link_status = link_analysis.get("status")
        expected_link_statuses = {"not-published"} if status == "reviewed-no-knowledge" else {"linked", "intentional-isolate"}
        if link_status not in expected_link_statuses:
            errors.append(f"project {key}: link_analysis.status must be one of {sorted(expected_link_statuses)}")
        if link_status == "intentional-isolate" and not str(link_analysis.get("rationale") or "").strip():
            errors.append(f"project {key}: intentional-isolate requires a rationale")
        completion_state = project.get("completion") if isinstance(project.get("completion"), dict) else {}
        completion_level = completion_state.get("level")
        completion_status = completion_state.get("status")
        expected_completion_levels = {"not-published"} if status == "reviewed-no-knowledge" else COMPLETION_LEVELS - {"unreviewed", "not-published"}
        if completion_level not in expected_completion_levels:
            errors.append(f"project {key}: invalid completion level for semantic status")
        if completion_status not in COMPLETION_STATUSES:
            errors.append(f"project {key}: invalid completion status")
        if not str(completion_state.get("rationale") or "").strip():
            errors.append(f"project {key}: completion rationale is required")
        completion_evidence_ids = completion_state.get("evidence_event_ids")
        if status == "reviewed" and (not isinstance(completion_evidence_ids, list) or not completion_evidence_ids):
            errors.append(f"project {key}: reviewed completion state needs evidence_event_ids")
            completion_evidence_ids = []
        elif not isinstance(completion_evidence_ids, list):
            errors.append(f"project {key}: completion evidence_event_ids must be a list")
            completion_evidence_ids = []
        for event_id in completion_evidence_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"project {key}: unknown completion evidence event {event_id}")
            elif _event_project_key(event) != key:
                errors.append(f"project {key}: completion evidence event {event_id} belongs to another project")
        history = project.get("history") if isinstance(project.get("history"), dict) else {}
        history_count = 0
        for section in HISTORY_SECTIONS:
            items = history.get(section)
            if not isinstance(items, list):
                errors.append(f"project {key}: history.{section} must be a list")
                continue
            history_count += len(items)
            for item_index, item in enumerate(items):
                _validate_history_item(item, key, event_by_id, errors, f"project {key} {section}[{item_index}]")
        if status == "reviewed" and history_count == 0:
            errors.append(f"project {key}: reviewed knowledge project needs at least one evidence-bound history item")
        exceptions = project.get("event_exceptions") if isinstance(project.get("event_exceptions"), list) else []
        seen_exceptions: set[str] = set()
        for exception in exceptions:
            if not isinstance(exception, dict):
                errors.append(f"project {key}: event exception is not an object")
                continue
            event_id = str(exception.get("event_id") or "")
            if event_id in seen_exceptions:
                errors.append(f"project {key}: duplicate exception for {event_id}")
            seen_exceptions.add(event_id)
            if event_id not in event_ids:
                errors.append(f"project {key}: exception event {event_id} is not in this project")
            if exception.get("disposition") not in SEMANTIC_DISPOSITIONS:
                errors.append(f"project {key}: invalid exception disposition for {event_id}")

    attributions = review.get("actor_attributions") if isinstance(review.get("actor_attributions"), list) else []
    attribution_ids: set[str] = set()
    project_lane_attribution: dict[str, str] = {}
    event_attribution: dict[str, str] = {}
    for index, attribution in enumerate(attributions):
        location = f"actor_attributions[{index}]"
        if not isinstance(attribution, dict):
            errors.append(f"{location} is not an object")
            continue
        attribution_id = str(attribution.get("attribution_id") or "")
        if not ATTRIBUTION_ID_RE.fullmatch(attribution_id) or attribution_id in attribution_ids:
            errors.append(f"{location}: attribution_id must be a unique actor-* stable id")
        attribution_ids.add(attribution_id)
        scope = attribution.get("scope")
        if scope not in ATTRIBUTION_SCOPES:
            errors.append(f"{location}: invalid attribution scope")
        actor = str(attribution.get("actor_kind") or "")
        if actor not in ATTRIBUTION_ACTORS:
            errors.append(f"{location}: invalid actor_kind")
        project_key = str(attribution.get("project_key") or "")
        if project_key not in project_by_key:
            errors.append(f"{location}: project_key must exist")
        basis = attribution.get("basis")
        if not isinstance(basis, list) or not basis:
            errors.append(f"{location}: attribution basis is required")
            basis_set: set[str] = set()
        elif any(str(item) not in ATTRIBUTION_BASES for item in basis):
            errors.append(f"{location}: attribution basis contains an unsupported shortcut")
            basis_set = {str(item) for item in basis}
        else:
            basis_set = {str(item) for item in basis}
        if not str(attribution.get("rationale") or "").strip():
            errors.append(f"{location}: rationale is required")
        evidence_ids = attribution.get("evidence_event_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            errors.append(f"{location}: evidence_event_ids are required")
            evidence_ids = []
        attribution_evidence_events: list[UnifiedEvent] = []
        for event_id in evidence_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"{location}: unknown evidence event {event_id}")
            elif _event_project_key(event) != project_key:
                errors.append(f"{location}: evidence event {event_id} belongs to another project")
            else:
                attribution_evidence_events.append(event)
        if actor == "primary_user":
            semantic_bases = basis_set & {
                "explicit-self-reference",
                "session-owner-confirmation",
                "cross-event-corroboration",
                "role-context-review",
            }
            if not semantic_bases:
                errors.append(f"{location}: native-user-lane alone cannot establish primary-user identity")
            if not any(event.role == "user" for event in attribution_evidence_events):
                errors.append(f"{location}: primary-user attribution needs at least one user-lane evidence event")
        if scope == "project-user-lane":
            if project_key in project_lane_attribution:
                errors.append(f"{location}: project user lane already has an attribution")
            project_lane_attribution[project_key] = actor
        elif scope == "event":
            event_id = str(attribution.get("event_id") or "")
            event = event_by_id.get(event_id)
            if event is None:
                errors.append(f"{location}: event scope requires a known event_id")
            elif _event_project_key(event) != project_key:
                errors.append(f"{location}: attributed event belongs to another project")
            if event_id in event_attribution:
                errors.append(f"{location}: event already has an attribution")
            event_attribution[event_id] = actor

    completion_evidence_types = {
        "implemented": {"patch", "tool_result", "status"},
        "artifact-created": {"patch", "tool_result", "attachment", "delivery", "status"},
        "installed": {"tool_result", "browser", "device", "status"},
        "enabled": {"tool_result", "browser", "device", "status"},
        "invoked": {"tool_result", "browser", "device", "status"},
        "automated-tests-passed": {"tool_result", "status"},
        "real-interaction-observed": {"browser", "device", "status", "delivery"},
        "submitted": {"tool_result", "browser", "status", "delivery"},
        "merged": {"tool_result", "browser", "status", "delivery"},
        "remotely-published": {"browser", "status", "delivery"},
        "publicly-reachable": {"browser"},
    }
    for key, project in project_by_key.items():
        if project.get("semantic_status") != "reviewed":
            continue
        completion_state = project.get("completion") if isinstance(project.get("completion"), dict) else {}
        level = completion_state.get("level")
        status = completion_state.get("status")
        completion_events = [
            event_by_id[str(event_id)]
            for event_id in completion_state.get("evidence_event_ids") or []
            if str(event_id) in event_by_id
        ]
        has_primary_user = any(
            event.role == "user" and _effective_actor(event, project_lane_attribution, event_attribution) == "primary_user"
            for event in completion_events
        )
        if status == "observed":
            if level in {"requested", "user-accepted"}:
                if not has_primary_user:
                    errors.append(f"project {key}: observed {level} needs attributed primary-user evidence")
            elif level in completion_evidence_types:
                allowed_types = completion_evidence_types[str(level)]
                if not any(event.evidence_grade == "B" and event.event_type in allowed_types for event in completion_events):
                    errors.append(f"project {key}: observed {level} needs matching observable evidence types {sorted(allowed_types)}")
            elif level in {"designed", "unknown"}:
                if not has_primary_user and not any(event.evidence_grade == "B" for event in completion_events):
                    errors.append(f"project {key}: observed {level} needs primary-user or observable grade-B evidence")

    claims = review.get("base_claims") if isinstance(review.get("base_claims"), list) else []
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        location = f"base_claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{location} is not an object")
            continue
        claim_id = str(claim.get("claim_id") or "")
        if not CLAIM_ID_RE.fullmatch(claim_id) or claim_id in claim_ids:
            errors.append(f"{location}: claim_id must be a unique claim-* stable id")
        claim_ids.add(claim_id)
        claim_type = claim.get("knowledge_type")
        if claim_type not in CLAIM_TYPES:
            errors.append(f"{location}: invalid knowledge_type")
        if claim.get("status") not in CLAIM_STATUSES:
            errors.append(f"{location}: invalid status")
        if claim.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"{location}: invalid confidence")
        if not str(claim.get("statement") or "").strip():
            errors.append(f"{location}: empty statement")
        if not str(claim.get("observed_at") or "").strip():
            errors.append(f"{location}: observed_at is required")
        if not str(claim.get("applies_to") or "").strip():
            errors.append(f"{location}: applies_to is required")
        evidence_ids = claim.get("evidence_event_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            errors.append(f"{location}: evidence_event_ids required")
            continue
        claim_events: list[UnifiedEvent] = []
        for event_id in evidence_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"{location}: unknown evidence event {event_id}")
            else:
                claim_events.append(event)
                event_project_key = event.project_key or f"session:{event.logical_session_id}"
                if project_by_key.get(event_project_key, {}).get("semantic_status") != "reviewed":
                    errors.append(f"{location}: claim evidence belongs to a project not published as reviewed knowledge")
        if claim.get("status") == "confirmed" and claim_type in {"identity", "direction", "collaboration", "expression"}:
            if not any(event.role == "user" and _effective_actor(event, project_lane_attribution, event_attribution) == "primary_user" for event in claim_events):
                errors.append(f"{location}: confirmed {claim_type} claim needs semantically attributed primary-user evidence")
        if claim.get("status") == "confirmed" and claim_type in {"collaboration", "expression"}:
            rule_scope = claim.get("rule_scope")
            derivation = claim.get("derivation")
            if rule_scope not in FEEDBACK_SCOPES:
                errors.append(f"{location}: confirmed {claim_type} claim needs a bounded rule_scope")
            if derivation not in RULE_CLAIM_DERIVATIONS:
                errors.append(f"{location}: confirmed {claim_type} claim needs a valid derivation")
            if derivation == "repeated-context":
                distinct_projects = {_event_project_key(event) for event in claim_events}
                if len(distinct_projects) < 2:
                    errors.append(f"{location}: repeated-context promotion needs evidence from at least two project chains")
        if claim_type in {"identity", "direction", "collaboration", "expression"} and str(claim.get("subject") or "") != "primary_user":
            errors.append(f"{location}: personal base-knowledge subject must be primary_user")
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "")
        for field in ("conflicts", "supersedes"):
            references = claim.get(field)
            if references is None:
                references = []
            if not isinstance(references, list):
                errors.append(f"base_claims[{index}].{field} must be a list")
                continue
            for target_id in references:
                if str(target_id) == claim_id or str(target_id) not in claim_ids:
                    errors.append(f"base_claims[{index}].{field} references an unknown or self claim")

    claim_by_id = {str(claim.get("claim_id")): claim for claim in claims if isinstance(claim, dict)}
    feedback_signals = review.get("feedback_signals") if isinstance(review.get("feedback_signals"), list) else []
    feedback_by_id: dict[str, dict[str, Any]] = {}
    for index, signal in enumerate(feedback_signals):
        location = f"feedback_signals[{index}]"
        if not isinstance(signal, dict):
            errors.append(f"{location} is not an object")
            continue
        feedback_id = str(signal.get("feedback_id") or "")
        if not FEEDBACK_ID_RE.fullmatch(feedback_id) or feedback_id in feedback_by_id:
            errors.append(f"{location}: feedback_id must be a unique feedback-* stable id")
        feedback_by_id[feedback_id] = signal
        project_key = str(signal.get("project_key") or "")
        if project_by_key.get(project_key, {}).get("semantic_status") != "reviewed":
            errors.append(f"{location}: project_key must identify a reviewed project")
        if signal.get("kind") not in FEEDBACK_KINDS:
            errors.append(f"{location}: invalid feedback kind")
        if signal.get("object") not in FEEDBACK_OBJECTS:
            errors.append(f"{location}: invalid feedback object")
        if signal.get("scope") not in FEEDBACK_SCOPES:
            errors.append(f"{location}: invalid feedback scope")
        if signal.get("status") not in FEEDBACK_STATUSES:
            errors.append(f"{location}: invalid feedback status")
        if not str(signal.get("statement") or "").strip():
            errors.append(f"{location}: statement is required")
        if not str(signal.get("applies_to") or "").strip():
            errors.append(f"{location}: applies_to is required")
        evidence_ids = signal.get("evidence_event_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            errors.append(f"{location}: evidence_event_ids are required")
            continue
        signal_events: list[UnifiedEvent] = []
        for event_id in evidence_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"{location}: unknown evidence event {event_id}")
            elif _event_project_key(event) != project_key:
                errors.append(f"{location}: evidence event {event_id} belongs to another project")
            else:
                signal_events.append(event)
        if signal.get("kind") in {"positive", "negative", "gap"}:
            if not any(event.role == "user" and _effective_actor(event, project_lane_attribution, event_attribution) == "primary_user" for event in signal_events):
                errors.append(f"{location}: human feedback needs semantically attributed primary-user evidence")
        elif signal.get("kind") == "outcome" and signal_events:
            if not any(
                (event.role == "user" and _effective_actor(event, project_lane_attribution, event_attribution) == "primary_user")
                or event.evidence_grade == "B"
                for event in signal_events
            ):
                errors.append(f"{location}: outcome feedback needs primary-user or observable grade-B evidence")

    rule_evolutions = review.get("rule_evolutions") if isinstance(review.get("rule_evolutions"), list) else []
    evolution_ids: set[str] = set()
    promoted_by_evolution: set[str] = set()
    for index, evolution in enumerate(rule_evolutions):
        location = f"rule_evolutions[{index}]"
        if not isinstance(evolution, dict):
            errors.append(f"{location} is not an object")
            continue
        evolution_id = str(evolution.get("evolution_id") or "")
        if not EVOLUTION_ID_RE.fullmatch(evolution_id) or evolution_id in evolution_ids:
            errors.append(f"{location}: evolution_id must be a unique evolution-* stable id")
        evolution_ids.add(evolution_id)
        status = evolution.get("status")
        if status not in EVOLUTION_STATUSES:
            errors.append(f"{location}: invalid evolution status")
        scope = evolution.get("scope")
        if scope not in FEEDBACK_SCOPES:
            errors.append(f"{location}: invalid evolution scope")
        version = evolution.get("rule_version")
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            errors.append(f"{location}: rule_version must be a positive integer")
        feedback_ids = evolution.get("feedback_ids")
        if not isinstance(feedback_ids, list) or not feedback_ids:
            errors.append(f"{location}: feedback_ids are required")
            feedback_ids = []
        unknown_feedback = [str(item) for item in feedback_ids if str(item) not in feedback_by_id]
        if unknown_feedback:
            errors.append(f"{location}: feedback_ids contain unknown records")
        for field in ("proposed_rule", "rationale", "expected_behavior_change"):
            if not str(evolution.get(field) or "").strip():
                errors.append(f"{location}: {field} is required")
        validation_result = evolution.get("validation_result")
        if validation_result not in EVOLUTION_VALIDATION_RESULTS:
            errors.append(f"{location}: invalid validation_result")
        approval_ids = evolution.get("approval_event_ids")
        if not isinstance(approval_ids, list):
            approval_ids = []
            errors.append(f"{location}: approval_event_ids must be a list")
        approval_events: list[UnifiedEvent] = []
        for event_id in approval_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"{location}: unknown approval event {event_id}")
            else:
                approval_events.append(event)
                if project_by_key.get(_event_project_key(event), {}).get("semantic_status") != "reviewed":
                    errors.append(f"{location}: approval evidence belongs to a project not published as reviewed knowledge")
        promoted_claim_id = str(evolution.get("promoted_claim_id") or "")
        if status in {"approved", "validated"}:
            if not any(feedback_by_id.get(str(feedback_id), {}).get("status") == "observed" for feedback_id in feedback_ids):
                errors.append(f"{location}: approved or validated evolution needs at least one observed feedback signal")
            promoted = claim_by_id.get(promoted_claim_id)
            if promoted is None:
                errors.append(f"{location}: approved or validated evolution needs an existing promoted_claim_id")
            elif promoted.get("status") != "confirmed" or promoted.get("knowledge_type") not in {"evidence_rule", "collaboration", "expression"}:
                errors.append(f"{location}: promoted claim must be a confirmed evidence/collaboration/expression rule")
            else:
                promoted_by_evolution.add(promoted_claim_id)
                if promoted.get("knowledge_type") in {"collaboration", "expression"} and promoted.get("derivation") != "feedback-promotion":
                    errors.append(f"{location}: a feedback-promoted collaboration/expression claim must use derivation=feedback-promotion")
                if promoted.get("knowledge_type") in {"collaboration", "expression"} and promoted.get("rule_scope") != scope:
                    errors.append(f"{location}: evolution scope must equal the promoted claim rule_scope")
            if not approval_events or not any(
                event.role == "user" and _effective_actor(event, project_lane_attribution, event_attribution) == "primary_user"
                for event in approval_events
            ):
                errors.append(f"{location}: approved rule needs semantically attributed primary-user approval evidence")
        elif promoted_claim_id:
            errors.append(f"{location}: candidate or rejected evolution must not activate a promoted claim")
        if scope == "global" and status in {"approved", "validated"}:
            distinct_projects = {
                str(feedback_by_id[str(feedback_id)].get("project_key") or "")
                for feedback_id in feedback_ids
                if str(feedback_id) in feedback_by_id and feedback_by_id[str(feedback_id)].get("status") == "observed"
            }
            explicit_global = evolution.get("explicit_global_approval") is True
            if len(distinct_projects) < 2 and not explicit_global:
                errors.append(f"{location}: a global rule needs feedback from two project contexts or explicit_global_approval")
        baseline_ids = evolution.get("baseline_event_ids")
        if not isinstance(baseline_ids, list):
            baseline_ids = []
            errors.append(f"{location}: baseline_event_ids must be a list")
        for event_id in baseline_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"{location}: unknown baseline event {event_id}")
            elif project_by_key.get(_event_project_key(event), {}).get("semantic_status") != "reviewed":
                errors.append(f"{location}: baseline evidence belongs to a project not published as reviewed knowledge")
        validation_ids = evolution.get("validation_event_ids")
        if not isinstance(validation_ids, list):
            validation_ids = []
            errors.append(f"{location}: validation_event_ids must be a list")
        validation_events: list[UnifiedEvent] = []
        for event_id in validation_ids:
            event = event_by_id.get(str(event_id))
            if event is None:
                errors.append(f"{location}: unknown validation event {event_id}")
            else:
                validation_events.append(event)
                if project_by_key.get(_event_project_key(event), {}).get("semantic_status") != "reviewed":
                    errors.append(f"{location}: validation evidence belongs to a project not published as reviewed knowledge")
        if (
            baseline_ids
            and validation_ids
            and all(str(event_id) in event_by_id for event_id in list(baseline_ids) + list(validation_ids))
        ):
            for chronology_error in behavior_evidence_order_errors(events, baseline_ids, validation_ids):
                errors.append(f"{location}: {chronology_error}")
        if status == "validated":
            if validation_result != "passed":
                errors.append(f"{location}: validated evolution requires validation_result=passed")
            if not str(evolution.get("observed_behavior_change") or "").strip():
                errors.append(f"{location}: validated evolution needs observed_behavior_change")
            if not baseline_ids:
                errors.append(f"{location}: validated evolution needs baseline_event_ids for a before/after comparison")
            if set(str(item) for item in baseline_ids) & set(str(item) for item in validation_ids):
                errors.append(f"{location}: baseline and validation evidence must be distinct")
            if not validation_events or not any(
                event.evidence_grade == "B"
                or (event.role == "user" and _effective_actor(event, project_lane_attribution, event_attribution) == "primary_user")
                for event in validation_events
            ):
                errors.append(f"{location}: validated evolution needs observable grade-B or primary-user behavior evidence")
        elif status == "approved" and validation_result == "passed":
            errors.append(f"{location}: a passed behavior check must use status=validated")

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        if claim.get("status") == "confirmed" and claim.get("knowledge_type") in {"collaboration", "expression"}:
            if claim.get("derivation") == "feedback-promotion" and str(claim.get("claim_id") or "") not in promoted_by_evolution:
                errors.append(f"base_claims[{index}]: feedback-promotion claim needs an approved or validated rule_evolution")

    relationships = review.get("project_relationships") if isinstance(review.get("project_relationships"), list) else []
    relationship_ids: set[str] = set()
    confirmed_participants: set[str] = set()
    for index, relationship in enumerate(relationships):
        location = f"project_relationships[{index}]"
        if not isinstance(relationship, dict):
            errors.append(f"{location} is not an object")
            continue
        relationship_id = str(relationship.get("relationship_id") or "")
        if not STABLE_ID_RE.fullmatch(relationship_id) or relationship_id in relationship_ids:
            errors.append(f"{location}: relationship_id must be a unique rel-* stable id")
        relationship_ids.add(relationship_id)
        source_key = str(relationship.get("source_project_key") or "")
        target_key = str(relationship.get("target_project_key") or "")
        if source_key not in project_by_key or target_key not in project_by_key:
            errors.append(f"{location}: source and target project keys must exist")
        if source_key and source_key == target_key:
            errors.append(f"{location}: source and target must differ")
        if relationship.get("status") not in RELATIONSHIP_STATUSES:
            errors.append(f"{location}: invalid status")
        if relationship.get("direction") not in RELATIONSHIP_DIRECTIONS:
            errors.append(f"{location}: invalid direction")
        if relationship.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"{location}: invalid confidence")
        relation_label = str(relationship.get("relation") or "").strip()
        if not relation_label:
            errors.append(f"{location}: relation label is required")
        elif len(relation_label) > 120 or "\n" in relation_label or "\r" in relation_label:
            errors.append(f"{location}: relation label must be one line and at most 120 characters")
        if not str(relationship.get("rationale") or "").strip():
            errors.append(f"{location}: rationale is required")
        evidence_basis = relationship.get("evidence_basis")
        if not isinstance(evidence_basis, list) or not evidence_basis:
            errors.append(f"{location}: evidence_basis is required")
        elif any(str(item) not in RELATIONSHIP_BASES for item in evidence_basis):
            errors.append(f"{location}: evidence_basis contains an unsupported or lexical-only basis")
        source_ids = relationship.get("source_evidence_event_ids")
        target_ids = relationship.get("target_evidence_event_ids")
        if not isinstance(source_ids, list):
            source_ids = []
            errors.append(f"{location}: source_evidence_event_ids must be a list")
        if not isinstance(target_ids, list):
            target_ids = []
            errors.append(f"{location}: target_evidence_event_ids must be a list")
        if not source_ids or not target_ids:
            errors.append(f"{location}: relation analysis needs evidence from both project chains")
        for side, ids, expected_key in (("source", source_ids, source_key), ("target", target_ids, target_key)):
            for event_id in ids:
                event = event_by_id.get(str(event_id))
                if event is None:
                    errors.append(f"{location}: unknown {side} evidence event {event_id}")
                elif (event.project_key or f"session:{event.logical_session_id}") != expected_key:
                    errors.append(f"{location}: {side} evidence event {event_id} belongs to another project")
        if relationship.get("status") == "confirmed":
            if project_by_key.get(source_key, {}).get("semantic_status") != "reviewed" or project_by_key.get(target_key, {}).get("semantic_status") != "reviewed":
                errors.append(f"{location}: confirmed relations can connect only published projects")
            confirmed_participants.update({source_key, target_key})

    claim_project_keys: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        for event_id in claim.get("evidence_event_ids") or []:
            event = event_by_id.get(str(event_id))
            if event is not None:
                claim_project_keys.add(event.project_key or f"session:{event.logical_session_id}")
    for key, project in project_by_key.items():
        link_analysis = project.get("link_analysis") if isinstance(project.get("link_analysis"), dict) else {}
        if project.get("semantic_status") == "reviewed" and link_analysis.get("status") == "linked" and key not in confirmed_participants and key not in claim_project_keys:
            errors.append(f"project {key}: link_analysis says linked but no confirmed relationship or evidence-linked base claim exists")

    if _sensitive_review_text(
        {
            "reviewer": reviewer,
            "project_membership": review.get("project_membership"),
            "actor_attributions": attributions,
            "base_claims": claims,
            "feedback_signals": feedback_signals,
            "rule_evolutions": rule_evolutions,
            "project_relationships": relationships,
            "projects": projects,
        }
    ):
        errors.append("review file contains a credential, private contact, private network address, or unredacted home path")
    return review, events, errors


def _entry_markdown(item: dict[str, Any]) -> str:
    evidence = ", ".join(f"`{event_id}`" for event_id in item.get("evidence_event_ids") or [])
    status = str(item.get("status") or "observed")
    lines = [f"- {str(item.get('text') or '').strip()}  ", f"  Evidence: {evidence}. Status: `{status}`."]
    if item.get("before") is not None or item.get("after") is not None:
        lines.append(f"  Before: {str(item.get('before') or '').strip() or '[empty]' }")
        lines.append(f"  After: {str(item.get('after') or '').strip() or '[empty]' }")
    if item.get("applies_to"):
        lines.append(f"  Applies to: {str(item.get('applies_to')).strip()}")
    return "\n".join(lines)


def _render_base(title: str, claims: list[dict[str, Any]], review: dict[str, Any]) -> str:
    lines = [f"# {title}", "", f"Published from reviewed evidence run `{review.get('run_id')}`.", ""]
    if not claims:
        lines.extend(["No reviewed claim was published for this category.", ""])
    for claim in claims:
        evidence = ", ".join(f"`{event_id}`" for event_id in claim.get("evidence_event_ids") or [])
        lines.extend(
            [
                f"## {claim.get('claim_id')}",
                "",
                str(claim.get("statement") or "").strip(),
                "",
                f"- Status: `{claim.get('status')}`",
                f"- Confidence: `{claim.get('confidence')}`",
                f"- Observed at: {claim.get('observed_at') or 'not supplied'}",
                f"- Applies to: {claim.get('applies_to') or 'bounded by cited context'}",
                *( [f"- Rule scope: `{claim.get('rule_scope')}`", f"- Derivation: `{claim.get('derivation')}`"] if claim.get("knowledge_type") in {"collaboration", "expression"} else [] ),
                f"- Evidence: {evidence}",
                f"- Conflicts: {', '.join(claim.get('conflicts') or []) or 'none recorded'}",
                f"- Supersedes: {', '.join(claim.get('supersedes') or []) or 'none recorded'}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_active_evolutions(evolutions: list[dict[str, Any]]) -> str:
    active = [item for item in evolutions if item.get("status") in {"approved", "validated"}]
    lines = ["## Feedback-governed rule evolution", ""]
    if not active:
        lines.extend(
            [
                "No approved rule change was published. Recorded feedback without approval or behavior evidence remains in the private audit ledger.",
                "",
            ]
        )
        return "\n".join(lines)
    for item in sorted(active, key=lambda value: (str(value.get("scope") or ""), int(value.get("rule_version") or 0), str(value.get("evolution_id") or ""))):
        lines.extend(
            [
                f"### {item.get('evolution_id')}",
                "",
                str(item.get("proposed_rule") or "").strip(),
                "",
                f"- State: `{item.get('status')}`",
                f"- Scope: `{item.get('scope')}`",
                f"- Rule version: `{item.get('rule_version')}`",
                f"- Promoted claim: `{item.get('promoted_claim_id')}`",
                f"- Feedback records: {', '.join(f'`{value}`' for value in item.get('feedback_ids') or [])}",
                f"- Expected behavior change: {str(item.get('expected_behavior_change') or '').strip()}",
                f"- Validation result: `{item.get('validation_result')}`",
                f"- Baseline evidence: {', '.join(f'`{value}`' for value in item.get('baseline_event_ids') or []) or 'not yet supplied'}",
                f"- After evidence: {', '.join(f'`{value}`' for value in item.get('validation_event_ids') or []) or 'not yet supplied'}",
                f"- Observed behavior change: {str(item.get('observed_behavior_change') or '').strip() or 'awaiting evidence'}",
                "",
            ]
        )
    return "\n".join(lines)


def _claim_document_path(claim_type: str) -> str:
    if claim_type == "evidence_rule":
        return "00-evidence-rules.md"
    if claim_type in {"identity", "direction"}:
        return "01-identity-and-current-direction.md"
    return "02-collaboration-and-expression.md"


def _edge_id(*parts: str) -> str:
    return "edge-" + sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]


def _relative_markdown_path(source: str, target: str) -> str:
    base = posixpath.dirname(source) or "."
    return posixpath.relpath(target, start=base)


def _append_related_section(content: str, source_path: str, entries: list[dict[str, Any]], isolate_rationale: str | None = None) -> str:
    lines = [content.rstrip(), "", "## Related knowledge", ""]
    if not entries:
        if isolate_rationale:
            lines.extend([f"Intentional isolate: {isolate_rationale}", ""])
        else:
            lines.extend(["No evidence-backed document relationship was published.", ""])
        return "\n".join(lines)
    seen: set[tuple[str, str]] = set()
    for entry in sorted(entries, key=lambda item: (str(item.get("title") or ""), str(item.get("target_path") or ""), str(item.get("edge_id") or ""))):
        marker = (str(entry.get("target_path")), str(entry.get("edge_id")))
        if marker in seen:
            continue
        seen.add(marker)
        target_path = str(entry["target_path"])
        title = str(entry.get("title") or target_path).replace("[", "\\[").replace("]", "\\]")
        relation = " ".join(str(entry.get("relation") or "related").split())
        evidence = ", ".join(f"`{event_id}`" for event_id in entry.get("evidence_event_ids") or []) or "structural reviewed link"
        lines.append(f"- [{title}]({_relative_markdown_path(source_path, target_path)}) — {relation}. Evidence: {evidence}.")
    lines.append("")
    return "\n".join(lines)


def distill_review(kb: Path, review_path: Path) -> dict[str, Any]:
    root = _kb_root(kb)
    review, events, errors = validate_review(root, review_path)
    if errors:
        raise ValueError("review validation failed:\n- " + "\n- ".join(errors))
    event_by_id = {event.event_id: event for event in events}
    groups = _project_groups(events)
    project_by_key = {str(project["project_key"]): project for project in review["projects"]}
    semantic_rows: list[dict[str, Any]] = []
    for key, group in groups.items():
        project = project_by_key[key]
        exception_map = {str(item["event_id"]): item for item in project.get("event_exceptions") or []}
        for event in group:
            exception = exception_map.get(event.event_id)
            semantic_rows.append(
                {
                    "stage": "semantic",
                    "run_id": review["run_id"],
                    "event_id": event.event_id,
                    "project_key": key,
                    "disposition": exception.get("disposition") if exception else project["default_disposition"],
                    "notes": exception.get("notes") if exception else "project review default",
                }
            )

    claims = list(review.get("base_claims") or [])
    feedback_signals = list(review.get("feedback_signals") or [])
    rule_evolutions = list(review.get("rule_evolutions") or [])
    claims_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        claims_by_type[str(claim["knowledge_type"])].append(claim)
    evidence_rules = _render_base("Evidence and reading rules", claims_by_type["evidence_rule"], review)
    evidence_rules += "\n## Publication gates\n\nEvery retained event has a semantic disposition. Every published assertion cites existing event ids. Unknown, disputed, stale, and retracted claims remain explicit.\n"
    identity = _render_base("Identity and current direction", claims_by_type["identity"] + claims_by_type["direction"], review)
    collaboration = _render_base("Collaboration and expression rules", claims_by_type["collaboration"] + claims_by_type["expression"], review)
    collaboration += "\n" + _render_active_evolutions(rule_evolutions)
    documents: dict[str, str] = {
        "00-evidence-rules.md": evidence_rules,
        "01-identity-and-current-direction.md": identity,
        "02-collaboration-and-expression.md": collaboration,
    }
    index_documents: list[dict[str, Any]] = []
    for path, doc_type in (
        ("00-evidence-rules.md", "evidence"),
        ("01-identity-and-current-direction.md", "identity"),
        ("02-collaboration-and-expression.md", "collaboration"),
    ):
        index_documents.append({"path": path, "title": documents[path].splitlines()[0].lstrip("# "), "type": doc_type, "keywords": _keywords(documents[path])})

    project_path_by_key: dict[str, str] = {}
    published_projects = 0
    for key in sorted(groups):
        project = project_by_key[key]
        if project["semantic_status"] == "reviewed-no-knowledge":
            continue
        title = str(project.get("title") or key)
        aliases = [str(alias) for alias in project.get("aliases") or []]
        lines = [f"# {title}", "", f"Project key: `{key}`", "", f"Aliases: {', '.join(aliases) or 'none'}", ""]
        completion_state = project.get("completion") or {}
        completion_evidence = ", ".join(f"`{event_id}`" for event_id in completion_state.get("evidence_event_ids") or []) or "none"
        lines.extend(
            [
                "## Completion state",
                "",
                f"- Highest supported level: `{completion_state.get('level')}`",
                f"- Evidence status: `{completion_state.get('status')}`",
                f"- Evidence: {completion_evidence}",
                f"- Boundary: {str(completion_state.get('rationale') or '').strip()}",
                "",
            ]
        )
        for section in HISTORY_SECTIONS:
            heading = section.replace("_", " ").title()
            lines.extend([f"## {heading}", ""])
            items = project["history"][section]
            if not items:
                lines.extend(["No reviewed assertion for this section.", ""])
            else:
                lines.extend(_entry_markdown(item) for item in items)
                lines.append("")
        filename = _slug(title, key)
        relative = f"projects/{filename}"
        project_path_by_key[key] = relative
        documents[relative] = "\n".join(lines)
        index_documents.append(
            {
                "path": relative,
                "title": title,
                "aliases": aliases,
                "type": "project",
                "project_key": key,
                "event_count": len(groups[key]),
                "completion_level": completion_state.get("level"),
                "completion_status": completion_state.get("status"),
                "keywords": _keywords(title + "\n" + "\n".join(aliases) + "\n" + documents[relative]),
            }
        )
        published_projects += 1

    document_by_path = {str(item["path"]): item for item in index_documents}
    graph_nodes: list[dict[str, Any]] = [
        {
            "id": f"doc:{path}",
            "kind": "document",
            "path": path,
            "title": item.get("title"),
            "document_type": item.get("type"),
            **({"project_key": item.get("project_key")} if item.get("project_key") else {}),
            **(
                {"completion_level": item.get("completion_level"), "completion_status": item.get("completion_status")}
                if item.get("project_key")
                else {}
            ),
        }
        for path, item in sorted(document_by_path.items())
    ]
    graph_edges: list[dict[str, Any]] = []
    assertion_nodes_by_event: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, project_path in sorted(project_path_by_key.items()):
        project = project_by_key[key]
        for section in HISTORY_SECTIONS:
            for item_index, item in enumerate(project["history"][section]):
                evidence_ids = [str(event_id) for event_id in item.get("evidence_event_ids") or []]
                assertion_id = "assertion:" + sha256(
                    "\x00".join((key, section, str(item_index), str(item.get("text") or ""), *evidence_ids)).encode("utf-8")
                ).hexdigest()[:24]
                graph_nodes.append(
                    {
                        "id": assertion_id,
                        "kind": "project_assertion",
                        "title": str(item.get("text") or "")[:240],
                        "project_key": key,
                        "document_path": project_path,
                        "section": section,
                        "assertion_status": item.get("status"),
                        "evidence_event_ids": evidence_ids,
                    }
                )
                graph_edges.append(
                    {
                        "edge_id": _edge_id("contains-project-assertion", project_path, assertion_id),
                        "source": f"doc:{project_path}",
                        "target": assertion_id,
                        "relation": "contains project assertion",
                        "direction": "directed",
                        "status": "confirmed",
                        "evidence_event_ids": evidence_ids,
                        "rationale": "This reviewed assertion is published in the project history.",
                    }
                )
                for event_id in evidence_ids:
                    assertion_nodes_by_event[(key, event_id)].append(assertion_id)
    claim_by_id = {str(claim["claim_id"]): claim for claim in claims}
    for claim_id, claim in sorted(claim_by_id.items()):
        document_path = _claim_document_path(str(claim["knowledge_type"]))
        claim_node_id = f"claim:{claim_id}"
        graph_nodes.append(
            {
                "id": claim_node_id,
                "kind": "claim",
                "title": str(claim.get("statement") or "")[:240],
                "claim_id": claim_id,
                "claim_status": claim.get("status"),
                "document_path": document_path,
                **({"rule_scope": claim.get("rule_scope"), "derivation": claim.get("derivation")} if claim.get("knowledge_type") in {"collaboration", "expression"} else {}),
            }
        )
        graph_edges.append(
            {
                "edge_id": _edge_id("contains", document_path, claim_id),
                "source": f"doc:{document_path}",
                "target": claim_node_id,
                "relation": "contains claim",
                "direction": "directed",
                "status": "confirmed",
                "evidence_event_ids": list(claim.get("evidence_event_ids") or []),
                "rationale": "The reviewed claim is published in this base document.",
            }
        )
        evidence_by_project: dict[str, list[str]] = defaultdict(list)
        for event_id in claim.get("evidence_event_ids") or []:
            event = event_by_id.get(str(event_id))
            if event is None:
                continue
            key = event.project_key or f"session:{event.logical_session_id}"
            if key in project_path_by_key:
                evidence_by_project[key].append(str(event_id))
        for key, evidence_ids in sorted(evidence_by_project.items()):
            project_path = project_path_by_key[key]
            graph_edges.append(
                {
                    "edge_id": _edge_id("claim-evidence", claim_id, key),
                    "source": f"doc:{project_path}",
                    "target": f"doc:{document_path}",
                    "relation": f"evidence for claim {claim_id}",
                    "direction": "directed",
                    "status": "confirmed",
                    "evidence_event_ids": evidence_ids,
                    "rationale": "The base claim cites reviewed events from this project chain.",
                }
            )
        for target_id in claim.get("conflicts") or []:
            if str(target_id) in claim_by_id:
                graph_edges.append(
                    {
                        "edge_id": _edge_id("conflicts", claim_id, str(target_id)),
                        "source": claim_node_id,
                        "target": f"claim:{target_id}",
                        "relation": "conflicts with",
                        "direction": "symmetric",
                        "status": "confirmed",
                        "evidence_event_ids": list(claim.get("evidence_event_ids") or []),
                        "rationale": "The reviewed claim explicitly records this conflict.",
                    }
                )
        for target_id in claim.get("supersedes") or []:
            if str(target_id) in claim_by_id:
                graph_edges.append(
                    {
                        "edge_id": _edge_id("supersedes", claim_id, str(target_id)),
                        "source": claim_node_id,
                        "target": f"claim:{target_id}",
                        "relation": "supersedes",
                        "direction": "directed",
                        "status": "confirmed",
                        "evidence_event_ids": list(claim.get("evidence_event_ids") or []),
                        "rationale": "The reviewed claim explicitly records this supersession.",
                    }
                )

    feedback_by_id = {str(item["feedback_id"]): item for item in feedback_signals}
    for feedback_id, signal in sorted(feedback_by_id.items()):
        project_key = str(signal["project_key"])
        project_path = project_path_by_key[project_key]
        node_id = f"feedback:{feedback_id}"
        graph_nodes.append(
            {
                "id": node_id,
                "kind": "feedback_signal",
                "title": str(signal.get("statement") or "")[:240],
                "feedback_id": feedback_id,
                "feedback_kind": signal.get("kind"),
                "feedback_object": signal.get("object"),
                "scope": signal.get("scope"),
                "feedback_status": signal.get("status"),
                "project_key": project_key,
                "document_path": project_path,
                "evidence_event_ids": list(signal.get("evidence_event_ids") or []),
            }
        )
        graph_edges.append(
            {
                "edge_id": _edge_id("records-feedback", project_path, feedback_id),
                "source": f"doc:{project_path}",
                "target": node_id,
                "relation": "records feedback signal",
                "direction": "directed",
                "status": "confirmed",
                "evidence_event_ids": list(signal.get("evidence_event_ids") or []),
                "rationale": "The reviewed project chain contains this scoped feedback record.",
            }
        )

    for evolution in sorted(rule_evolutions, key=lambda item: str(item.get("evolution_id") or "")):
        evolution_id = str(evolution["evolution_id"])
        node_id = f"evolution:{evolution_id}"
        graph_nodes.append(
            {
                "id": node_id,
                "kind": "rule_evolution",
                "title": str(evolution.get("proposed_rule") or "")[:240],
                "evolution_id": evolution_id,
                "evolution_status": evolution.get("status"),
                "scope": evolution.get("scope"),
                "rule_version": evolution.get("rule_version"),
                "validation_result": evolution.get("validation_result"),
            }
        )
        for feedback_id in evolution.get("feedback_ids") or []:
            signal = feedback_by_id.get(str(feedback_id))
            if signal is None:
                continue
            graph_edges.append(
                {
                    "edge_id": _edge_id("feedback-supports-evolution", str(feedback_id), evolution_id),
                    "source": f"feedback:{feedback_id}",
                    "target": node_id,
                    "relation": "supports rule evolution",
                    "direction": "directed",
                    "status": "confirmed",
                    "evidence_event_ids": list(signal.get("evidence_event_ids") or []),
                    "rationale": "This scoped feedback signal is explicitly cited by the rule-evolution record.",
                }
            )
        promoted_claim_id = str(evolution.get("promoted_claim_id") or "")
        if evolution.get("status") in {"approved", "validated"} and promoted_claim_id:
            graph_edges.append(
                {
                    "edge_id": _edge_id("evolution-activates-claim", evolution_id, promoted_claim_id),
                    "source": node_id,
                    "target": f"claim:{promoted_claim_id}",
                    "relation": "activates reviewed rule claim",
                    "direction": "directed",
                    "status": "confirmed",
                    "evidence_event_ids": (
                        list(evolution.get("approval_event_ids") or [])
                        + list(evolution.get("baseline_event_ids") or [])
                        + list(evolution.get("validation_event_ids") or [])
                    ),
                    "rationale": "The rule change has the required approval and is linked to its published bounded claim.",
                }
            )

    explicit_relationships = list(review.get("project_relationships") or [])
    relationship_candidates: list[dict[str, Any]] = []
    for relationship in explicit_relationships:
        source_key = str(relationship["source_project_key"])
        target_key = str(relationship["target_project_key"])
        if source_key not in project_path_by_key or target_key not in project_path_by_key:
            relationship_candidates.append({**relationship, "publication": "not-published-project"})
            continue
        edge = {
            "edge_id": str(relationship["relationship_id"]),
            "source": f"doc:{project_path_by_key[source_key]}",
            "target": f"doc:{project_path_by_key[target_key]}",
            "relation": " ".join(str(relationship["relation"]).split()),
            "evidence_basis": list(relationship.get("evidence_basis") or []),
            "direction": relationship["direction"],
            "status": relationship["status"],
            "confidence": relationship["confidence"],
            "evidence_event_ids": list(relationship.get("source_evidence_event_ids") or []) + list(relationship.get("target_evidence_event_ids") or []),
            "rationale": str(relationship.get("rationale") or "").strip(),
            "source_project_key": source_key,
            "target_project_key": target_key,
            "source_anchor_nodes": sorted(
                {
                    node_id
                    for event_id in relationship.get("source_evidence_event_ids") or []
                    for node_id in assertion_nodes_by_event.get((source_key, str(event_id)), [])
                }
            ),
            "target_anchor_nodes": sorted(
                {
                    node_id
                    for event_id in relationship.get("target_evidence_event_ids") or []
                    for node_id in assertion_nodes_by_event.get((target_key, str(event_id)), [])
                }
            ),
        }
        graph_edges.append(edge)
        if edge["status"] != "confirmed":
            relationship_candidates.append({**relationship, "publication": "withheld"})

    node_by_id = {str(node["id"]): node for node in graph_nodes}
    related_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph_edges:
        if edge.get("status") != "confirmed":
            continue
        source_node = node_by_id.get(str(edge.get("source")))
        target_node = node_by_id.get(str(edge.get("target")))
        if not source_node or not target_node or source_node.get("kind") != "document" or target_node.get("kind") != "document":
            continue
        source_path = str(source_node["path"])
        target_path = str(target_node["path"])
        related_by_path[source_path].append(
            {
                "edge_id": edge["edge_id"],
                "target_path": target_path,
                "title": target_node.get("title"),
                "relation": edge.get("relation"),
                "evidence_event_ids": edge.get("evidence_event_ids") or [],
            }
        )
        reverse_relation = edge.get("relation") if edge.get("direction") == "symmetric" else f"reverse of {edge.get('relation')}"
        related_by_path[target_path].append(
            {
                "edge_id": edge["edge_id"],
                "target_path": source_path,
                "title": source_node.get("title"),
                "relation": reverse_relation,
                "evidence_event_ids": edge.get("evidence_event_ids") or [],
            }
        )

    link_audit: list[dict[str, Any]] = []
    for path in sorted(document_by_path):
        item = document_by_path[path]
        isolate_rationale = None
        if item.get("project_key"):
            analysis = project_by_key[str(item["project_key"])].get("link_analysis") or {}
            if analysis.get("status") == "intentional-isolate":
                isolate_rationale = str(analysis.get("rationale") or "").strip()
        documents[path] = _append_related_section(documents[path], path, related_by_path.get(path, []), isolate_rationale)
        link_audit.append(
            {
                "path": path,
                "project_key": item.get("project_key"),
                "confirmed_document_links": len(related_by_path.get(path, [])),
                "intentional_isolate": bool(isolate_rationale),
                "isolate_rationale": isolate_rationale,
            }
        )

    graph = {
        "graph_version": 2,
        "semantic_status": "published",
        "run_id": review["run_id"],
        "nodes": graph_nodes,
        "edges": graph_edges,
    }
    validated_evolutions = sum(item.get("status") == "validated" for item in rule_evolutions)
    approved_evolutions = sum(item.get("status") == "approved" for item in rule_evolutions)
    if validated_evolutions:
        evolution_status = "validated_rule_changes"
    elif approved_evolutions:
        evolution_status = "approved_rule_changes_pending_validation"
    elif feedback_signals:
        evolution_status = "feedback_recorded_no_active_rule_change"
    else:
        evolution_status = "no_feedback_recorded"
    index = {
        "index_version": 4,
        "semantic_status": "published",
        "run_id": review["run_id"],
        "review_event_set_sha256": review["event_set_sha256"],
        "documents": index_documents,
        "graph": {
            "path": "knowledge-graph.json",
            "nodes": len(graph_nodes),
            "edges": len(graph_edges),
            "confirmed_document_edges": sum(
                edge.get("status") == "confirmed"
                and node_by_id.get(str(edge.get("source")), {}).get("kind") == "document"
                and node_by_id.get(str(edge.get("target")), {}).get("kind") == "document"
                for edge in graph_edges
            ),
        },
        "evolution": {
            "status": evolution_status,
            "feedback_signals": len(feedback_signals),
            "candidate_or_rejected_rule_changes": len(rule_evolutions) - approved_evolutions - validated_evolutions,
            "approved_rule_changes_pending_validation": approved_evolutions,
            "validated_rule_changes": validated_evolutions,
        },
    }
    documents["knowledge-graph.json"] = json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    documents["knowledge-index.json"] = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    stale_documents = _archive_stale_project_documents(root, set(project_path_by_key.values()), str(review["run_id"]))
    for relative, content in documents.items():
        _atomic_text(root / "knowledge" / relative, content)
    _jsonl_write(root / "audit" / "semantic-dispositions.jsonl", semantic_rows)
    _json_write(root / "audit" / "claims.json", claims)
    _json_write(root / "audit" / "actor-attributions.json", list(review.get("actor_attributions") or []))
    _json_write(root / "audit" / "feedback-signals.json", feedback_signals)
    _json_write(root / "audit" / "rule-evolutions.json", rule_evolutions)
    _json_write(root / "audit" / "project-membership.json", review.get("project_membership") or {})
    _json_write(root / "audit" / "relationships.json", graph_edges)
    _json_write(root / "audit" / "relationship-candidates.json", relationship_candidates)
    _json_write(root / "audit" / "link-audit.json", link_audit)
    _json_write(root / "audit" / "stale-project-documents.json", stale_documents)
    _json_write(
        root / "audit" / "published-files.json",
        {
            "manifest_version": 1,
            "run_id": review["run_id"],
            "knowledge_paths": sorted(documents),
            "project_paths": sorted(project_path_by_key.values()),
        },
    )
    completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    has_unsupported = not completion.get("gates", {}).get("unsupported_formats_clear")
    completion["status"] = "needs_retrieval_verification"
    completion["retrieval_contract_version"] = 2
    completion["gates"]["semantic_review_complete"] = True
    completion["gates"]["knowledge_graph_complete"] = True
    completion["gates"]["published_knowledge"] = True
    completion["gates"]["retrieval_related_match"] = False
    completion["gates"]["retrieval_unrelated_no_match"] = False
    completion["gates"]["retrieval_related_suite"] = False
    completion["gates"]["retrieval_hard_negative_suite"] = False
    completion["gates"]["unsupported_formats_acknowledged"] = bool(review.get("unsupported_formats_acknowledged"))
    completion["semantic_dispositions"] = len(semantic_rows)
    completion["published_claims"] = len(claims)
    completion["published_projects"] = published_projects
    completion["stale_project_documents_archived"] = sum(item.get("status") == "archived" for item in stale_documents)
    completion["published_project_relationships"] = sum(relationship.get("status") == "confirmed" for relationship in explicit_relationships)
    completion["published_graph_edges"] = sum(edge.get("status") == "confirmed" for edge in graph_edges)
    completion["feedback_signals"] = len(feedback_signals)
    completion["rule_evolutions"] = len(rule_evolutions)
    completion["validated_rule_evolutions"] = validated_evolutions
    completion["evolution_status"] = evolution_status
    completion["project_membership_changed_events"] = len((review.get("project_membership") or {}).get("diff") or [])
    completion["has_unsupported_formats"] = has_unsupported
    completion["reviewer"] = review["reviewer"]
    completion["distilled_at"] = datetime.now(timezone.utc).isoformat()
    completion.pop("completed_at", None)
    completion.pop("retrieval_verified_at", None)
    completion.pop("retrieval_suite_sha256", None)
    completion.pop("retrieval_verification_sha256", None)
    completion.pop("retrieval_profile", None)
    completion.pop("publication_manifest_sha256", None)
    _json_write(root / "audit" / "completion-report.json", completion)
    return {
        "status": completion["status"],
        "run_id": review["run_id"],
        "semantic_dispositions": len(semantic_rows),
        "published_claims": len(claims),
        "published_projects": published_projects,
        "stale_project_documents_archived": completion["stale_project_documents_archived"],
        "published_project_relationships": completion["published_project_relationships"],
        "published_graph_edges": completion["published_graph_edges"],
        "knowledge_root": str((root / "knowledge").resolve()),
    }
