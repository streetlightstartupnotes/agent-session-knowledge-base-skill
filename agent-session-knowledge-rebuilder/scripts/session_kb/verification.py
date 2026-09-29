from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .query import load_validated_graph, query_knowledge
from .sanitize import Sanitizer
from .context_units import RETRIEVAL_ENGINE_VERSION, selection_options


RELATED_GATE = "retrieval_related_match"
UNRELATED_GATE = "retrieval_unrelated_no_match"
RELATED_SUITE_GATE = "retrieval_related_suite"
HARD_NEGATIVE_SUITE_GATE = "retrieval_hard_negative_suite"
RETRIEVAL_CONTRACT_VERSION = 3
MIN_RELATED_CASES = 2
MIN_HARD_NEGATIVE_CASES = 2
DEFAULT_RETRIEVAL_PROFILE = {"min_score": 4, "max_projects": 3, "max_related": 2}
PUBLICATION_PREREQUISITE_GATES = (
    "frozen_snapshot",
    "transport_accounted",
    "parse_clean",
    "discovery_coverage_complete",
    "semantic_review_complete",
    "knowledge_graph_complete",
    "published_knowledge",
)


def _kb_root(kb: Path) -> Path:
    expanded = kb.expanduser().resolve()
    if (expanded / "audit" / "completion-report.json").is_file() and (expanded / "knowledge" / "knowledge-index.json").is_file():
        return expanded
    if expanded.name == "knowledge" and (expanded.parent / "audit" / "completion-report.json").is_file():
        return expanded.parent
    raise ValueError(f"published knowledge base not found under {expanded}")


def _json_read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sanitize_task(task: str) -> tuple[str, dict[str, int]]:
    sanitizer = Sanitizer()
    cleaned = sanitizer.sanitize_text(task).strip()
    return cleaned, dict(sorted(sanitizer.stats.items()))


def _task_fingerprint(task: str) -> str:
    return sha256(task.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256(payload).hexdigest()


def require_publication_prerequisites(completion: dict[str, Any]) -> None:
    """Refuse retrieval finalization when an earlier evidence gate is absent."""

    gates = completion.get("gates") if isinstance(completion.get("gates"), dict) else {}
    missing = [gate for gate in PUBLICATION_PREREQUISITE_GATES if gates.get(gate) is not True]
    if gates.get("unsupported_formats_clear") is not True and gates.get("unsupported_formats_acknowledged") is not True:
        missing.append("unsupported_formats_acknowledged")
    if missing:
        raise ValueError("retrieval verification prerequisite gates are incomplete: " + ", ".join(missing))


def require_verified_retrieval_report(root: Path, completion: dict[str, Any]) -> dict[str, Any]:
    """Bind a final v0.5 completion to its complete multi-case audit report."""

    if int(completion.get("retrieval_contract_version") or 1) < 2:
        return {}
    report_path = root.expanduser().resolve() / "audit" / "retrieval-verification.json"
    if not report_path.is_file():
        raise ValueError("retrieval suite audit report is missing")
    report = _json_read(report_path)
    expected_digest = str(completion.get("retrieval_verification_sha256") or "")
    if not expected_digest or _canonical_sha256(report) != expected_digest:
        raise ValueError("retrieval suite audit report does not match the verified completion")
    if (
        report.get("report_version") != 2
        or int(report.get("retrieval_contract_version") or 0) not in {2, 3}
        or report.get("retrieval_contract_version") != completion.get("retrieval_contract_version")
        or report.get("run_id") != completion.get("run_id")
        or report.get("status") != "passed"
        or report.get("suite_sha256") != completion.get("retrieval_suite_sha256")
    ):
        raise ValueError("retrieval suite audit metadata is invalid")
    profile = report.get("retrieval_profile")
    if report.get("retrieval_contract_version") == 3 and "retrieval_engine_version" not in report:
        raise ValueError("retrieval contract 3 requires an engine and selection binding")
    if "retrieval_engine_version" in report:
        if (report["retrieval_engine_version"] != RETRIEVAL_ENGINE_VERSION
                or completion.get("retrieval_engine_version") != RETRIEVAL_ENGINE_VERSION
                or selection_options(report.get("selection_options")) != report.get("selection_options")
                or report.get("selection_options") != completion.get("selection_options")):
            raise ValueError("retrieval engine or selection options changed; rerun the suite")
    if (
        not isinstance(profile, dict)
        or set(profile) != set(DEFAULT_RETRIEVAL_PROFILE)
        or any(not isinstance(profile.get(key), int) or isinstance(profile.get(key), bool) for key in DEFAULT_RETRIEVAL_PROFILE)
        or not 0 <= int(profile["min_score"]) <= 10_000
        or not 1 <= int(profile["max_projects"]) <= 20
        or not 0 <= int(profile["max_related"]) <= 20
        or profile != completion.get("retrieval_profile")
    ):
        raise ValueError("retrieval suite profile is invalid or does not match completion")
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    if (
        (checks.get(RELATED_SUITE_GATE) or {}).get("passed") is not True
        or (checks.get(HARD_NEGATIVE_SUITE_GATE) or {}).get("passed") is not True
    ):
        raise ValueError("retrieval suite audit checks are incomplete")
    counts = report.get("case_counts") if isinstance(report.get("case_counts"), dict) else {}
    cases = report.get("cases") if isinstance(report.get("cases"), list) else []
    total = int(counts.get("total") or 0)
    related = int(counts.get("related") or 0)
    negatives = int(counts.get("hard_negative") or 0)
    passed = int(counts.get("passed") or 0)
    failed = int(counts.get("failed") or 0)
    if (
        related < MIN_RELATED_CASES
        or negatives < MIN_HARD_NEGATIVE_CASES
        or total != related + negatives
        or len(cases) != total
        or passed != total
        or failed != 0
        or any(not isinstance(case, dict) or case.get("passed") is not True for case in cases)
        or sum(case.get("kind") == "related" for case in cases if isinstance(case, dict)) != related
        or sum(case.get("kind") == "hard_negative" for case in cases if isinstance(case, dict)) != negatives
    ):
        raise ValueError("retrieval suite audit case accounting is invalid")
    manifest = report.get("publication_manifest") if isinstance(report.get("publication_manifest"), dict) else {}
    if manifest.get("sha256") != completion.get("publication_manifest_sha256"):
        raise ValueError("retrieval suite audit publication manifest does not match completion")
    return report


def publication_manifest(root: Path, index: dict[str, Any]) -> dict[str, Any]:
    root = root.expanduser().resolve()
    knowledge_root = (root / "knowledge").resolve()
    # Bind the manifest to the exact graph the query path will use, and reject
    # graph nodes that point outside the indexed document allowlist.
    load_validated_graph(knowledge_root, index)
    relative_paths = {"knowledge-index.json", str((index.get("graph") or {}).get("path") or "knowledge-graph.json")}
    relative_paths.update(str(item.get("path")) for item in index.get("documents") or [] if isinstance(item, dict) and item.get("path"))
    files: list[dict[str, Any]] = []
    for relative_value in sorted(relative_paths):
        relative = Path(relative_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe published knowledge path: {relative}")
        path = (knowledge_root / relative).resolve()
        try:
            path.relative_to(knowledge_root)
        except ValueError as exc:
            raise ValueError(f"published knowledge path escapes root: {relative}") from exc
        if not path.is_file():
            raise ValueError(f"published knowledge file is missing: {relative}")
        data = path.read_bytes()
        files.append({"path": relative.as_posix(), "size": len(data), "sha256": sha256(data).hexdigest()})
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"manifest_version": 1, "files": files, "sha256": sha256(payload).hexdigest()}


def _query_summary(result: dict[str, Any], task: str, sanitizer_stats: dict[str, int]) -> dict[str, Any]:
    project_keys = sorted(
        {
            str(document["project_key"])
            for document in result.get("documents") or []
            if isinstance(document, dict) and document.get("project_key")
        }
    )
    return {
        "task_sha256": _task_fingerprint(task),
        "task_redactions": sanitizer_stats,
        "match_status": result.get("match_status"),
        "selected_count": result.get("selected_count"),
        "project_matches": result.get("project_matches"),
        "related_documents": result.get("related_documents", 0),
        "selected_project_keys": project_keys,
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def _normalize_eval_cases(eval_set: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not isinstance(eval_set, dict):
        raise ValueError("retrieval eval set must be a JSON object")
    cases = eval_set.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("retrieval eval set needs a non-empty cases list")
    raw_profile = eval_set.get("retrieval_profile") or {}
    if not isinstance(raw_profile, dict) or set(raw_profile) - set(DEFAULT_RETRIEVAL_PROFILE):
        raise ValueError("retrieval_profile may contain only min_score, max_projects, and max_related")
    profile = {
        "min_score": _bounded_int(
            raw_profile.get("min_score"),
            default=DEFAULT_RETRIEVAL_PROFILE["min_score"],
            minimum=0,
            maximum=10_000,
            label="retrieval_profile.min_score",
        ),
        "max_projects": _bounded_int(
            raw_profile.get("max_projects"),
            default=DEFAULT_RETRIEVAL_PROFILE["max_projects"],
            minimum=1,
            maximum=20,
            label="retrieval_profile.max_projects",
        ),
        "max_related": _bounded_int(
            raw_profile.get("max_related"),
            default=DEFAULT_RETRIEVAL_PROFILE["max_related"],
            minimum=0,
            maximum=20,
            label="retrieval_profile.max_related",
        ),
    }
    normalized: list[dict[str, Any]] = []
    selection = selection_options(eval_set.get("selection_options"))
    seen_hashes: set[str] = set()
    for position, raw in enumerate(cases):
        if not isinstance(raw, dict):
            raise ValueError(f"retrieval eval case {position} must be an object")
        kind = str(raw.get("kind") or "").strip().lower().replace("-", "_")
        if kind not in {"related", "hard_negative"}:
            raise ValueError(f"retrieval eval case {position} has unsupported kind")
        per_case_query_fields = sorted(set(raw) & (set(DEFAULT_RETRIEVAL_PROFILE) | set(selection)))
        if per_case_query_fields:
            raise ValueError(
                f"retrieval eval case {position} cannot override the suite retrieval profile: {', '.join(per_case_query_fields)}"
            )
        task = str(raw.get("task") or "")
        safe_task, redactions = _sanitize_task(task)
        if not safe_task:
            raise ValueError(f"retrieval eval case {position} is empty after privacy cleanup")
        task_hash = _task_fingerprint(safe_task)
        if task_hash in seen_hashes:
            raise ValueError("retrieval eval tasks must be distinct after privacy cleanup")
        seen_hashes.add(task_hash)
        expected_projects = sorted({str(item).strip() for item in raw.get("expected_project_keys") or [] if str(item).strip()})
        expected_types = sorted({str(item).strip() for item in raw.get("expected_document_types") or [] if str(item).strip()})
        if kind == "hard_negative" and (expected_projects or expected_types):
            raise ValueError("hard-negative cases cannot declare expected matches")
        default_project_matches = 0 if kind == "related" and expected_types and not expected_projects else (1 if kind == "related" else 0)
        normalized.append(
            {
                "kind": kind,
                "task": safe_task,
                "task_sha256": task_hash,
                "task_redactions": redactions,
                "expected_project_keys": expected_projects,
                "expected_document_types": expected_types,
                "min_context_units": _bounded_int(raw.get("min_context_units"),
                    default=1 if kind == "related" and selection["view"] != "documents" else 0,
                    minimum=0, maximum=1000, label="min_context_units"),
                "min_project_matches": _bounded_int(
                    raw.get("min_project_matches"),
                    default=default_project_matches,
                    minimum=0,
                    maximum=20,
                    label="min_project_matches",
                ),
                **profile,
            }
        )
    return normalized, profile


def _suite_case_summary(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    context_count = sum(len(item.get("units", [])) for item in result.get("documents", []))
    selected_projects = sorted(
        {
            str(document.get("project_key"))
            for document in result.get("documents") or []
            if isinstance(document, dict) and document.get("project_key")
        }
    )
    selected_types = sorted(
        {
            str(document.get("type"))
            for document in result.get("documents") or []
            if isinstance(document, dict) and document.get("type")
        }
    )
    expected_projects = set(case["expected_project_keys"])
    expected_types = set(case["expected_document_types"])
    documents = result.get("documents") or []
    # Each declared target must supply its own evidence. Graph neighbours cannot
    # compensate for unavailable facts/current state in the expected project.
    target_counts = [sum(len(d.get("units", [])) for d in documents if d.get("project_key") == key)
                     for key in sorted(expected_projects)]
    target_counts.extend(sum(len(d.get("units", [])) for d in documents if d.get("type") == kind)
                         for kind in sorted(expected_types))
    if not target_counts:
        target_counts = [sum(len(d.get("units", [])) for d in documents if not d.get("relationship") and d.get("type") != "evidence")]
    if case["kind"] == "related":
        passed = (
            result.get("match_status") == "matched"
            and int(result.get("project_matches") or 0) >= int(case["min_project_matches"])
            and expected_projects.issubset(set(selected_projects))
            and expected_types.issubset(set(selected_types))
            and all(count >= case["min_context_units"] for count in target_counts)
        )
    else:
        passed = result.get("match_status") == "no_match" and int(result.get("project_matches") or 0) == 0
    return {
        "kind": case["kind"],
        "task_sha256": case["task_sha256"],
        "task_redactions": case["task_redactions"],
        "passed": passed,
        "match_status": result.get("match_status"),
        "selected_count": int(result.get("selected_count") or 0),
        "project_matches": int(result.get("project_matches") or 0),
        "related_documents": int(result.get("related_documents") or 0),
        "selected_project_key_sha256": [_task_fingerprint(item) for item in selected_projects],
        "selected_document_types": selected_types,
        "expected_project_key_sha256": [_task_fingerprint(item) for item in sorted(expected_projects)],
        "expected_document_types": sorted(expected_types),
        "min_project_matches": int(case["min_project_matches"]),
        "min_context_units": case["min_context_units"],
        "context_unit_count": context_count,
        "minimum_target_context_units": min(target_counts),
        "returned_content_chars": sum(len(item.get("content", "")) + item.get("returned_chars", 0)
                                      for item in result.get("documents", [])),
        "query_parameters": {
            "min_score": int(case["min_score"]),
            "max_projects": int(case["max_projects"]),
            "max_related": int(case["max_related"]),
        },
    }


def verify_retrieval_suite(
    kb: Path,
    eval_set: dict[str, Any],
    *,
    minimum_related: int = MIN_RELATED_CASES,
    minimum_hard_negatives: int = MIN_HARD_NEGATIVE_CASES,
) -> dict[str, Any]:
    """Run a privacy-safe multi-case retrieval evaluation and update final gates."""

    root = _kb_root(kb)
    completion_path = root / "audit" / "completion-report.json"
    index_path = root / "knowledge" / "knowledge-index.json"
    completion = _json_read(completion_path)
    index = _json_read(index_path)
    run_id = str(index.get("run_id") or "")
    if not run_id or str(completion.get("run_id") or "") != run_id:
        raise ValueError("completion run_id does not match published knowledge-index run_id")
    if index.get("semantic_status") != "published":
        raise ValueError("retrieval verification requires a semantically published knowledge base")
    require_publication_prerequisites(completion)

    cases, retrieval_profile = _normalize_eval_cases(eval_set)
    selection = selection_options(eval_set.get("selection_options"))
    related_count = sum(case["kind"] == "related" for case in cases)
    negative_count = sum(case["kind"] == "hard_negative" for case in cases)
    if related_count < minimum_related:
        raise ValueError(f"retrieval eval set needs at least {minimum_related} distinct related cases")
    if negative_count < minimum_hard_negatives:
        raise ValueError(f"retrieval eval set needs at least {minimum_hard_negatives} distinct hard-negative cases")

    manifest = publication_manifest(root, index)
    summaries: list[dict[str, Any]] = []
    for case in cases:
        result = query_knowledge(
            root,
            case["task"],
            max_projects=case["max_projects"],
            emit_content=selection["view"] != "documents",
            min_score=case["min_score"],
            max_related=case["max_related"],
            **selection,
        )
        summaries.append(_suite_case_summary(case, result))

    related_pass = all(item["passed"] for item in summaries if item["kind"] == "related")
    hard_negative_pass = all(item["passed"] for item in summaries if item["kind"] == "hard_negative")
    passed = related_pass and hard_negative_pass
    has_unsupported = completion.get("gates", {}).get("unsupported_formats_clear") is not True
    final_status = (
        "complete_with_unsupported_formats" if passed and has_unsupported else "complete" if passed else "needs_retrieval_verification"
    )
    suite_fingerprint_payload = [
        {
            "kind": item["kind"],
            "task_sha256": item["task_sha256"],
            "expected_project_key_sha256": item["expected_project_key_sha256"],
            "expected_document_types": item["expected_document_types"],
            "min_project_matches": item["min_project_matches"],
            "min_context_units": item["min_context_units"],
            "query_parameters": item["query_parameters"],
        }
        for item in summaries
    ]
    suite_sha256 = _canonical_sha256({"retrieval_profile": retrieval_profile, "cases": suite_fingerprint_payload,
                                     "selection_options": selection, "retrieval_engine_version": RETRIEVAL_ENGINE_VERSION})
    verified_at = datetime.now(timezone.utc).isoformat()
    report = {
        "report_version": 2,
        "retrieval_contract_version": RETRIEVAL_CONTRACT_VERSION,
        "run_id": run_id,
        "status": "passed" if passed else "failed",
        "verified_at": verified_at,
        "suite_sha256": suite_sha256,
        "retrieval_profile": retrieval_profile,
        "selection_options": selection,
        "retrieval_engine_version": RETRIEVAL_ENGINE_VERSION,
        "case_counts": {
            "total": len(summaries),
            "related": related_count,
            "hard_negative": negative_count,
            "passed": sum(item["passed"] for item in summaries),
            "failed": sum(not item["passed"] for item in summaries),
        },
        "checks": {
            RELATED_SUITE_GATE: {"passed": related_pass, "case_count": related_count},
            HARD_NEGATIVE_SUITE_GATE: {"passed": hard_negative_pass, "case_count": negative_count},
        },
        "cases": summaries,
        "completion_status": final_status,
        "content_persisted": False,
        "publication_manifest": manifest,
    }
    _atomic_json(root / "audit" / "retrieval-verification.json", report)
    gates = completion.setdefault("gates", {})
    gates[RELATED_GATE] = related_pass
    gates[UNRELATED_GATE] = hard_negative_pass
    gates[RELATED_SUITE_GATE] = related_pass
    gates[HARD_NEGATIVE_SUITE_GATE] = hard_negative_pass
    completion["retrieval_contract_version"] = RETRIEVAL_CONTRACT_VERSION
    completion["retrieval_suite_sha256"] = suite_sha256
    completion["retrieval_profile"] = retrieval_profile
    completion["selection_options"] = selection
    completion["retrieval_engine_version"] = RETRIEVAL_ENGINE_VERSION
    completion["retrieval_verification_sha256"] = _canonical_sha256(report)
    completion["status"] = final_status
    completion["retrieval_verification_attempted_at"] = verified_at
    completion["publication_manifest_sha256"] = manifest["sha256"]
    if passed:
        completion["retrieval_verified_at"] = verified_at
        completion["completed_at"] = verified_at
    else:
        completion.pop("retrieval_verified_at", None)
        completion.pop("completed_at", None)
    _atomic_json(completion_path, completion)
    return report


def verify_retrieval(
    kb: Path,
    related_task: str,
    unrelated_task: str,
    expected_project_key: str | None = None,
) -> dict[str, Any]:
    """Verify positive and negative retrieval without persisting task or document content."""

    root = _kb_root(kb)
    completion_path = root / "audit" / "completion-report.json"
    index_path = root / "knowledge" / "knowledge-index.json"
    completion = _json_read(completion_path)
    index = _json_read(index_path)
    run_id = str(index.get("run_id") or "")
    if not run_id or str(completion.get("run_id") or "") != run_id:
        raise ValueError("completion run_id does not match published knowledge-index run_id")
    if index.get("semantic_status") != "published":
        raise ValueError("retrieval verification requires a semantically published knowledge base")
    require_publication_prerequisites(completion)
    manifest = publication_manifest(root, index)

    safe_related, related_redactions = _sanitize_task(related_task)
    safe_unrelated, unrelated_redactions = _sanitize_task(unrelated_task)
    related_result = query_knowledge(root, safe_related, emit_content=False)
    unrelated_result = query_knowledge(root, safe_unrelated, emit_content=False)
    related_summary = _query_summary(related_result, safe_related, related_redactions)
    unrelated_summary = _query_summary(unrelated_result, safe_unrelated, unrelated_redactions)

    expected = str(expected_project_key or "").strip()
    related_match = related_result.get("match_status") == "matched" and int(related_result.get("project_matches") or 0) > 0
    expected_match: bool | None = None
    if expected:
        expected_match = expected in set(related_summary["selected_project_keys"])
        related_match = related_match and expected_match
    unrelated_no_match = unrelated_result.get("match_status") == "no_match"
    pair_passed = related_match and unrelated_no_match
    requires_suite = int(completion.get("retrieval_contract_version") or 1) >= 2
    passed = pair_passed and not requires_suite
    has_unsupported = completion.get("gates", {}).get("unsupported_formats_clear") is not True
    final_status = (
        "complete_with_unsupported_formats" if passed and has_unsupported else "complete" if passed else "needs_retrieval_verification"
    )

    related_summary["passed"] = related_match
    related_summary["expected_project_key_required"] = bool(expected)
    related_summary["expected_project_key_matched"] = expected_match
    if expected:
        related_summary["expected_project_key_sha256"] = _task_fingerprint(expected)
    unrelated_summary["passed"] = unrelated_no_match
    report = {
        "report_version": 1,
        "run_id": run_id,
        "status": "passed" if passed else "legacy_pair_passed_needs_suite" if pair_passed and requires_suite else "failed",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            RELATED_GATE: related_summary,
            UNRELATED_GATE: unrelated_summary,
        },
        "completion_status": final_status,
        "content_persisted": False,
        "publication_manifest": manifest,
    }

    # Write the audit first. A crash between these writes leaves publication
    # fail-closed because the completion gates remain false.
    _atomic_json(root / "audit" / "retrieval-verification.json", report)
    gates = completion.setdefault("gates", {})
    gates[RELATED_GATE] = related_match
    gates[UNRELATED_GATE] = unrelated_no_match
    completion["status"] = final_status
    completion["retrieval_verification_attempted_at"] = report["verified_at"]
    completion["retrieval_engine_version"] = RETRIEVAL_ENGINE_VERSION
    completion["selection_options"] = selection_options()
    completion["publication_manifest_sha256"] = manifest["sha256"]
    if passed:
        completion["retrieval_verified_at"] = report["verified_at"]
        completion["completed_at"] = report["verified_at"]
    else:
        completion.pop("retrieval_verified_at", None)
        completion.pop("completed_at", None)
    _atomic_json(completion_path, completion)
    return report
