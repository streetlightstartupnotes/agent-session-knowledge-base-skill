from __future__ import annotations

import json
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from .adapters import AdapterRegistry
from .discovery import discover, freeze_sources
from .pipeline import build_knowledge_base


def _safe_child(root: Path, relative_value: Any, label: str) -> Path:
    root = root.expanduser().resolve()
    relative = Path(str(relative_value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe {label} path")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes fixture root") from exc
    if candidate.is_symlink():
        raise ValueError(f"{label} path cannot be a symlink")
    return candidate


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            values.append(value)
    return values


def run_golden_manifest(
    registry: AdapterRegistry,
    fixture_root: Path,
    manifest_path: Path,
    *,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """Run repository-safe synthetic or user-held private golden cases in isolation."""

    root = fixture_root.expanduser().resolve()
    manifest_file = manifest_path.expanduser().resolve()
    try:
        manifest_file.relative_to(root)
    except ValueError as exc:
        raise ValueError("golden manifest must be inside its fixture root") from exc
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise ValueError("golden manifest must be a regular file")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("golden_version") != 1:
        raise ValueError("unsupported golden manifest")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("golden manifest needs cases")

    owned_temporary: tempfile.TemporaryDirectory[str] | None = None
    if output_root is None:
        owned_temporary = tempfile.TemporaryDirectory()
        output_base = Path(owned_temporary.name)
    else:
        output_base = output_root.expanduser().resolve()
        output_base.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    materialized_temporary = tempfile.TemporaryDirectory()
    materialized_base = Path(materialized_temporary.name)
    try:
        seen_ids: set[str] = set()
        for raw_case in cases:
            if not isinstance(raw_case, dict):
                raise ValueError("golden case must be an object")
            case_id = str(raw_case.get("id") or "").strip()
            if not case_id or not case_id.replace("-", "").replace("_", "").isalnum() or case_id in seen_ids:
                raise ValueError("golden case id must be unique and portable")
            seen_ids.add(case_id)
            inline_files = raw_case.get("files")
            if inline_files is not None:
                if manifest.get("fixture_kind") != "public-synthetic" or not isinstance(inline_files, list) or not inline_files:
                    raise ValueError("inline golden files are allowed only for a non-empty public-synthetic case")
                source_root = materialized_base / case_id
                source_root.mkdir(parents=True)
                for file_item in inline_files:
                    if not isinstance(file_item, dict) or not isinstance(file_item.get("text"), str):
                        raise ValueError("inline golden file needs path and text")
                    target = _safe_child(source_root, file_item.get("path"), "inline golden file")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(file_item["text"])
            else:
                source_root = _safe_child(root, raw_case.get("source_root"), "golden source")
                if not source_root.is_dir():
                    raise ValueError("golden source root is missing")
            expected_adapters = {
                str(key): int(value)
                for key, value in (raw_case.get("expected_adapters") or {}).items()
                if str(key) and int(value) >= 0
            }
            if not expected_adapters:
                raise ValueError("golden case needs expected_adapters")
            discovery = discover(registry, roots=[source_root])
            snapshot = freeze_sources(discovery.sources)
            snapshot["discovery"] = discovery.to_dict()
            destination = output_base / case_id
            result = build_knowledge_base(
                registry,
                discovery.sources,
                destination,
                discovery=discovery,
                snapshot=snapshot,
                incremental=False,
                dry_run=False,
            )
            stats = result.get("stats") or {}
            events = _read_jsonl(destination / "audit" / "events.jsonl")
            event_types = {str(item.get("event_type") or "") for item in events}
            content = "\n".join(str(item.get("content") or "") for item in events)
            required_types = {str(item) for item in raw_case.get("required_event_types") or []}
            forbidden_text = [str(item) for item in raw_case.get("forbidden_text") or [] if str(item)]
            checks = {
                "adapter_counts": stats.get("adapter_counts") == expected_adapters,
                "minimum_events": len(events) >= int(raw_case.get("minimum_events") or 1),
                "required_event_types": required_types.issubset(event_types),
                "forbidden_text_absent": not any(item in content for item in forbidden_text),
                "unsupported_clear": not discovery.unsupported,
                "coverage_complete": not discovery.coverage_gaps,
                "discovery_clean": not discovery.errors,
                "parse_clean": bool((result.get("completion") or {}).get("gates", {}).get("parse_clean")),
                "transport_accounted": bool((result.get("completion") or {}).get("gates", {}).get("transport_accounted")),
            }
            results.append(
                {
                    "case_id": case_id,
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks,
                    "adapter_counts": stats.get("adapter_counts") or {},
                    "event_count": len(events),
                    "event_types": sorted(event_types),
                    "unsupported_count": len(discovery.unsupported),
                    "coverage_gap_count": len(discovery.coverage_gaps),
                    "error_count": len(discovery.errors),
                }
            )
        passed = all(item["status"] == "passed" for item in results)
        manifest_sha256 = sha256(manifest_file.read_bytes()).hexdigest()
        return {
            "golden_report_version": 1,
            "status": "passed" if passed else "failed",
            "fixture_kind": str(manifest.get("fixture_kind") or "unspecified"),
            "manifest_sha256": manifest_sha256,
            "case_count": len(results),
            "passed_count": sum(item["status"] == "passed" for item in results),
            "failed_count": sum(item["status"] == "failed" for item in results),
            "cases": results,
            "compatibility_claim_updated": False,
        }
    finally:
        materialized_temporary.cleanup()
        if owned_temporary is not None:
            owned_temporary.cleanup()
