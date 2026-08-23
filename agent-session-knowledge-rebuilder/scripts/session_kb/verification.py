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


RELATED_GATE = "retrieval_related_match"
UNRELATED_GATE = "retrieval_unrelated_no_match"


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
    if index.get("semantic_status") != "published" or completion.get("gates", {}).get("published_knowledge") is not True:
        raise ValueError("retrieval verification requires a semantically published knowledge base")
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
    passed = related_match and unrelated_no_match
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
        "status": "passed" if passed else "failed",
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
    completion["publication_manifest_sha256"] = manifest["sha256"]
    if passed:
        completion["retrieval_verified_at"] = report["verified_at"]
        completion["completed_at"] = report["verified_at"]
    else:
        completion.pop("retrieval_verified_at", None)
        completion.pop("completed_at", None)
    _atomic_json(completion_path, completion)
    return report
