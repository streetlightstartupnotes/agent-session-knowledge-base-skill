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
STABLE_ID_RE = re.compile(r"rel-[A-Za-z0-9][A-Za-z0-9._:-]{0,91}")
CLAIM_ID_RE = re.compile(r"claim-[A-Za-z0-9][A-Za-z0-9._:-]{0,89}")


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


def _project_groups(events: list[UnifiedEvent]) -> dict[str, list[UnifiedEvent]]:
    groups: dict[str, list[UnifiedEvent]] = defaultdict(list)
    for event in events:
        groups[event.project_key or f"session:{event.logical_session_id}"].append(event)
    for group in groups.values():
        group.sort(key=lambda event: (str(event.timestamp or ""), event.logical_session_id, event.sequence, event.event_id))
    return dict(groups)


def create_review_template(kb: Path, destination: Path | None = None) -> dict[str, Any]:
    root = _kb_root(kb)
    completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    events = _events(root)
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
                "semantic_status": "unreviewed",
                "default_disposition": "project_context",
                "rationale": "",
                "event_exceptions": [],
                "link_analysis": {"status": "unreviewed", "rationale": ""},
                "history": {section: [] for section in HISTORY_SECTIONS},
            }
        )
    template = {
        "review_version": 2,
        "run_id": completion.get("run_id"),
        "event_count": len(events),
        "event_set_sha256": _hash_ids([event.event_id for event in events]),
        "reviewer": {
            "id": "",
            "reviewed_at": "",
            "attestation": "",
        },
        "unsupported_formats_acknowledged": False,
        "base_claims": [],
        "project_relationships": [],
        "projects": projects,
        "instructions": {
            "semantic_status": "Set every project to reviewed or reviewed-no-knowledge after reading its complete ordered event chain.",
            "history_item": {"text": "A bounded assertion", "evidence_event_ids": ["evt-..."], "status": "observed"},
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
                "conflicts": [],
                "supersedes": [],
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
        },
    }
    target = destination.expanduser() if destination else root / "review" / "review.json"
    if destination is None and target.exists():
        safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(template["run_id"] or "next"))
        target = root / "review" / f"review-{safe_run_id}.json"
    if target.exists():
        raise ValueError(f"review file already exists: {target}")
    _json_write(target, template)
    return {"review_file": str(target.resolve()), "projects": len(projects), "events": len(events), "run_id": template["run_id"]}


def create_review_packet(kb: Path, project_key: str) -> dict[str, Any]:
    """Return one complete sanitized project chain for bounded semantic reading."""
    root = _kb_root(kb)
    events = _events(root)
    groups = _project_groups(events)
    key = str(project_key).strip()
    if key not in groups:
        raise ValueError(f"unknown project_key: {key}")
    group = groups[key]
    event_ids = [event.event_id for event in group]
    return {
        "packet_version": 1,
        "project_key": key,
        "project_label": next((event.project_label for event in group if event.project_label), None) or key,
        "event_count": len(group),
        "event_ids_sha256": _hash_ids(event_ids),
        "ordered_events": [event.to_dict() for event in group],
        "review_focus": list(HISTORY_SECTIONS),
        "relationship_rule": (
            "After this chain is reviewed, compare its objective, corrections, artifacts, dependencies, handoffs, and contradictions with other reviewed chains. "
            "Before confirming a relationship, reopen both packets and cite event ids from both sides. Filename, title, broad project type, or keyword overlap alone is insufficient."
        ),
    }


def _sensitive_review_text(value: Any) -> bool:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    sanitizer = Sanitizer()
    sanitized = sanitizer.sanitize_text(serialized)
    return serialized != sanitized and bool(sanitizer.stats)


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
    for event_id in evidence_ids:
        event = event_by_id.get(str(event_id))
        if event is None:
            errors.append(f"{location}: unknown evidence event {event_id}")
        elif (event.project_key or f"session:{event.logical_session_id}") != project_key:
            errors.append(f"{location}: evidence event {event_id} belongs to another project")


def validate_review(kb: Path, review_path: Path) -> tuple[dict[str, Any], list[UnifiedEvent], list[str]]:
    root = _kb_root(kb)
    review = json.loads(review_path.expanduser().read_text(encoding="utf-8"))
    events = _events(root)
    event_by_id = {event.event_id: event for event in events}
    groups = _project_groups(events)
    completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    if review.get("review_version") != 2:
        errors.append("unsupported review_version")
    if review.get("run_id") != completion.get("run_id"):
        errors.append("review run_id does not match the frozen evidence run")
    expected_event_hash = _hash_ids([event.event_id for event in events])
    if review.get("event_count") != len(events) or review.get("event_set_sha256") != expected_event_hash:
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
    for key, group in groups.items():
        project = project_by_key.get(key)
        if project is None:
            continue
        event_ids = [event.event_id for event in group]
        if project.get("event_count") != len(group) or project.get("event_ids_sha256") != _hash_ids(event_ids):
            errors.append(f"project {key}: event set hash/count mismatch")
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
            if not any(event.evidence_grade == "A" and event.actor_kind == "primary_user" for event in claim_events):
                errors.append(f"{location}: confirmed {claim_type} claim needs primary-user grade-A evidence")
        if claim_type in {"identity", "direction"} and str(claim.get("subject") or "") != "primary_user":
            errors.append(f"{location}: identity/direction subject must be primary_user")
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

    if _sensitive_review_text({"reviewer": reviewer, "base_claims": claims, "project_relationships": relationships, "projects": projects}):
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
                f"- Evidence: {evidence}",
                f"- Conflicts: {', '.join(claim.get('conflicts') or []) or 'none recorded'}",
                f"- Supersedes: {', '.join(claim.get('supersedes') or []) or 'none recorded'}",
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
    claims_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        claims_by_type[str(claim["knowledge_type"])].append(claim)
    evidence_rules = _render_base("Evidence and reading rules", claims_by_type["evidence_rule"], review)
    evidence_rules += "\n## Publication gates\n\nEvery retained event has a semantic disposition. Every published assertion cites existing event ids. Unknown, disputed, stale, and retracted claims remain explicit.\n"
    identity = _render_base("Identity and current direction", claims_by_type["identity"] + claims_by_type["direction"], review)
    collaboration = _render_base("Collaboration and expression rules", claims_by_type["collaboration"] + claims_by_type["expression"], review)
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
        "graph_version": 1,
        "semantic_status": "published",
        "run_id": review["run_id"],
        "nodes": graph_nodes,
        "edges": graph_edges,
    }
    index = {
        "index_version": 3,
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
    }
    documents["knowledge-graph.json"] = json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    documents["knowledge-index.json"] = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for relative, content in documents.items():
        _atomic_text(root / "knowledge" / relative, content)
    _jsonl_write(root / "audit" / "semantic-dispositions.jsonl", semantic_rows)
    _json_write(root / "audit" / "claims.json", claims)
    _json_write(root / "audit" / "relationships.json", graph_edges)
    _json_write(root / "audit" / "relationship-candidates.json", relationship_candidates)
    _json_write(root / "audit" / "link-audit.json", link_audit)
    completion = json.loads((root / "audit" / "completion-report.json").read_text(encoding="utf-8"))
    has_unsupported = not completion.get("gates", {}).get("unsupported_formats_clear")
    completion["status"] = "complete_with_unsupported_formats" if has_unsupported else "complete"
    completion["gates"]["semantic_review_complete"] = True
    completion["gates"]["knowledge_graph_complete"] = True
    completion["gates"]["published_knowledge"] = True
    completion["gates"]["unsupported_formats_acknowledged"] = bool(review.get("unsupported_formats_acknowledged"))
    completion["semantic_dispositions"] = len(semantic_rows)
    completion["published_claims"] = len(claims)
    completion["published_projects"] = published_projects
    completion["published_project_relationships"] = sum(relationship.get("status") == "confirmed" for relationship in explicit_relationships)
    completion["published_graph_edges"] = sum(edge.get("status") == "confirmed" for edge in graph_edges)
    completion["reviewer"] = review["reviewer"]
    completion["completed_at"] = datetime.now(timezone.utc).isoformat()
    _json_write(root / "audit" / "completion-report.json", completion)
    return {
        "status": completion["status"],
        "run_id": review["run_id"],
        "semantic_dispositions": len(semantic_rows),
        "published_claims": len(claims),
        "published_projects": published_projects,
        "published_project_relationships": completion["published_project_relationships"],
        "published_graph_edges": completion["published_graph_edges"],
        "knowledge_root": str((root / "knowledge").resolve()),
    }
