from __future__ import annotations

import json
import os
import tempfile
from contextlib import nullcontext
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .locking import mutation_lock


LIFECYCLE_ACTIONS = {"retract", "forget", "schedule"}
SELECTOR_FIELDS = ("source_file_ids", "event_ids", "claim_ids", "project_keys")
GENERATED_AREAS = ("audit", "review", "knowledge")
GENERATED_SUFFIXES = {".json", ".jsonl", ".md"}
TRANSACTION_RELATIVE = Path("audit/lifecycle-transaction.json")
STAGING_RELATIVE = Path("audit/lifecycle-staging")
BACKUP_WARNING = (
    "This operation changes only the generated knowledge base. It never edits source sessions, "
    "and it cannot erase copies held by backups, sync providers, caches, exports, or other devices."
)


class LifecycleError(ValueError):
    """A fail-closed lifecycle planning or mutation error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _id_hash(value: object) -> str:
    return sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def _event_locator_hash(row: Mapping[str, Any]) -> str:
    return _id_hash(f"{row.get('source_file_id') or ''}\0{row.get('record_locator') or ''}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | datetime | date | None, field: str, *, required: bool = False) -> datetime | None:
    if value is None or value == "":
        if required:
            raise LifecycleError(f"{field} is required")
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        try:
            if len(text) == 10:
                parsed = datetime.combine(date.fromisoformat(text), time.min)
            else:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LifecycleError(f"{field} must be an ISO-8601 date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime | date | None, field: str) -> str | None:
    parsed = _parse_time(value, field)
    return parsed.isoformat() if parsed else None


def _normal_values(value: object) -> list[str]:
    if value is None:
        return []
    values: Iterable[object]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, Sequence):
        values = value
    else:
        values = (value,)
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _selectors(
    selectors: Mapping[str, object] | None,
    *,
    source_file_ids: Sequence[str] | str | None,
    event_ids: Sequence[str] | str | None,
    claim_ids: Sequence[str] | str | None,
    project_keys: Sequence[str] | str | None,
) -> dict[str, list[str]]:
    supplied = dict(selectors or {})
    unknown = sorted(set(supplied) - set(SELECTOR_FIELDS))
    if unknown:
        raise LifecycleError(f"unknown lifecycle selector fields: {', '.join(unknown)}")
    explicit = {
        "source_file_ids": source_file_ids,
        "event_ids": event_ids,
        "claim_ids": claim_ids,
        "project_keys": project_keys,
    }
    result = {field: _normal_values(explicit[field] if explicit[field] is not None else supplied.get(field)) for field in SELECTOR_FIELDS}
    if not any(result.values()):
        raise LifecycleError("at least one source_file_id, event_id, claim_id, or project_key selector is required")
    return result


def _raw_root(kb: Path) -> Path:
    raw = kb.expanduser()
    if raw.is_symlink():
        raise LifecycleError(f"knowledge-base path must not be a symlink: {raw}")
    if raw.name == "knowledge" and (raw.parent / "audit").is_dir():
        raw = raw.parent
    if raw.is_symlink():
        raise LifecycleError(f"knowledge-base path must not be a symlink: {raw}")
    try:
        root = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise LifecycleError(f"knowledge base does not exist: {raw}") from exc
    if not root.is_dir() or not (root / "audit").is_dir() or not (root / "knowledge").is_dir():
        raise LifecycleError(f"not a generated Agent-session knowledge base: {root}")
    return root


def _safe_generated_path(root: Path, relative: Path | str, *, must_exist: bool = False) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts or rel.parts[0] not in GENERATED_AREAS:
        raise LifecycleError(f"unsafe generated path: {rel}")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise LifecycleError(f"generated path contains a symlink: {rel}")
    try:
        current.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise LifecycleError(f"generated path escapes the knowledge base: {rel}") from exc
    if must_exist and not current.is_file():
        raise LifecycleError(f"generated file is missing: {rel}")
    return current


def _generated_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for area in GENERATED_AREAS:
        base = _safe_generated_path(root, area)
        if not base.exists():
            continue
        if not base.is_dir():
            raise LifecycleError(f"generated area is not a directory: {area}")
        for path in base.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink():
                raise LifecycleError(f"generated tree contains a symlink: {relative}")
            if path.is_file() and path.suffix.lower() in GENERATED_SUFFIXES:
                _safe_generated_path(root, relative, must_exist=True)
                files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _state_sha256(root: Path) -> str:
    digest = sha256()
    for path in _generated_files(root):
        relative = path.relative_to(root).as_posix()
        # A transaction journal is private control state rather than source
        # knowledge. Excluding it lets an interrupted, pre-commit staging pass
        # be safely discarded and rebuilt against the original plan hash.
        if relative == TRANSACTION_RELATIVE.as_posix() or relative.startswith(STAGING_RELATIVE.as_posix() + "/"):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha256(path.read_bytes()).digest())
        digest.update(b"\n")
    return digest.hexdigest()


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"invalid generated JSON: {path.name}: {exc}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise LifecycleError(f"generated JSONL row is not an object: {path.name}:{line_number}")
            result.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"invalid generated JSONL: {path.name}: {exc}") from exc
    return result


def _review_files(root: Path) -> list[Path]:
    review_root = _safe_generated_path(root, "review")
    if not review_root.exists():
        return []
    return [path for path in _generated_files(root) if path.parent == review_root and path.suffix.lower() == ".json"]


def _evidence_ids(item: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for key, value in item.items():
        if key == "event_id" and value:
            result.add(str(value))
        elif key.endswith("event_ids") and isinstance(value, list):
            result.update(str(event_id) for event_id in value if event_id)
    return result


def _project_for_event(row: Mapping[str, Any]) -> str:
    value = row.get("project_key")
    if value:
        return str(value)
    logical = row.get("logical_session_id")
    return f"session:{logical}" if logical else ""


def _all_reviews(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    result: list[tuple[Path, dict[str, Any]]] = []
    for path in _review_files(root):
        value = _load_json(path, {})
        if isinstance(value, dict) and ("base_claims" in value or "projects" in value):
            result.append((path, value))
    return result


def _safe_index_documents(root: Path, index: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in index.get("documents") or []:
        if not isinstance(item, dict) or not item.get("path"):
            raise LifecycleError("knowledge index contains an invalid document entry")
        relative = Path("knowledge") / str(item["path"])
        path = _safe_generated_path(root, relative)
        key = relative.relative_to("knowledge").as_posix()
        if key.startswith("archive/"):
            raise LifecycleError(f"published index points into private archive: {key}")
        result[key] = item
        if path.exists() and not path.is_file():
            raise LifecycleError(f"indexed knowledge path is not a regular file: {key}")
    graph_meta = index.get("graph") if isinstance(index.get("graph"), dict) else {}
    graph_relative = Path("knowledge") / str(graph_meta.get("path") or "knowledge-graph.json")
    _safe_generated_path(root, graph_relative)
    return result


def _records_by_id(records: Iterable[Mapping[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        value = record.get(field)
        if value:
            result[str(value)] = dict(record)
    return result


def _build_context(root: Path, selectors: dict[str, list[str]], action: str) -> dict[str, Any]:
    events = _load_jsonl(_safe_generated_path(root, "audit/events.jsonl"))
    audit_claims = _load_json(_safe_generated_path(root, "audit/claims.json"), [])
    feedback = _load_json(_safe_generated_path(root, "audit/feedback-signals.json"), [])
    evolutions = _load_json(_safe_generated_path(root, "audit/rule-evolutions.json"), [])
    relationships = _load_json(_safe_generated_path(root, "audit/relationships.json"), [])
    index = _load_json(_safe_generated_path(root, "knowledge/knowledge-index.json"), {})
    graph_relative = Path("knowledge") / str(
        ((index.get("graph") or {}).get("path") if isinstance(index, dict) and isinstance(index.get("graph"), dict) else None)
        or "knowledge-graph.json"
    )
    graph_path = _safe_generated_path(root, graph_relative)
    graph = _load_json(graph_path, {})
    reviews = _all_reviews(root)
    indexed_documents = _safe_index_documents(root, index if isinstance(index, dict) else {})

    claim_records = list(audit_claims) if isinstance(audit_claims, list) else []
    feedback_records = list(feedback) if isinstance(feedback, list) else []
    evolution_records = list(evolutions) if isinstance(evolutions, list) else []
    for _, review in reviews:
        claim_records.extend(item for item in review.get("base_claims") or [] if isinstance(item, dict))
        feedback_records.extend(item for item in review.get("feedback_signals") or [] if isinstance(item, dict))
        evolution_records.extend(item for item in review.get("rule_evolutions") or [] if isinstance(item, dict))
    claims_by_id = _records_by_id(claim_records, "claim_id")
    feedback_by_id = _records_by_id(feedback_records, "feedback_id")
    evolutions_by_id = _records_by_id(evolution_records, "evolution_id")

    wanted_sources = set(selectors["source_file_ids"])
    wanted_events = set(selectors["event_ids"])
    wanted_claims = set(selectors["claim_ids"])
    wanted_projects = set(selectors["project_keys"])
    event_by_id = {str(row.get("event_id")): row for row in events if row.get("event_id")}
    selected_event_ids = {
        str(row["event_id"])
        for row in events
        if row.get("event_id")
        and (
            str(row.get("source_file_id") or "") in wanted_sources
            or str(row.get("event_id")) in wanted_events
            or _project_for_event(row) in wanted_projects
        )
    }
    selected_project_keys = set(wanted_projects)
    selected_project_keys.update(_project_for_event(event_by_id[event_id]) for event_id in selected_event_ids if event_id in event_by_id)
    selected_project_keys.discard("")
    direct_project_keys = set(selected_project_keys)

    selected_claim_ids = set(wanted_claims & set(claims_by_id))
    for claim_id, claim in claims_by_id.items():
        evidence = _evidence_ids(claim)
        if evidence & selected_event_ids:
            selected_claim_ids.add(claim_id)
    # A claim lifecycle change also affects the project chains that supplied
    # its provenance, even when those event bodies remain intact (retract).
    for claim_id in selected_claim_ids:
        selected_project_keys.update(
            _project_for_event(event_by_id[event_id])
            for event_id in _evidence_ids(claims_by_id[claim_id])
            if event_id in event_by_id
        )
    selected_project_keys.discard("")
    if action == "forget":
        for claim_id in tuple(selected_claim_ids):
            selected_event_ids.update(_evidence_ids(claims_by_id[claim_id]))
        selected_project_keys.update(
            _project_for_event(event_by_id[event_id]) for event_id in selected_event_ids if event_id in event_by_id
        )
        selected_project_keys.discard("")
        direct_project_keys.update(
            _project_for_event(event_by_id[event_id]) for event_id in selected_event_ids if event_id in event_by_id
        )
        direct_project_keys.discard("")

    selected_feedback_ids: set[str] = set()
    for feedback_id, item in feedback_by_id.items():
        if _evidence_ids(item) & selected_event_ids or str(item.get("project_key") or "") in direct_project_keys:
            selected_feedback_ids.add(feedback_id)
    selected_rule_ids: set[str] = set()
    for evolution_id, item in evolutions_by_id.items():
        feedback_ids = {str(value) for value in item.get("feedback_ids") or []}
        if str(item.get("promoted_claim_id") or "") in selected_claim_ids or feedback_ids & selected_feedback_ids or _evidence_ids(item) & selected_event_ids:
            selected_rule_ids.add(evolution_id)

    project_doc_paths = {
        path
        for path, item in indexed_documents.items()
        if str(item.get("project_key") or "") in direct_project_keys
    }
    base_doc_paths: set[str] = set()
    for claim_id in selected_claim_ids:
        kind = str(claims_by_id.get(claim_id, {}).get("knowledge_type") or "")
        if kind == "evidence_rule":
            base_doc_paths.add("00-evidence-rules.md")
        elif kind in {"identity", "direction"}:
            base_doc_paths.add("01-identity-and-current-direction.md")
        elif kind in {"collaboration", "expression"}:
            base_doc_paths.add("02-collaboration-and-expression.md")
    if selected_rule_ids:
        base_doc_paths.add("02-collaboration-and-expression.md")

    graph_nodes = list(graph.get("nodes") or []) if isinstance(graph, dict) else []
    graph_edges = list(graph.get("edges") or []) if isinstance(graph, dict) else []
    selected_node_ids: set[str] = set()
    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        doc_path = str(node.get("path") or node.get("document_path") or "")
        if (
            str(node.get("claim_id") or "") in selected_claim_ids
            or str(node.get("feedback_id") or "") in selected_feedback_ids
            or str(node.get("evolution_id") or "") in selected_rule_ids
            or str(node.get("project_key") or "") in direct_project_keys
            or doc_path in project_doc_paths
            or _evidence_ids(node) & selected_event_ids
        ):
            selected_node_ids.add(node_id)
    selected_edge_ids: set[str] = set()
    for edge in graph_edges:
        if not isinstance(edge, dict):
            continue
        if (
            str(edge.get("source") or "") in selected_node_ids
            or str(edge.get("target") or "") in selected_node_ids
            or str(edge.get("source_project_key") or "") in direct_project_keys
            or str(edge.get("target_project_key") or "") in direct_project_keys
            or _evidence_ids(edge) & selected_event_ids
        ):
            if edge.get("edge_id"):
                selected_edge_ids.add(str(edge["edge_id"]))

    review_project_keys = {
        str(project.get("project_key") or "")
        for _, review in reviews
        for project in review.get("projects") or []
        if isinstance(project, dict) and project.get("project_key")
    }
    matched = {
        "source_file_ids": sorted({str(row.get("source_file_id")) for row in events if str(row.get("source_file_id") or "") in wanted_sources}),
        "event_ids": sorted(wanted_events & set(event_by_id)),
        "claim_ids": sorted(wanted_claims & set(claims_by_id)),
        "project_keys": sorted(
            wanted_projects
            & (
                {_project_for_event(row) for row in events}
                | {str(item.get("project_key") or "") for item in indexed_documents.values()}
                | review_project_keys
            )
        ),
    }
    unmatched = {
        field: sorted(set(selectors[field]) - set(matched[field]))
        for field in SELECTOR_FIELDS
        if set(selectors[field]) - set(matched[field])
    }
    if not any(matched.values()):
        raise LifecycleError("lifecycle selectors did not match generated knowledge")

    affected_docs = sorted(project_doc_paths | base_doc_paths)
    return {
        "events": events,
        "claims": list(audit_claims) if isinstance(audit_claims, list) else [],
        "feedback": list(feedback) if isinstance(feedback, list) else [],
        "evolutions": list(evolutions) if isinstance(evolutions, list) else [],
        "relationships": list(relationships) if isinstance(relationships, list) else [],
        "index": index if isinstance(index, dict) else {},
        "graph": graph if isinstance(graph, dict) else {},
        "graph_path": graph_path,
        "reviews": reviews,
        "indexed_documents": indexed_documents,
        "event_by_id": event_by_id,
        "claims_by_id": claims_by_id,
        "feedback_by_id": feedback_by_id,
        "evolutions_by_id": evolutions_by_id,
        "matched": matched,
        "unmatched": unmatched,
        "selected_event_ids": selected_event_ids,
        "selected_claim_ids": selected_claim_ids,
        "selected_project_keys": selected_project_keys,
        "direct_project_keys": direct_project_keys,
        "selected_feedback_ids": selected_feedback_ids,
        "selected_rule_ids": selected_rule_ids,
        "selected_node_ids": selected_node_ids,
        "selected_edge_ids": selected_edge_ids,
        "affected_docs": affected_docs,
        "project_doc_paths": project_doc_paths,
    }


def plan_lifecycle(
    kb: Path,
    action: str,
    selectors: Mapping[str, object] | None = None,
    *,
    source_file_ids: Sequence[str] | str | None = None,
    event_ids: Sequence[str] | str | None = None,
    claim_ids: Sequence[str] | str | None = None,
    project_keys: Sequence[str] | str | None = None,
    reason: str = "",
    requested_at: str | datetime | date | None = None,
    observed_at: str | datetime | date | None = None,
    checked_at: str | datetime | date | None = None,
    recheck_after: str | datetime | date | None = None,
    retain_until: str | datetime | date | None = None,
    retention_until: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Create a content-free, state-bound plan. Planning is always read-only."""

    normalized_action = str(action).strip().lower()
    if normalized_action not in LIFECYCLE_ACTIONS:
        raise LifecycleError(f"unsupported lifecycle action: {action}")
    normalized = _selectors(
        selectors,
        source_file_ids=source_file_ids,
        event_ids=event_ids,
        claim_ids=claim_ids,
        project_keys=project_keys,
    )
    if retain_until is not None and retention_until is not None:
        raise LifecycleError("use only one of retain_until or retention_until")
    schedule = {
        "observed_at": _iso(observed_at, "observed_at"),
        "checked_at": _iso(checked_at, "checked_at"),
        "recheck_after": _iso(recheck_after, "recheck_after"),
        "retain_until": _iso(retain_until if retain_until is not None else retention_until, "retain_until"),
    }
    if normalized_action == "schedule" and not any(schedule.values()):
        raise LifecycleError("schedule requires observed_at, checked_at, recheck_after, or retain_until")
    root = _raw_root(kb)
    pending = _safe_generated_path(root, TRANSACTION_RELATIVE)
    if pending.exists():
        raise LifecycleError("a lifecycle transaction is pending; resume it with its original plan before creating another plan")
    state = _state_sha256(root)
    context = _build_context(root, normalized, normalized_action)
    impact = {
        "documents": context["affected_docs"],
        "sources": context["matched"]["source_file_ids"],
        "claims": sorted(context["selected_claim_ids"]),
        "edges": sorted(context["selected_edge_ids"]),
        "rules": sorted(context["selected_rule_ids"]),
        "projects": sorted(context["selected_project_keys"]),
        "events": sorted(context["selected_event_ids"]),
        "feedback_signals": sorted(context["selected_feedback_ids"]),
        "requires_redistill": normalized_action in {"retract", "forget"},
        "requires_retrieval_gates": normalized_action in {"retract", "forget"},
    }
    plan: dict[str, Any] = {
        "plan_version": 1,
        "action": normalized_action,
        "dry_run": True,
        "root_state_sha256": state,
        "selector_sha256": _digest(normalized),
        "selectors": normalized,
        "matched_selectors": context["matched"],
        "unmatched_selectors": context["unmatched"],
        "requested_at": _iso(requested_at, "requested_at") or _utc_now(),
        "reason_sha256": _id_hash(reason) if reason else None,
        "schedule": schedule,
        "impact": impact,
        "source_sessions_mutated": False,
        "warnings": [BACKUP_WARNING],
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8") + b"\n"


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical(row) + b"\n" for row in rows)


def _transaction_path(root: Path) -> Path:
    return _safe_generated_path(root, TRANSACTION_RELATIVE)


def _stage_root(root: Path, plan_sha256: str) -> Path:
    safe_digest = str(plan_sha256)
    if len(safe_digest) != 64 or any(character not in "0123456789abcdef" for character in safe_digest):
        raise LifecycleError("lifecycle plan hash is not a canonical SHA-256 value")
    return _safe_generated_path(root, STAGING_RELATIVE / safe_digest)


def _write_journal(root: Path, value: Mapping[str, Any]) -> None:
    _atomic_bytes(_transaction_path(root), _json_bytes(dict(value)))


def _cleanup_staging(root: Path, staging_root: Path) -> None:
    expected_parent = _safe_generated_path(root, STAGING_RELATIVE)
    _safe_generated_path(root, staging_root.relative_to(root))
    if staging_root.exists():
        paths = sorted(staging_root.rglob("*"), key=lambda path: len(path.parts), reverse=True)
        for path in paths:
            _safe_generated_path(root, path.relative_to(root))
            if path.is_symlink():
                raise LifecycleError(f"lifecycle staging contains a symlink: {path.relative_to(root)}")
            if path.is_file():
                if path.suffix != ".stage":
                    raise LifecycleError(f"unexpected lifecycle staging file: {path.relative_to(root)}")
                path.unlink()
            elif path.is_dir():
                path.rmdir()
            else:
                raise LifecycleError(f"unexpected lifecycle staging entry: {path.relative_to(root)}")
        staging_root.rmdir()
    if expected_parent.is_dir() and not any(expected_parent.iterdir()):
        expected_parent.rmdir()


def _discard_preparing_transaction(root: Path, journal: Mapping[str, Any]) -> None:
    staging = _stage_root(root, str(journal.get("plan_sha256") or ""))
    _cleanup_staging(root, staging)
    path = _transaction_path(root)
    if path.is_file():
        path.unlink()


def _stage_transaction(
    root: Path,
    plan: Mapping[str, Any],
    writes: Mapping[Path, bytes],
    deletes: set[Path],
    *,
    tombstone_id: str,
    forbidden_literals: set[str] | None = None,
) -> dict[str, Any]:
    plan_sha256 = str(plan["plan_sha256"])
    staging_root = _stage_root(root, plan_sha256)
    transaction_path = _transaction_path(root)
    if transaction_path.exists():
        raise LifecycleError("a lifecycle transaction already exists")
    if staging_root.exists():
        if not staging_root.is_dir() or any(staging_root.iterdir()):
            raise LifecycleError("orphaned lifecycle staging exists; inspect it before retrying")
        staging_root.rmdir()
    staging_parent = _safe_generated_path(root, STAGING_RELATIVE)
    if staging_parent.exists() and not staging_parent.is_dir():
        raise LifecycleError("lifecycle staging parent is not a directory")
    staging_root.mkdir(parents=True, exist_ok=False)
    journal: dict[str, Any] = {
        "transaction_version": 1,
        "plan_sha256": plan_sha256,
        "action": plan["action"],
        "phase": "preparing",
        "created_at": _utc_now(),
        "root_state_sha256": plan["root_state_sha256"],
        "staging_relative": staging_root.relative_to(root).as_posix(),
        "tombstone_id": tombstone_id,
        "writes": [],
        "deletes": [],
        "source_sessions_mutated": False,
    }
    _write_journal(root, journal)
    entries: list[dict[str, Any]] = []
    used_stage_names: set[str] = set()
    for target, content in sorted(writes.items(), key=lambda item: item[0].relative_to(root).as_posix()):
        target = _safe_generated_path(root, target.relative_to(root))
        relative = target.relative_to(root).as_posix()
        if relative == TRANSACTION_RELATIVE.as_posix() or relative.startswith(STAGING_RELATIVE.as_posix() + "/"):
            raise LifecycleError(f"transaction cannot replace its own private control files: {relative}")
        if forbidden_literals and any(value and value.encode("utf-8") in content for value in forbidden_literals):
            raise LifecycleError(f"forget staging still contains a selected private body: {relative}")
        stage_name = _id_hash(relative)[:40] + ".stage"
        if stage_name in used_stage_names:
            raise LifecycleError("lifecycle staging name collision")
        used_stage_names.add(stage_name)
        stage_path = _safe_generated_path(root, staging_root.relative_to(root) / stage_name)
        _atomic_bytes(stage_path, content)
        entries.append(
            {
                "target": relative,
                "stage": stage_path.relative_to(root).as_posix(),
                "size": len(content),
                "sha256": sha256(content).hexdigest(),
            }
        )
    delete_values: list[str] = []
    for target in sorted(deletes, key=lambda item: item.relative_to(root).as_posix()):
        safe_target = _safe_generated_path(root, target.relative_to(root), must_exist=True)
        delete_values.append(safe_target.relative_to(root).as_posix())
    completion_relative = "audit/completion-report.json"
    if completion_relative not in {str(item["target"]) for item in entries}:
        raise LifecycleError("lifecycle transaction must stage a final completion report")
    journal["writes"] = entries
    journal["deletes"] = delete_values
    journal["phase"] = "staged"
    journal["staged_at"] = _utc_now()
    _write_journal(root, journal)
    return journal


def _load_pending_transaction(root: Path) -> dict[str, Any] | None:
    path = _transaction_path(root)
    if not path.exists():
        return None
    if not path.is_file():
        raise LifecycleError("lifecycle transaction journal is not a regular file")
    journal = _load_json(path, {})
    if not isinstance(journal, dict) or journal.get("transaction_version") != 1:
        raise LifecycleError("lifecycle transaction journal is invalid")
    return journal


def _validated_staged_entries(root: Path, journal: Mapping[str, Any]) -> list[tuple[Path, Path, bytes]]:
    staging_root = _stage_root(root, str(journal.get("plan_sha256") or ""))
    expected_staging = str(journal.get("staging_relative") or "")
    if expected_staging != staging_root.relative_to(root).as_posix() or not staging_root.is_dir():
        raise LifecycleError("lifecycle transaction staging directory is missing or mismatched")
    entries: list[tuple[Path, Path, bytes]] = []
    seen_targets: set[str] = set()
    for item in journal.get("writes") or []:
        if not isinstance(item, dict):
            raise LifecycleError("lifecycle transaction contains an invalid write entry")
        target_value = str(item.get("target") or "")
        stage_value = str(item.get("stage") or "")
        if target_value in seen_targets:
            raise LifecycleError(f"lifecycle transaction repeats a target: {target_value}")
        seen_targets.add(target_value)
        target = _safe_generated_path(root, target_value)
        stage = _safe_generated_path(root, stage_value, must_exist=True)
        try:
            stage.relative_to(staging_root)
        except ValueError as exc:
            raise LifecycleError("lifecycle staged file escapes its plan directory") from exc
        content = stage.read_bytes()
        expected_size = item.get("size")
        if not isinstance(expected_size, int) or len(content) != expected_size or sha256(content).hexdigest() != str(item.get("sha256") or ""):
            raise LifecycleError(f"lifecycle staged file failed integrity validation: {stage.name}")
        entries.append((target, stage, content))
    if "audit/completion-report.json" not in seen_targets:
        raise LifecycleError("lifecycle transaction has no final completion report")
    return entries


def _fail_closed_completion(root: Path, plan_sha256: str) -> bytes:
    completion_path = _safe_generated_path(root, "audit/completion-report.json")
    completion = _load_json(completion_path, {})
    if not isinstance(completion, dict):
        completion = {}
    completion["status"] = "lifecycle-applying"
    completion["lifecycle_plan_sha256"] = plan_sha256
    completion["lifecycle_transaction_pending"] = True
    gates = completion.setdefault("gates", {})
    for gate in (
        "semantic_review_complete",
        "knowledge_graph_complete",
        "published_knowledge",
        "retrieval_related_match",
        "retrieval_unrelated_no_match",
        "retrieval_related_suite",
        "retrieval_hard_negative_suite",
    ):
        gates[gate] = False
    completion.pop("completed_at", None)
    completion.pop("retrieval_verified_at", None)
    completion.pop("retrieval_suite_sha256", None)
    completion.pop("retrieval_verification_sha256", None)
    completion.pop("retrieval_profile", None)
    completion.pop("publication_manifest_sha256", None)
    return _json_bytes(completion)


def _transaction_result(plan: Mapping[str, Any], journal: Mapping[str, Any], *, recovered: bool) -> dict[str, Any]:
    return {
        "status": "applied",
        "action": plan["action"],
        "dry_run": False,
        "plan_sha256": plan["plan_sha256"],
        "impact": plan["impact"],
        "source_sessions_mutated": False,
        "backup_erasure_guaranteed": False,
        "warnings": [BACKUP_WARNING],
        "tombstone_id": journal["tombstone_id"],
        "transaction_recovered": recovered,
    }


def _finish_committed_transaction(root: Path, plan: Mapping[str, Any], journal: dict[str, Any]) -> dict[str, Any]:
    # A crash during cleanup may leave a committed journal with only some (or
    # none) of its stage files. Validate live targets against journal hashes;
    # do not require the disposable stage copies to still exist.
    for item in journal.get("writes") or []:
        if not isinstance(item, dict):
            raise LifecycleError("committed lifecycle transaction contains an invalid write entry")
        target = _safe_generated_path(root, str(item.get("target") or ""), must_exist=True)
        content = target.read_bytes()
        if len(content) != item.get("size") or sha256(content).hexdigest() != str(item.get("sha256") or ""):
            completion_target = _safe_generated_path(root, "audit/completion-report.json")
            _atomic_bytes(completion_target, _fail_closed_completion(root, str(plan["plan_sha256"])))
            raise LifecycleError("committed lifecycle targets changed before transaction cleanup")
    for value in journal.get("deletes") or []:
        target = _safe_generated_path(root, str(value))
        if target.exists():
            completion_target = _safe_generated_path(root, "audit/completion-report.json")
            _atomic_bytes(completion_target, _fail_closed_completion(root, str(plan["plan_sha256"])))
            raise LifecycleError("committed lifecycle deletion is incomplete")
    staging_root = _stage_root(root, str(plan["plan_sha256"]))
    _cleanup_staging(root, staging_root)
    transaction_path = _transaction_path(root)
    if transaction_path.is_file():
        transaction_path.unlink()
    return _transaction_result(plan, journal, recovered=True)


def _resume_transaction(root: Path, plan: Mapping[str, Any], journal: dict[str, Any]) -> dict[str, Any]:
    if str(journal.get("plan_sha256") or "") != str(plan.get("plan_sha256") or ""):
        raise LifecycleError("another lifecycle plan is pending; resume it with the exact original plan")
    if str(journal.get("action") or "") != str(plan.get("action") or ""):
        raise LifecycleError("pending lifecycle transaction action does not match the plan")
    phase = str(journal.get("phase") or "")
    if phase == "preparing":
        _discard_preparing_transaction(root, journal)
        raise LifecycleError("incomplete lifecycle staging was discarded; retry the same plan")
    if phase not in {"staged", "applying", "committed"}:
        raise LifecycleError(f"unsupported lifecycle transaction phase: {phase}")
    if phase == "committed":
        return _finish_committed_transaction(root, plan, journal)
    entries = _validated_staged_entries(root, journal)
    completion_target = _safe_generated_path(root, "audit/completion-report.json")
    tombstone_target = _safe_generated_path(root, "audit/lifecycle-tombstones.jsonl")

    # This is the first write to live generated knowledge. If it fails, no
    # content target has changed. Once it succeeds, Reader publication gates
    # remain false until the final staged completion report is written last.
    _atomic_bytes(completion_target, _fail_closed_completion(root, str(plan["plan_sha256"])))
    journal["phase"] = "applying"
    journal["applying_at"] = journal.get("applying_at") or _utc_now()
    _write_journal(root, journal)

    by_target = {target: (stage, content) for target, stage, content in entries}
    if tombstone_target not in by_target:
        raise LifecycleError("lifecycle transaction must commit its tombstone before content changes")
    # Tombstone first: after this point a rebuild can suppress forgotten ids,
    # even if a later target write is interrupted.
    _atomic_bytes(tombstone_target, by_target[tombstone_target][1])
    for target in sorted(by_target, key=lambda path: path.relative_to(root).as_posix()):
        if target in {completion_target, tombstone_target}:
            continue
        _atomic_bytes(target, by_target[target][1])
    for value in journal.get("deletes") or []:
        target = _safe_generated_path(root, str(value))
        if target.exists():
            if not target.is_file():
                raise LifecycleError(f"lifecycle delete target is not a regular file: {value}")
            target.unlink()

    # Final completion is deliberately last. Schedule restores the prior
    # completion; retract/forget leave it at needs_redistill. Either way no
    # Reader can observe a final status while earlier targets are partial.
    _atomic_bytes(completion_target, by_target[completion_target][1])
    journal["phase"] = "committed"
    journal["committed_at"] = _utc_now()
    _write_journal(root, journal)

    staging_root = _stage_root(root, str(plan["plan_sha256"]))
    _cleanup_staging(root, staging_root)
    transaction_path = _transaction_path(root)
    if transaction_path.is_file():
        transaction_path.unlink()
    return _transaction_result(plan, journal, recovered=phase == "applying")


def _content_literals(context: Mapping[str, Any]) -> set[str]:
    literals: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str):
            cleaned = value.strip()
            if len(cleaned) >= 2:
                literals.add(cleaned)
        elif isinstance(value, list):
            for item in value:
                add(item)
        elif isinstance(value, dict):
            for item in value.values():
                add(item)

    for event_id in context["selected_event_ids"]:
        row = context["event_by_id"].get(event_id) or {}
        for key in ("content", "session_title", "working_dir", "project_label", "source_relpath", "metadata"):
            add(row.get(key))
    claim_sets = [context["claims"]] + [review.get("base_claims") or [] for _, review in context["reviews"]]
    for records in claim_sets:
        for claim in records:
            if str(claim.get("claim_id") or "") not in context["selected_claim_ids"]:
                continue
            for key in ("statement", "applies_to", "rationale", "before", "after"):
                add(claim.get(key))
    feedback_sets = [context["feedback"]] + [review.get("feedback_signals") or [] for _, review in context["reviews"]]
    for records in feedback_sets:
        for item in records:
            if str(item.get("feedback_id") or "") not in context["selected_feedback_ids"]:
                continue
            for key in ("statement", "applies_to", "rationale"):
                add(item.get(key))
    evolution_sets = [context["evolutions"]] + [review.get("rule_evolutions") or [] for _, review in context["reviews"]]
    for records in evolution_sets:
        for item in records:
            if str(item.get("evolution_id") or "") not in context["selected_rule_ids"]:
                continue
            for key in ("before_rule", "proposed_rule", "rationale", "expected_behavior_change", "observed_behavior_change"):
                add(item.get(key))
    for _, review in context["reviews"]:
        for project in review.get("projects") or []:
            if str(project.get("project_key") or "") not in context["direct_project_keys"]:
                continue
            for key in ("title", "aliases", "rationale", "history", "link_analysis", "completion", "event_exceptions"):
                add(project.get(key))
        for relationship in review.get("project_relationships") or []:
            if (
                str(relationship.get("source_project_key") or "") in context["direct_project_keys"]
                or str(relationship.get("target_project_key") or "") in context["direct_project_keys"]
                or _evidence_ids(relationship) & context["selected_event_ids"]
            ):
                add(relationship.get("rationale"))
    for node in context["graph"].get("nodes") or []:
        if str(node.get("id") or "") in context["selected_node_ids"]:
            add(node.get("title"))
    identifiers = {
        value
        for field in SELECTOR_FIELDS
        for value in context["matched"].get(field, [])
    }
    return {value for value in literals if value not in identifiers}


def _scrub_string(value: str, literals: set[str]) -> str:
    result = value
    for literal in sorted(literals, key=lambda item: (-len(item), item)):
        result = result.replace(literal, "[FORGOTTEN]")
    return result


def _scrub_value(value: Any, literals: set[str]) -> Any:
    if isinstance(value, str):
        return _scrub_string(value, literals)
    if isinstance(value, list):
        return [_scrub_value(item, literals) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_value(item, literals) for key, item in value.items()}
    return value


def _without_lifecycle_sections(text: str, claim_ids: set[str], rule_ids: set[str]) -> str:
    removed_headings = set(claim_ids) | set(rule_ids)
    if not removed_headings:
        return text
    lines = text.splitlines()
    result: list[str] = []
    skip_level: int | None = None
    for line in lines:
        stripped = line.lstrip()
        level = len(stripped) - len(stripped.lstrip("#"))
        is_heading = level > 0 and len(stripped) > level and stripped[level] == " "
        if is_heading:
            heading = stripped[level + 1 :].strip()
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if heading in removed_headings:
                skip_level = level
        if skip_level is None:
            result.append(line)
    return "\n".join(result).rstrip() + "\n"


def _invalidate_related_section(text: str) -> str:
    marker = "\n## Related knowledge\n"
    prefix = text.split(marker, 1)[0].rstrip()
    return prefix + "\n\n## Related knowledge\n\nLifecycle change pending redistillation and retrieval verification.\n"


def _hash_list(values: Iterable[str]) -> list[str]:
    return sorted(_id_hash(value) for value in values)


def _tombstone(plan: Mapping[str, Any], context: Mapping[str, Any], applied_at: str) -> dict[str, Any]:
    impact = plan["impact"]
    minimal = plan["action"] == "forget"
    record: dict[str, Any] = {
        "tombstone_version": 1,
        "tombstone_id": "tombstone-" + _digest((plan["plan_sha256"], applied_at))[:24],
        "action": plan["action"],
        "applied_at": applied_at,
        "selector_sha256": plan["selector_sha256"],
        "reason_sha256": plan.get("reason_sha256"),
        "source_sessions_mutated": False,
        "backup_erasure_guaranteed": False,
        "affected_counts": {key: len(value) for key, value in impact.items() if isinstance(value, list)},
    }
    if minimal:
        record["affected_identifier_sha256"] = {
            "sources": _hash_list(impact["sources"]),
            "documents": _hash_list(impact["documents"]),
            "claims": _hash_list(impact["claims"]),
            "edges": _hash_list(impact["edges"]),
            "rules": _hash_list(impact["rules"]),
            # Only an explicit project selector means "forget this whole
            # project forever". Event/claim selectors still derive affected
            # projects for deindexing and re-review, but must not silently
            # widen into a project-wide ingestion tombstone.
            "projects": _hash_list(context["matched"].get("project_keys", [])),
            "events": _hash_list(impact["events"]),
            "event_locators": sorted(
                {
                    _event_locator_hash(context["event_by_id"][event_id])
                    for event_id in context["selected_event_ids"]
                    if event_id in context["event_by_id"]
                }
            ),
        }
    else:
        record["selectors"] = plan["matched_selectors"]
        record["affected"] = {
            "sources": impact["sources"],
            "documents": impact["documents"],
            "claims": impact["claims"],
            "edges": impact["edges"],
            "rules": impact["rules"],
            "projects": impact["projects"],
            "events": impact["events"],
        }
    return record


def _update_review(review: dict[str, Any], context: Mapping[str, Any], action: str, schedule: Mapping[str, Any]) -> dict[str, Any]:
    selected_claims = context["selected_claim_ids"]
    selected_events = context["selected_event_ids"]
    selected_projects = context["direct_project_keys"]
    selected_feedback = context["selected_feedback_ids"]
    selected_rules = context["selected_rule_ids"]
    updated = json.loads(json.dumps(review, ensure_ascii=False, default=str))

    claims: list[dict[str, Any]] = []
    for claim in updated.get("base_claims") or []:
        if str(claim.get("claim_id") or "") in selected_claims:
            if action == "forget":
                continue
            if action == "retract":
                claim["status"] = "retracted"
            elif action == "schedule":
                claim.update({key: value for key, value in schedule.items() if value is not None})
        claims.append(claim)
    if "base_claims" in updated:
        updated["base_claims"] = claims

    feedback: list[dict[str, Any]] = []
    for item in updated.get("feedback_signals") or []:
        if str(item.get("feedback_id") or "") in selected_feedback:
            if action == "forget":
                continue
            if action == "retract":
                item["status"] = "retracted"
        feedback.append(item)
    if "feedback_signals" in updated:
        updated["feedback_signals"] = feedback

    evolutions: list[dict[str, Any]] = []
    for item in updated.get("rule_evolutions") or []:
        if str(item.get("evolution_id") or "") in selected_rules:
            if action == "forget":
                continue
            if action == "retract":
                item["status"] = "rejected"
                item["validation_result"] = "failed"
        evolutions.append(item)
    if "rule_evolutions" in updated:
        updated["rule_evolutions"] = evolutions

    attributions: list[dict[str, Any]] = []
    for item in updated.get("actor_attributions") or []:
        if action == "forget" and (_evidence_ids(item) & selected_events or str(item.get("project_key") or "") in selected_projects):
            continue
        attributions.append(item)
    if "actor_attributions" in updated:
        updated["actor_attributions"] = attributions

    project_relationships: list[dict[str, Any]] = []
    for item in updated.get("project_relationships") or []:
        affected = (
            str(item.get("source_project_key") or "") in selected_projects
            or str(item.get("target_project_key") or "") in selected_projects
            or bool(_evidence_ids(item) & selected_events)
        )
        if affected and action == "forget":
            continue
        if affected and action == "retract":
            item["status"] = "rejected"
        project_relationships.append(item)
    if "project_relationships" in updated:
        updated["project_relationships"] = project_relationships

    for project in updated.get("projects") or []:
        project_key = str(project.get("project_key") or "")
        selected_project = project_key in selected_projects
        if action == "forget" and selected_project:
            project["title"] = "Forgotten project " + _id_hash(project_key)[:12]
            project["aliases"] = []
            project["semantic_status"] = "reviewed-no-knowledge"
            project["rationale"] = "Generated knowledge removed by an explicit lifecycle request."
            project["event_exceptions"] = [
                item for item in project.get("event_exceptions") or [] if str(item.get("event_id") or "") not in selected_events
            ]
            project["history"] = {key: [] for key in (project.get("history") or {})}
            project["link_analysis"] = {"status": "intentional-isolate", "rationale": "Lifecycle forget request."}
            project["completion"] = {"level": "not-published", "status": "observed", "evidence_event_ids": [], "rationale": "Forgotten."}
        elif action == "forget":
            for section, items in (project.get("history") or {}).items():
                project["history"][section] = [item for item in items or [] if not (_evidence_ids(item) & selected_events)]
            project["event_exceptions"] = [
                item for item in project.get("event_exceptions") or [] if str(item.get("event_id") or "") not in selected_events
            ]
        elif action == "retract" and selected_project:
            project["semantic_status"] = "reviewed-no-knowledge"
            for items in (project.get("history") or {}).values():
                for item in items or []:
                    item["status"] = "retracted"
            project["link_analysis"] = {"status": "intentional-isolate", "rationale": "Lifecycle retraction."}
    return updated


def _policy_rows(root: Path) -> list[dict[str, Any]]:
    value = _load_json(_safe_generated_path(root, "audit/lifecycle-policies.json"), [])
    return list(value) if isinstance(value, list) else []


def _schedule_policies(root: Path, plan: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    policies = _policy_rows(root)
    selector = plan["matched_selectors"]
    policy_id = "policy-" + _digest(selector)[:24]
    current = next((item for item in policies if item.get("policy_id") == policy_id), None)
    payload = {
        "policy_version": 1,
        "policy_id": policy_id,
        "selectors": selector,
        **{key: value for key, value in plan["schedule"].items() if value is not None},
        "updated_at": _utc_now(),
        "status": "active",
    }
    if current is None:
        policies.append(payload)
    else:
        current.update(payload)
    return sorted(policies, key=lambda item: str(item.get("policy_id") or ""))


def _apply_locked(root: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    expected_plan = dict(plan)
    supplied_digest = expected_plan.pop("plan_sha256", None)
    if supplied_digest != _digest(expected_plan):
        raise LifecycleError("lifecycle plan digest is invalid")
    pending = _load_pending_transaction(root)
    if pending is not None:
        if str(pending.get("plan_sha256") or "") != str(plan.get("plan_sha256") or ""):
            raise LifecycleError("another lifecycle plan is pending; resume it with the exact original plan")
        if pending.get("phase") == "preparing":
            _discard_preparing_transaction(root, pending)
        else:
            return _resume_transaction(root, plan, pending)
    if _state_sha256(root) != plan.get("root_state_sha256"):
        raise LifecycleError("generated knowledge changed after planning; create a new dry-run plan")
    context = _build_context(root, plan["selectors"], str(plan["action"]))
    action = str(plan["action"])
    schedule = plan.get("schedule") if isinstance(plan.get("schedule"), dict) else {}
    writes: dict[Path, bytes] = {}
    deletes: set[Path] = set()

    if action == "schedule":
        claims = json.loads(json.dumps(context["claims"], ensure_ascii=False, default=str))
        for claim in claims:
            if str(claim.get("claim_id") or "") in context["selected_claim_ids"]:
                claim.update({key: value for key, value in schedule.items() if value is not None})
        claims_path = _safe_generated_path(root, "audit/claims.json")
        writes[claims_path] = _json_bytes(claims)
        for path, review in context["reviews"]:
            writes[path] = _json_bytes(_update_review(review, context, action, schedule))
        policies_path = _safe_generated_path(root, "audit/lifecycle-policies.json")
        writes[policies_path] = _json_bytes(_schedule_policies(root, plan, context))
        # Schedule is allowed to keep a previously final publication usable,
        # but only after every policy/review target has committed. Its original
        # completion report is therefore staged and restored last.
        completion_path = _safe_generated_path(root, "audit/completion-report.json", must_exist=True)
        writes[completion_path] = completion_path.read_bytes()
    else:
        forget = action == "forget"
        literals = _content_literals(context) if forget else set()
        # A forget action removes selected event rows entirely. Durable
        # re-ingestion protection lives only in the content-free hashed
        # tombstone written below; no native session/event identifiers or
        # residual event skeleton are kept as a substitute for deletion.
        events = [
            dict(row)
            for row in context["events"]
            if not (forget and str(row.get("event_id") or "") in context["selected_event_ids"])
        ]
        writes[_safe_generated_path(root, "audit/events.jsonl")] = _jsonl_bytes(events)

        claims: list[dict[str, Any]] = []
        for claim in context["claims"]:
            selected = str(claim.get("claim_id") or "") in context["selected_claim_ids"]
            if selected and forget:
                continue
            copy = dict(claim)
            if selected:
                copy["status"] = "retracted"
            claims.append(copy)
        writes[_safe_generated_path(root, "audit/claims.json")] = _json_bytes(claims)

        feedback = []
        for item in context["feedback"]:
            selected = str(item.get("feedback_id") or "") in context["selected_feedback_ids"]
            if selected and forget:
                continue
            copy = dict(item)
            if selected:
                copy["status"] = "retracted"
            feedback.append(copy)
        writes[_safe_generated_path(root, "audit/feedback-signals.json")] = _json_bytes(feedback)

        evolutions = []
        for item in context["evolutions"]:
            selected = str(item.get("evolution_id") or "") in context["selected_rule_ids"]
            if selected and forget:
                continue
            copy = dict(item)
            if selected:
                copy["status"] = "rejected"
                copy["validation_result"] = "failed"
            evolutions.append(copy)
        writes[_safe_generated_path(root, "audit/rule-evolutions.json")] = _json_bytes(evolutions)

        relationships = [
            item
            for item in context["relationships"]
            if str(item.get("edge_id") or "") not in context["selected_edge_ids"]
            and not (_evidence_ids(item) & context["selected_event_ids"])
        ]
        writes[_safe_generated_path(root, "audit/relationships.json")] = _json_bytes(relationships)

        for path, review in context["reviews"]:
            writes[path] = _json_bytes(_update_review(review, context, action, schedule))

        graph = json.loads(json.dumps(context["graph"], ensure_ascii=False, default=str))
        graph["nodes"] = [node for node in graph.get("nodes") or [] if str(node.get("id") or "") not in context["selected_node_ids"]]
        remaining_nodes = {str(node.get("id") or "") for node in graph["nodes"]}
        graph["edges"] = [
            edge
            for edge in graph.get("edges") or []
            if str(edge.get("edge_id") or "") not in context["selected_edge_ids"]
            and str(edge.get("source") or "") in remaining_nodes
            and str(edge.get("target") or "") in remaining_nodes
            and not (_evidence_ids(edge) & context["selected_event_ids"])
        ]
        graph["semantic_status"] = "lifecycle-pending-redistill"
        graph["lifecycle_plan_sha256"] = plan["plan_sha256"]
        writes[context["graph_path"]] = _json_bytes(graph)

        index = json.loads(json.dumps(context["index"], ensure_ascii=False, default=str))
        index["documents"] = [item for item in index.get("documents") or [] if str(item.get("path") or "") not in context["project_doc_paths"]]
        index["semantic_status"] = "lifecycle-pending-redistill"
        index["lifecycle_plan_sha256"] = plan["plan_sha256"]
        if isinstance(index.get("graph"), dict):
            index["graph"]["nodes"] = len(graph["nodes"])
            index["graph"]["edges"] = len(graph["edges"])
        writes[_safe_generated_path(root, "knowledge/knowledge-index.json")] = _json_bytes(index)

        for relative in context["project_doc_paths"]:
            path = _safe_generated_path(root, Path("knowledge") / relative)
            if path.exists():
                deletes.add(path)
        for relative in context["affected_docs"]:
            if relative in context["project_doc_paths"]:
                continue
            path = _safe_generated_path(root, Path("knowledge") / relative)
            if path.is_file():
                text = _without_lifecycle_sections(
                    path.read_text(encoding="utf-8"), context["selected_claim_ids"], context["selected_rule_ids"]
                )
                writes[path] = text.encode("utf-8")

        if forget:
            archive_root = _safe_generated_path(root, "knowledge/archive")
            if archive_root.is_dir():
                markers = (
                    set(context["selected_event_ids"])
                    | set(context["selected_claim_ids"])
                    | set(context["selected_project_keys"])
                    | literals
                )
                selected_names = {Path(value).name for value in context["project_doc_paths"]}
                for path in archive_root.rglob("*.md"):
                    _safe_generated_path(root, path.relative_to(root), must_exist=True)
                    text = path.read_text(encoding="utf-8")
                    if path.name in selected_names or any(marker and marker in text for marker in markers):
                        deletes.add(path)

        # All surviving generated Markdown loses now-stale relationship links.
        for path in _generated_files(root):
            if path.suffix.lower() == ".md" and path not in deletes:
                source = writes.get(path, path.read_bytes()).decode("utf-8")
                source = _without_lifecycle_sections(source, context["selected_claim_ids"], context["selected_rule_ids"])
                writes[path] = _invalidate_related_section(source).encode("utf-8")

        completion_path = _safe_generated_path(root, "audit/completion-report.json")
        completion = _load_json(completion_path, {})
        if isinstance(completion, dict):
            completion["status"] = "needs_redistill"
            gates = completion.setdefault("gates", {})
            for gate in (
                "semantic_review_complete",
                "knowledge_graph_complete",
                "published_knowledge",
                "retrieval_related_match",
                "retrieval_unrelated_no_match",
                "retrieval_related_suite",
                "retrieval_hard_negative_suite",
            ):
                gates[gate] = False
            completion["lifecycle_plan_sha256"] = plan["plan_sha256"]
            completion.pop("completed_at", None)
            completion.pop("retrieval_verified_at", None)
            completion.pop("retrieval_suite_sha256", None)
            completion.pop("retrieval_verification_sha256", None)
            completion.pop("retrieval_profile", None)
            completion.pop("publication_manifest_sha256", None)
            writes[completion_path] = _json_bytes(completion)

        manifest_path = _safe_generated_path(root, "audit/published-files.json")
        manifest = _load_json(manifest_path, {})
        if isinstance(manifest, dict):
            manifest["knowledge_paths"] = [value for value in manifest.get("knowledge_paths") or [] if str(value) not in context["project_doc_paths"]]
            manifest["project_paths"] = [value for value in manifest.get("project_paths") or [] if str(value) not in context["project_doc_paths"]]
            manifest["semantic_status"] = "lifecycle-pending-redistill"
            writes[manifest_path] = _json_bytes(manifest)

        retrieval_path = _safe_generated_path(root, "audit/retrieval-verification.json")
        if retrieval_path.is_file():
            writes[retrieval_path] = _json_bytes(
                {"status": "invalidated", "reason": "lifecycle-change", "lifecycle_plan_sha256": plan["plan_sha256"], "content_persisted": False}
            )

        policies_path = _safe_generated_path(root, "audit/lifecycle-policies.json")
        if policies_path.is_file():
            policies: list[dict[str, Any]] = []
            for policy in _policy_rows(root):
                policy_selectors = policy.get("selectors") if isinstance(policy.get("selectors"), dict) else {}
                affected = (
                    bool(set(_normal_values(policy_selectors.get("source_file_ids"))) & set(context["matched"]["source_file_ids"]))
                    or bool(set(_normal_values(policy_selectors.get("event_ids"))) & context["selected_event_ids"])
                    or bool(set(_normal_values(policy_selectors.get("claim_ids"))) & context["selected_claim_ids"])
                    or bool(set(_normal_values(policy_selectors.get("project_keys"))) & context["selected_project_keys"])
                )
                if affected and forget:
                    continue
                if affected:
                    policy["status"] = "inactive-retracted"
                    policy["updated_at"] = _utc_now()
                policies.append(policy)
            writes[policies_path] = _json_bytes(policies)

        if forget:
            # Remove forgotten literals from every surviving generated text
            # artifact, including old review packets and private archives.
            for path in _generated_files(root):
                if path in deletes:
                    continue
                content = writes.get(path, path.read_bytes())
                if path.suffix.lower() == ".md":
                    writes[path] = _scrub_string(content.decode("utf-8"), literals).encode("utf-8")
                elif path.suffix.lower() == ".json":
                    writes[path] = _json_bytes(_scrub_value(json.loads(content.decode("utf-8")), literals))
                elif path.suffix.lower() == ".jsonl":
                    rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
                    writes[path] = _jsonl_bytes([_scrub_value(row, literals) for row in rows])

    applied_at = _utc_now()
    tombstone = _tombstone(plan, context, applied_at)
    tombstone_path = _safe_generated_path(root, "audit/lifecycle-tombstones.jsonl")
    if tombstone_path in writes:
        tombstones = [
            json.loads(line)
            for line in writes[tombstone_path].decode("utf-8").splitlines()
            if line.strip()
        ]
    else:
        tombstones = _load_jsonl(tombstone_path)
    if action == "forget":
        forgotten_identifiers = {
            value
            for key in ("sources", "claims", "edges", "rules", "projects", "events")
            for value in plan["impact"][key]
        }
        tombstones = [_scrub_value(row, forgotten_identifiers) for row in tombstones]
    tombstones.append(tombstone)
    writes[tombstone_path] = _jsonl_bytes(tombstones)

    persisted_impact = {
        "impact_version": 1,
        "action": action,
        "applied_at": applied_at,
        "plan_sha256": plan["plan_sha256"],
        "requires_redistill": plan["impact"]["requires_redistill"],
        "requires_retrieval_gates": plan["impact"]["requires_retrieval_gates"],
        "source_sessions_mutated": False,
        "backup_erasure_guaranteed": False,
    }
    if action == "forget":
        persisted_impact["affected_identifier_sha256"] = {
            key: _hash_list(plan["impact"][key])
            for key in ("sources", "documents", "claims", "edges", "rules", "projects", "events")
        }
    else:
        persisted_impact["affected"] = {
            key: plan["impact"][key]
            for key in ("sources", "documents", "claims", "edges", "rules", "projects", "events")
        }
    writes[_safe_generated_path(root, "audit/lifecycle-last-impact.json")] = _json_bytes(persisted_impact)

    for path in writes:
        _safe_generated_path(root, path.relative_to(root))
        if path.exists() and path.is_symlink():
            raise LifecycleError(f"refusing to replace symlink: {path.relative_to(root)}")
    for path in deletes:
        _safe_generated_path(root, path.relative_to(root), must_exist=True)
    journal = _stage_transaction(
        root,
        plan,
        writes,
        deletes,
        tombstone_id=tombstone["tombstone_id"],
        # Very short/common words can legitimately occur in lifecycle control
        # metadata (for example the action name "forget"). The structured
        # scrub already removes all selected literals; this second staging
        # assertion catches substantive forgotten bodies without false hits on
        # protocol vocabulary.
        forbidden_literals={value for value in literals if len(value) >= 8} if action == "forget" else None,
    )
    return _resume_transaction(root, plan, journal)


def apply_lifecycle_plan(
    kb: Path,
    plan: Mapping[str, Any],
    *,
    commit: bool = False,
    acquire_lock: bool = True,
) -> dict[str, Any]:
    """Apply an unchanged dry-run plan only when ``commit=True``.

    A CLI that already holds :func:`session_kb.locking.mutation_lock` can pass
    ``acquire_lock=False`` to avoid nested locks.
    """

    if not commit:
        return dict(plan)
    root = _raw_root(kb)
    guard = mutation_lock(root, f"lifecycle-{plan.get('action')}") if acquire_lock else nullcontext()
    with guard:
        return _apply_locked(root, plan)


def execute_lifecycle(
    kb: Path,
    action: str,
    selectors: Mapping[str, object] | None = None,
    *,
    dry_run: bool = True,
    acquire_lock: bool = True,
    **options: Any,
) -> dict[str, Any]:
    """Plan by default; mutate only with the explicit ``dry_run=False`` opt-in."""

    plan = plan_lifecycle(kb, action, selectors, **options)
    return apply_lifecycle_plan(kb, plan, commit=not dry_run, acquire_lock=acquire_lock)


def list_due_revalidations(kb: Path, *, as_of: str | datetime | date | None = None) -> dict[str, Any]:
    """List due rechecks and retention expiries without returning claim bodies."""

    root = _raw_root(kb)
    current = _parse_time(as_of, "as_of") or datetime.now(timezone.utc)
    candidates: dict[str, dict[str, Any]] = {}
    for policy in _policy_rows(root):
        if not isinstance(policy, dict) or policy.get("status") != "active":
            continue
        policy_selectors = policy.get("selectors") if isinstance(policy.get("selectors"), dict) else {}
        candidates["selector:" + _digest(policy_selectors)] = dict(policy)
    claims = _load_json(_safe_generated_path(root, "audit/claims.json"), [])
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, dict) or not claim.get("claim_id") or claim.get("status") in {"retracted", "stale"}:
            continue
        if not claim.get("recheck_after") and not claim.get("retain_until"):
            continue
        claim_id = str(claim["claim_id"])
        claim_selectors = {"claim_ids": [claim_id]}
        candidate_key = "selector:" + _digest(
            {
                "source_file_ids": [],
                "event_ids": [],
                "claim_ids": [claim_id],
                "project_keys": [],
            }
        )
        claim_candidate = {
            "policy_id": "claim-" + _id_hash(claim_id)[:24],
            "selectors": claim_selectors,
            "observed_at": claim.get("observed_at"),
            "checked_at": claim.get("checked_at"),
            "recheck_after": claim.get("recheck_after"),
            "retain_until": claim.get("retain_until"),
            "status": "active",
        }
        if candidate_key in candidates:
            candidates[candidate_key].update({key: value for key, value in claim_candidate.items() if value is not None})
        else:
            candidates[candidate_key] = claim_candidate

    due: list[dict[str, Any]] = []
    for policy in candidates.values():
        reasons: list[str] = []
        recheck = _parse_time(policy.get("recheck_after"), "recheck_after")
        retention = _parse_time(policy.get("retain_until"), "retain_until")
        if recheck and recheck <= current:
            reasons.append("recheck_due")
        if retention and retention <= current:
            reasons.append("retention_expired")
        if reasons:
            due.append(
                {
                    "policy_id": policy.get("policy_id"),
                    "selectors": policy.get("selectors") or {},
                    "observed_at": policy.get("observed_at"),
                    "checked_at": policy.get("checked_at"),
                    "recheck_after": policy.get("recheck_after"),
                    "retain_until": policy.get("retain_until"),
                    "due_reasons": reasons,
                }
            )
    due.sort(key=lambda item: (str(item.get("recheck_after") or item.get("retain_until") or ""), str(item.get("policy_id") or "")))
    return {
        "as_of": current.isoformat(),
        "due_count": len(due),
        "due": due,
        "content_included": False,
        "source_sessions_mutated": False,
        "warnings": [BACKUP_WARNING],
    }
