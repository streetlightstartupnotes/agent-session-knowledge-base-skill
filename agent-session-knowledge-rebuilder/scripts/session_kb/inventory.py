from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from . import VERIFIED_ADAPTERS
from .adapters import AdapterRegistry
from .discovery import DiscoveryResult, discover
from .model import display_path


EXECUTABLE_CANDIDATES = (
    "codex",
    "clacky",
    "claude",
    "workbuddy",
    "cursor",
    "windsurf",
    "trae",
    "gemini",
    "opencode",
    "aider",
    "goose",
    "amp",
    "qwen",
    "kimi",
    "grok",
    "crush",
    "zed",
    "neo",
    "cline",
    "roo",
    "continue",
    "cody",
    "openhands",
    "plandex",
    "mentat",
    "gptme",
    "tabby",
    "sweep",
)


def _safe_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key)[:80] for key in value)[:100]


def _safe_value_types(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for record in records:
        for key, value in record.items():
            safe_key = str(key)[:80]
            result.setdefault(safe_key, set()).add(type(value).__name__)
    return {key: sorted(types) for key, types in sorted(result.items())[:100]}


def fingerprint_unknown(path: Path, limit: int = 131072) -> dict[str, Any]:
    stat = path.stat()
    with path.open("rb") as handle:
        head = handle.read(limit)
    result: dict[str, Any] = {
        "path": display_path(path),
        "suffix": path.suffix.lower() or "[none]",
        "size_bytes": stat.st_size,
        "fingerprint_only": True,
    }
    if head.startswith(b"SQLite format 3\x00"):
        result["container"] = "sqlite"
        return result
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        records: list[dict[str, Any]] = []
        invalid = 0
        for line in head.splitlines()[:32]:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                invalid += 1
                continue
            if isinstance(value, dict):
                records.append(value)
        result.update(
            {
                "container": "jsonl",
                "sample_records_parsed": len(records),
                "sample_records_invalid": invalid,
                "top_level_keys": sorted({key for record in records for key in _safe_keys(record)})[:100],
                "value_types_by_key": _safe_value_types(records),
            }
        )
        return result
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(head.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            result["container"] = "json-or-binary-unparsed"
        else:
            result["container"] = "json"
            result["root_type"] = type(value).__name__
            result["top_level_keys"] = _safe_keys(value)
        return result
    if path.suffix.lower() == ".md":
        text = head.decode("utf-8", errors="replace")
        result["container"] = "markdown"
        result["heading_count_in_sample"] = sum(line.lstrip().startswith("#") for line in text.splitlines())
        return result
    result["container"] = "unknown"
    return result


def inventory_environment(
    registry: AdapterRegistry,
    roots: list[Path] | None = None,
    output_dir: Path | None = None,
    max_files: int | None = None,
    fingerprint_limit: int = 100,
) -> tuple[dict[str, Any], DiscoveryResult]:
    discovery = discover(registry, roots=roots, output_dir=output_dir, max_files=max_files)
    commands = [
        {"name": name, "path": display_path(Path(found))}
        for name in EXECUTABLE_CANDIDATES
        if (found := shutil.which(name))
    ]
    fingerprints: list[dict[str, Any]] = []
    fingerprint_errors: list[dict[str, Any]] = []
    for item in discovery.unsupported:
        if len(fingerprints) >= fingerprint_limit or item.get("reason") != "unsupported_or_ambiguous_format":
            continue
        raw_path = str(item.get("path") or "")
        path = Path(raw_path).expanduser()
        if not path.is_file():
            continue
        try:
            fingerprints.append(fingerprint_unknown(path))
        except OSError as exc:
            fingerprint_errors.append({"path": display_path(path), "error": type(exc).__name__})
    adapter_counts = discovery.to_dict()["adapter_counts"]
    format_status = []
    for adapter in sorted(registry.adapters):
        recognized = int(adapter_counts.get(adapter, 0))
        format_status.append(
            {
                "adapter": adapter,
                "recognized_files": recognized,
                "status": "verified-and-present" if recognized and adapter in VERIFIED_ADAPTERS else ("implemented-no-current-sample" if not recognized else "implemented-unverified"),
            }
        )
    report = {
        "inventory_version": 1,
        "platform": {
            "system": platform.system() or os.name,
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "candidate_roots": discovery.roots,
        "installed_agent_commands": commands,
        "supported_files": len(discovery.sources),
        "adapter_counts": adapter_counts,
        "format_status": format_status,
        "unknown_or_ambiguous_candidates": len([item for item in discovery.unsupported if item.get("reason") == "unsupported_or_ambiguous_format"]),
        "unknown_fingerprints": fingerprints,
        "fingerprint_errors": fingerprint_errors,
        "coverage_gaps": discovery.coverage_gaps,
        "discovery_errors": discovery.errors,
        "truth_boundary": "Presence is not compatibility. Unknown formats remain unsupported until an adapter passes tests and a real-sample smoke run.",
    }
    return report, discovery
