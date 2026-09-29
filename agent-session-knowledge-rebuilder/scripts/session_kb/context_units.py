"""Bounded views of reviewed assertions; never infer a current state from order."""
from __future__ import annotations

import json
import unicodedata
from hashlib import sha256
from typing import Any

RETRIEVAL_ENGINE_VERSION = "unicode-scoped-units-1"
DEFAULT_SELECTION = {"base_context": "auto", "view": "documents", "max_facts": 6, "max_chars": 6000}
STATE_FIELDS = {"goal", "accepted_baseline", "constraints", "latest_correction", "delivery", "next_step"}


def selection_options(value: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is not None and (not isinstance(value, dict) or set(value) - set(DEFAULT_SELECTION)):
        raise ValueError("invalid selection options")
    result = {**DEFAULT_SELECTION, **(value or {})}
    if result["base_context"] not in {"auto", "none", "identity", "collaboration", "both"}:
        raise ValueError("invalid base_context")
    if result["view"] not in {"documents", "facts", "current"}:
        raise ValueError("invalid context view")
    for key, low, high in (("max_facts", 1, 50), ("max_chars", 256, 50000)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    return result


def history_unit(section: str, position: int, item: dict[str, Any]) -> dict[str, Any]:
    fields = ("text", "status", "evidence_event_ids", "before", "after", "applies_to")
    return {"unit_id": f"history:{section}:{position}", "section": section,
            **{key: item[key] for key in fields if key in item}}


def unit_view(document: dict[str, Any], task_tokens: set[str], options: dict[str, Any]) -> dict[str, Any]:
    units = document.get("context_units")
    if not isinstance(units, list):
        return {"context_status": "unavailable", "reason": "publication_has_no_reviewed_units",
                "units": [], "full_document_available": True}
    by_id = {str(item["unit_id"]): item for item in units}
    if options["view"] == "current":
        state = document.get("current_state") or {}
        if not state:
            return {"context_status": "unavailable", "reason": "current_state_not_reviewed",
                    "units": [], "missing_state_fields": sorted(STATE_FIELDS), "full_document_available": True}
        candidates = [{**by_id[unit_id], "state_field": field} for field, unit_id in state.items()]
    else:
        def relevance(item: dict[str, Any]) -> int:
            text = unicodedata.normalize("NFKC", str(item.get("text") or "")).casefold()
            return sum(token in text for token in task_tokens)
        candidates = sorted(units, key=lambda item: -relevance(item))
    selected: list[dict[str, Any]] = []
    used = 0
    for unit in candidates:
        size = len(json.dumps(unit, ensure_ascii=False, sort_keys=True))
        if len(selected) >= options["max_facts"] or used + size > options["max_chars"]:
            continue
        selected.append(unit)
        used += size
    selected_ids = {item["unit_id"] for item in selected}
    # Do not hide that a competing/superseded claim exists when it does not fit.
    linked = {str(other) for item in selected for key in ("conflicts", "supersedes")
              for other in item.get(key, [])}
    return {
        "context_status": "available" if selected else "empty_or_budget_limited",
        "units": selected, "returned_chars": used,
        "omitted_count": len(candidates) - len(selected),
        "unexpanded_claim_ids": sorted(linked - selected_ids),
        "missing_state_fields": sorted(STATE_FIELDS - set(document.get("current_state") or {}))
            if options["view"] == "current" else [],
        "full_document_available": True,
        "selection_sha256": sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    }


def validate_units(document: dict[str, Any]) -> None:
    if "context_units" not in document:
        if document.get("current_state"):
            raise ValueError("current state has no reviewed units")
        return
    units = document["context_units"]
    if not isinstance(units, list):
        raise ValueError("context_units must be a list")
    ids: set[str] = set()
    for unit in units:
        if (not isinstance(unit, dict) or not isinstance(unit.get("unit_id"), str)
                or not unit["unit_id"] or unit["unit_id"] in ids
                or not isinstance(unit.get("text"), str) or not unit["text"].strip()
                or unit.get("status") not in {"confirmed", "observed", "agent-reported", "inferred",
                                               "disputed", "stale", "retracted", "unverified"}
                or not isinstance(unit.get("evidence_event_ids"), list) or not unit["evidence_event_ids"]):
            raise ValueError("invalid reviewed context unit")
        ids.add(unit["unit_id"])
        if any(not isinstance(ref, str) or not ref for ref in unit["evidence_event_ids"]):
            raise ValueError("invalid context-unit evidence reference")
        for key in ("conflicts", "supersedes"):
            if key in unit and (not isinstance(unit[key], list)
                                or any(not isinstance(ref, str) or not ref for ref in unit[key])):
                raise ValueError("invalid linked context-unit reference")
    state = document.get("current_state") or {}
    if (not isinstance(state, dict) or set(state) - STATE_FIELDS
            or any(not isinstance(v, str) or v not in ids for v in state.values())):
        raise ValueError("invalid current-state unit reference")
    by_id = {unit["unit_id"]: unit for unit in units}
    if any(by_id[ref]["status"] in {"stale", "retracted"} for ref in state.values()):
        raise ValueError("current state references an inactive unit")
