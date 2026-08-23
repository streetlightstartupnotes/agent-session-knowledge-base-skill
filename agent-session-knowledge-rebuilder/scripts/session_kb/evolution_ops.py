from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping


VALID_SCOPES = {"artifact", "project", "task-type", "global"}
VALID_DECISIONS = {"approve", "reject"}
VALID_RESULTS = {"pending", "passed", "failed", "mixed"}


def _event_value(event: object, field: str) -> Any:
    if isinstance(event, Mapping):
        return event.get(field)
    return getattr(event, field, None)


def _reliable_timestamp(event: object) -> datetime | None:
    raw = _event_value(event, "timestamp")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def behavior_evidence_order_errors(
    events: Iterable[object],
    baseline_event_ids: Iterable[str],
    validation_event_ids: Iterable[str],
) -> list[str]:
    """Return fail-closed before/after ordering errors for behavior evidence.

    Events in one logical session are ordered only by their recorded sequence.
    Events in different sessions are comparable only when both carry valid,
    timezone-aware timestamps. Every validation event must be later than every
    baseline event.
    """

    event_by_id: dict[str, object] = {}
    duplicate_ids: set[str] = set()
    for event in events:
        event_id = str(_event_value(event, "event_id") or "")
        if not event_id:
            continue
        if event_id in event_by_id:
            duplicate_ids.add(event_id)
        event_by_id[event_id] = event
    errors = [f"duplicate unified event id prevents chronology validation: {event_id}" for event_id in sorted(duplicate_ids)]
    baseline_ids = [str(item) for item in baseline_event_ids]
    after_ids = [str(item) for item in validation_event_ids]
    missing = sorted({event_id for event_id in baseline_ids + after_ids if event_id not in event_by_id})
    errors.extend(f"unknown behavior evidence event: {event_id}" for event_id in missing)
    if errors:
        return errors

    for baseline_id in baseline_ids:
        baseline = event_by_id[baseline_id]
        baseline_session = str(_event_value(baseline, "logical_session_id") or "")
        for after_id in after_ids:
            after = event_by_id[after_id]
            after_session = str(_event_value(after, "logical_session_id") or "")
            if baseline_session and baseline_session == after_session:
                baseline_sequence = _event_value(baseline, "sequence")
                after_sequence = _event_value(after, "sequence")
                if (
                    not isinstance(baseline_sequence, int)
                    or isinstance(baseline_sequence, bool)
                    or not isinstance(after_sequence, int)
                    or isinstance(after_sequence, bool)
                ):
                    errors.append(
                        f"same-session order between baseline {baseline_id} and validation {after_id} needs integer sequences"
                    )
                elif after_sequence <= baseline_sequence:
                    errors.append(
                        f"validation event {after_id} is not later than baseline event {baseline_id} in logical session {baseline_session}"
                    )
                continue

            baseline_time = _reliable_timestamp(baseline)
            after_time = _reliable_timestamp(after)
            if baseline_time is None or after_time is None:
                errors.append(
                    f"cross-session order between baseline {baseline_id} and validation {after_id} needs comparable timezone-aware timestamps"
                )
            elif after_time <= baseline_time:
                errors.append(f"validation event {after_id} timestamp is not later than baseline event {baseline_id}")
    return errors


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _feedback_map(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in review.get("feedback_signals") or []:
        if not isinstance(item, dict):
            continue
        feedback_id = str(item.get("feedback_id") or "")
        if feedback_id:
            result[feedback_id] = item
    return result


def feedback_clusters(review: dict[str, Any]) -> list[dict[str, Any]]:
    """Cluster only exact normalized feedback; semantic grouping remains an Agent review task."""

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for signal in _feedback_map(review).values():
        if signal.get("status") != "observed":
            continue
        key = (
            str(signal.get("scope") or ""),
            str(signal.get("object") or ""),
            _normalized_text(signal.get("applies_to")),
            _normalized_text(signal.get("statement")),
        )
        grouped.setdefault(key, []).append(signal)

    clusters: list[dict[str, Any]] = []
    for key, signals in sorted(grouped.items(), key=lambda item: item[0]):
        feedback_ids = sorted(str(item["feedback_id"]) for item in signals)
        project_keys = sorted({str(item.get("project_key") or "") for item in signals if item.get("project_key")})
        clusters.append(
            {
                "cluster_id": f"feedback-cluster-{_stable_hash(feedback_ids)[:16]}",
                "scope": key[0],
                "object": key[1],
                "applies_to": str(signals[0].get("applies_to") or ""),
                "statement": str(signals[0].get("statement") or ""),
                "feedback_ids": feedback_ids,
                "project_keys": project_keys,
                "signal_count": len(signals),
                "distinct_project_count": len(project_keys),
                "global_repetition_gate_met": len(project_keys) >= 2,
                "semantic_merge_performed": False,
            }
        )
    return clusters


def propose_rule_evolution(review: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    """Add an evidence-linked candidate without activating a rule."""

    if not isinstance(proposal, dict):
        raise ValueError("evolution proposal must be an object")
    feedback_by_id = _feedback_map(review)
    feedback_ids = sorted({str(item) for item in proposal.get("feedback_ids") or [] if str(item)})
    if not feedback_ids:
        raise ValueError("evolution proposal needs feedback_ids")
    missing = [item for item in feedback_ids if item not in feedback_by_id]
    if missing:
        raise ValueError("evolution proposal references unknown feedback")
    signals = [feedback_by_id[item] for item in feedback_ids]
    if not any(item.get("status") == "observed" for item in signals):
        raise ValueError("evolution proposal needs observed feedback")
    signal_scopes = {str(item.get("scope") or "") for item in signals}
    scope = str(proposal.get("scope") or "")
    if scope not in VALID_SCOPES:
        raise ValueError("evolution proposal has invalid scope")
    if signal_scopes != {scope}:
        raise ValueError("evolution proposal scope must exactly match every cited feedback scope")
    proposed_rule = str(proposal.get("proposed_rule") or "").strip()
    rationale = str(proposal.get("rationale") or "").strip()
    expected_change = str(proposal.get("expected_behavior_change") or "").strip()
    if not proposed_rule or not rationale or not expected_change:
        raise ValueError("proposed_rule, rationale, and expected_behavior_change are required")
    rule_version = proposal.get("rule_version", 1)
    if not isinstance(rule_version, int) or isinstance(rule_version, bool) or rule_version <= 0:
        raise ValueError("rule_version must be a positive integer")
    identity = {
        "feedback_ids": feedback_ids,
        "scope": scope,
        "proposed_rule": proposed_rule,
        "rule_version": rule_version,
    }
    evolution_id = str(proposal.get("evolution_id") or f"evolution-{_stable_hash(identity)[:20]}")
    existing = {str(item.get("evolution_id") or "") for item in review.get("rule_evolutions") or [] if isinstance(item, dict)}
    if evolution_id in existing:
        raise ValueError("evolution_id already exists")
    projects = sorted({str(item.get("project_key") or "") for item in signals if item.get("project_key")})
    evolution = {
        "evolution_id": evolution_id,
        "status": "candidate",
        "scope": scope,
        "rule_version": rule_version,
        "feedback_ids": feedback_ids,
        "before_rule": str(proposal.get("before_rule") or ""),
        "proposed_rule": proposed_rule,
        "rationale": rationale,
        "expected_behavior_change": expected_change,
        "approval_event_ids": [],
        "baseline_event_ids": [],
        "validation_event_ids": [],
        "validation_result": "pending",
        "observed_behavior_change": "",
        "global_evidence_sufficient": scope != "global" or len(projects) >= 2,
        "source_project_keys": projects,
    }
    updated = copy.deepcopy(review)
    updated.setdefault("rule_evolutions", []).append(evolution)
    return {"review": updated, "evolution": evolution}


def approval_queue(review: dict[str, Any]) -> dict[str, Any]:
    pending = []
    for item in review.get("rule_evolutions") or []:
        if not isinstance(item, dict) or item.get("status") != "candidate":
            continue
        pending.append(
            {
                "evolution_id": item.get("evolution_id"),
                "scope": item.get("scope"),
                "rule_version": item.get("rule_version"),
                "feedback_ids": list(item.get("feedback_ids") or []),
                "before_rule": item.get("before_rule"),
                "proposed_rule": item.get("proposed_rule"),
                "expected_behavior_change": item.get("expected_behavior_change"),
                "global_evidence_sufficient": item.get("global_evidence_sufficient"),
            }
        )
    return {"queue_version": 1, "candidate_count": len(pending), "candidates": pending}


def decide_rule_evolution(
    review: dict[str, Any],
    evolution_id: str,
    decision: str,
    *,
    approval_event_ids: Iterable[str] = (),
    promoted_claim_id: str | None = None,
    explicit_global_approval: bool = False,
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError("evolution decision must be approve or reject")
    updated = copy.deepcopy(review)
    evolutions = [item for item in updated.get("rule_evolutions") or [] if isinstance(item, dict)]
    target = next((item for item in evolutions if str(item.get("evolution_id") or "") == evolution_id), None)
    if target is None:
        raise ValueError("unknown evolution_id")
    if target.get("status") != "candidate":
        raise ValueError("only a candidate evolution can be decided")
    if decision == "reject":
        target["status"] = "rejected"
        target["approval_event_ids"] = []
        target.pop("promoted_claim_id", None)
        target["explicit_global_approval"] = False
        return updated

    approval_ids = sorted({str(item) for item in approval_event_ids if str(item)})
    if not approval_ids or not promoted_claim_id:
        raise ValueError("approval needs approval_event_ids and promoted_claim_id")
    claims = {
        str(item.get("claim_id") or ""): item
        for item in updated.get("base_claims") or []
        if isinstance(item, dict) and item.get("claim_id")
    }
    promoted = claims.get(str(promoted_claim_id))
    if promoted is None or promoted.get("status") != "confirmed":
        raise ValueError("promoted_claim_id must identify a confirmed claim")
    if promoted.get("knowledge_type") in {"collaboration", "expression"}:
        if promoted.get("derivation") != "feedback-promotion" or promoted.get("rule_scope") != target.get("scope"):
            raise ValueError("promoted collaboration/expression claim must match the feedback-promotion scope")
    if target.get("scope") == "global" and not target.get("global_evidence_sufficient") and not explicit_global_approval:
        raise ValueError("global approval needs two feedback project contexts or explicit global approval")
    target["status"] = "approved"
    target["approval_event_ids"] = approval_ids
    target["promoted_claim_id"] = str(promoted_claim_id)
    target["explicit_global_approval"] = bool(explicit_global_approval)
    target["validation_result"] = "pending"
    return updated


def record_behavior_evaluation(
    review: dict[str, Any],
    evolution_id: str,
    *,
    validation_result: str,
    baseline_event_ids: Iterable[str],
    validation_event_ids: Iterable[str],
    events: Iterable[object],
    observed_behavior_change: str = "",
) -> dict[str, Any]:
    if validation_result not in VALID_RESULTS - {"pending"}:
        raise ValueError("behavior evaluation result must be passed, failed, or mixed")
    updated = copy.deepcopy(review)
    target = next(
        (
            item
            for item in updated.get("rule_evolutions") or []
            if isinstance(item, dict) and str(item.get("evolution_id") or "") == evolution_id
        ),
        None,
    )
    if target is None:
        raise ValueError("unknown evolution_id")
    if target.get("status") not in {"approved", "validated"}:
        raise ValueError("behavior evaluation requires an approved evolution")
    baseline = sorted({str(item) for item in baseline_event_ids if str(item)})
    after = sorted({str(item) for item in validation_event_ids if str(item)})
    if not baseline or not after or set(baseline) & set(after):
        raise ValueError("behavior evaluation needs distinct non-empty baseline and later evidence")
    chronology_errors = behavior_evidence_order_errors(events, baseline, after)
    if chronology_errors:
        raise ValueError("behavior evaluation chronology is invalid:\n- " + "\n- ".join(chronology_errors))
    target["baseline_event_ids"] = baseline
    target["validation_event_ids"] = after
    target["validation_result"] = validation_result
    target["observed_behavior_change"] = str(observed_behavior_change or "").strip()
    if validation_result == "passed":
        if not target["observed_behavior_change"]:
            raise ValueError("passed behavior evaluation needs observed_behavior_change")
        target["status"] = "validated"
    elif target.get("status") == "validated":
        target["status"] = "approved"
    return updated


def atomic_write_review(path: Path, review: dict[str, Any]) -> None:
    raw_target = path.expanduser()
    if raw_target.is_symlink():
        raise ValueError("review path cannot be a symlink")
    target = raw_target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(review, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
