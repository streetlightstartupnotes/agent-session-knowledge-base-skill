from __future__ import annotations

import json
from hashlib import sha256
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .adapters import AdapterRegistry, environment_root_hints
from .model import SourceFile, display_path


CANDIDATE_SUFFIXES = {".jsonl", ".ndjson", ".json", ".md", ".db", ".sqlite", ".sqlite3"}
GENERIC_SESSION_NAME = re.compile(r"session|conversation|history|chat|transcript|rollout|project|composer", re.IGNORECASE)
SKIP_PARTS = {
    ".git",
    "node_modules",
    "plugins",
    "skills",
    "cache",
    "caches",
    "file-history",
    "binaries",
    "connectors",
    "connectors-marketplace",
    "downloads",
    "assets",
    "docs",
    "documentation",
}
SNAPSHOT_BOUNDARY_BYTES = 65536
SNAPSHOT_VERSION = 2
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass
class DiscoveryResult:
    sources: list[SourceFile] = field(default_factory=list)
    roots: list[dict[str, Any]] = field(default_factory=list)
    unsupported: list[dict[str, Any]] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    probe_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        adapter_counts: dict[str, int] = {}
        agent_counts: dict[str, int] = {}
        for source in self.sources:
            adapter_counts[source.adapter] = adapter_counts.get(source.adapter, 0) + 1
            agent_counts[source.agent_name] = agent_counts.get(source.agent_name, 0) + 1
        return {
            "roots": self.roots,
            "supported_files": len(self.sources),
            "adapter_counts": dict(sorted(adapter_counts.items())),
            "agent_counts": dict(sorted(agent_counts.items())),
            "sources": [source.snapshot_dict() for source in self.sources],
            "unsupported": self.unsupported,
            "coverage_gaps": self.coverage_gaps,
            "errors": self.errors,
            "probe_counts": dict(sorted(self.probe_counts.items())),
        }


def _is_within(path: Path, parent: Path | None) -> bool:
    if parent is None:
        return False
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _candidate_files(root: Path, generic: bool, output_dir: Path | None) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    base_depth = len(root.parts)
    try:
        iterator = root.rglob("*")
        for path in iterator:
            suffix = path.suffix.lower()
            if not path.is_file() or (suffix not in CANDIDATE_SUFFIXES and not (not suffix and GENERIC_SESSION_NAME.search(path.name))):
                continue
            if _is_within(path, output_dir):
                continue
            relative_parts = path.relative_to(root).parts
            if any(part.lower() in SKIP_PARTS for part in relative_parts[:-1]):
                continue
            if len(path.parts) - base_depth > (8 if generic else 12):
                continue
            if suffix == ".md" and not re.search(r"-chunk-\d+\.md$", path.name, re.IGNORECASE) and not GENERIC_SESSION_NAME.search(path.name):
                continue
            if generic and not GENERIC_SESSION_NAME.search(path.as_posix()):
                continue
            yield path
    except OSError:
        return


def _read_head(path: Path, limit: int = 131072) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def discover(
    registry: AdapterRegistry,
    roots: list[Path] | None = None,
    explicit_sources: list[tuple[str, Path]] | None = None,
    output_dir: Path | None = None,
    max_files: int | None = None,
) -> DiscoveryResult:
    result = DiscoveryResult()
    requested: list[tuple[str, Path, str | None, bool]] = []
    if roots:
        requested.extend(("explicit-root", path.expanduser(), None, False) for path in roots)
    if explicit_sources:
        requested.extend(("explicit-source", path.expanduser(), adapter_name, False) for adapter_name, path in explicit_sources)
    if not requested:
        for label, path in environment_root_hints():
            requested.append((label, path, None, label in {"environment-candidate", "application-candidate"}))

    seen_roots: set[Path] = set()
    seen_files: set[Path] = set()
    unsupported_limit = 2000
    for label, root, forced_adapter, generic in requested:
        try:
            resolved_root = root.resolve()
        except (OSError, RuntimeError):
            resolved_root = root
        root_key = (resolved_root, forced_adapter, generic)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        root_record = {
            "label": label,
            "path": display_path(root),
            "exists": root.exists(),
            "forced_adapter": forced_adapter,
            "generic_probe": generic,
            "supported_files": 0,
            "candidate_files": 0,
            "scanned_candidate_files": 0,
            "scan_status": "complete",
        }
        result.roots.append(root_record)
        if not root.exists():
            continue
        candidates = list(_candidate_files(root, generic=generic, output_dir=output_dir))
        root_record["candidate_files"] = len(candidates)
        limit_reached = False
        for path in sorted(candidates):
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                resolved = path
            if resolved in seen_files:
                root_record["supported_files"] += 1
                continue
            if max_files is not None and len(result.sources) >= max_files:
                limit_reached = True
                break
            root_record["scanned_candidate_files"] += 1
            try:
                head = _read_head(path)
                stat = path.stat()
            except OSError as exc:
                result.errors.append({"path": display_path(path), "error": "source_read_error", "detail_type": type(exc).__name__})
                continue
            if forced_adapter:
                try:
                    adapter = registry.get(forced_adapter)
                except ValueError as exc:
                    result.errors.append({"path": display_path(path), "error": "unknown_forced_adapter", "detail_type": type(exc).__name__})
                    continue
                score, reason = adapter.probe(path, head)
                audit = [{"adapter": adapter.name, "score": score, "reason": reason}]
                if score <= 0:
                    adapter = None
            else:
                adapter, audit = registry.identify(path, head)
            for probe in audit:
                key = probe["adapter"] + ":" + str(probe["score"])
                result.probe_counts[key] = result.probe_counts.get(key, 0) + 1
            if adapter is None:
                if len(result.unsupported) < unsupported_limit:
                    result.unsupported.append(
                        {
                            "path": display_path(path),
                            "reason": "unsupported_or_ambiguous_format",
                            "probes": audit,
                        }
                    )
                continue
            seen_files.add(resolved)
            root_record["supported_files"] += 1
            result.sources.append(
                SourceFile(
                    path=path,
                    root=root if root.is_dir() else root.parent,
                    adapter=adapter.name,
                    agent_name=adapter.agent_name,
                    frozen_size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    inode=getattr(stat, "st_ino", 0),
                )
            )
        if limit_reached:
            root_record["scan_status"] = "truncated_by_max_files"
            result.coverage_gaps.append(
                {
                    "path": display_path(root),
                    "reason": "max_files_limit_reached",
                    "candidate_files": root_record["candidate_files"],
                    "scanned_candidate_files": root_record["scanned_candidate_files"],
                }
            )
        if root_record["exists"] and root_record["supported_files"] == 0 and not limit_reached:
            result.unsupported.append(
                {
                    "path": display_path(root),
                    "reason": "no_supported_transcript_sample_found",
                    "candidate_files": root_record["candidate_files"],
                }
            )
    result.sources.sort(key=lambda item: (item.agent_name, item.relative_path, item.source_file_id))
    return result


def _hash_exact_prefix(path: Path, size: int) -> str:
    digest = sha256()
    remaining = size
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(HASH_CHUNK_BYTES, remaining))
            if not chunk:
                raise OSError("source became shorter while freezing")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def freeze_sources(sources: list[SourceFile]) -> dict[str, Any]:
    frozen: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for source in sources:
        try:
            stat = source.path.stat()
        except OSError as exc:
            errors.append({"path": display_path(source.path), "error": "freeze_stat_error", "detail_type": type(exc).__name__})
            continue
        source.frozen_size = stat.st_size
        source.mtime_ns = stat.st_mtime_ns
        source.inode = getattr(stat, "st_ino", 0)
        item = source.snapshot_dict()
        try:
            head_length = min(SNAPSHOT_BOUNDARY_BYTES, stat.st_size)
            tail_length = min(SNAPSHOT_BOUNDARY_BYTES, stat.st_size)
            tail_start = max(0, stat.st_size - tail_length)
            with source.path.open("rb") as handle:
                head = handle.read(head_length)
                handle.seek(tail_start)
                tail = handle.read(tail_length)
            frozen_sha256 = _hash_exact_prefix(source.path, stat.st_size)
            after = source.path.stat()
            if (
                after.st_size != stat.st_size
                or after.st_mtime_ns != stat.st_mtime_ns
                or (getattr(stat, "st_ino", 0) and getattr(after, "st_ino", 0) != getattr(stat, "st_ino", 0))
            ):
                raise OSError("source changed while freezing")
            item.update(
                {
                    "head_length": head_length,
                    "head_sha256": sha256(head).hexdigest(),
                    "tail_start": tail_start,
                    "tail_length": tail_length,
                    "tail_sha256": sha256(tail).hexdigest(),
                    "frozen_sha256": frozen_sha256,
                }
            )
        except OSError as exc:
            errors.append({"path": display_path(source.path), "error": "freeze_hash_error", "detail_type": type(exc).__name__})
            continue
        frozen.append(item)
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requested_source_count": len(sources),
        "source_count": len(frozen),
        "sources": frozen,
        "errors": errors,
        "complete": not errors and len(frozen) == len(sources),
    }


def validate_snapshot(value: dict[str, Any], sources: list[SourceFile] | None = None) -> None:
    if not isinstance(value, dict) or value.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError("invalid or legacy snapshot; create a fresh version-2 snapshot")
    rows = value.get("sources")
    if not isinstance(rows, list):
        raise ValueError("invalid snapshot: expected a sources list")
    source_count = int(value.get("source_count", -1))
    requested_count = int(value.get("requested_source_count", -1))
    if value.get("errors") or value.get("complete") is not True or source_count != len(rows) or requested_count != source_count:
        raise ValueError("snapshot is incomplete and cannot be consumed")
    ids: set[str] = set()
    for item in rows:
        if not isinstance(item, dict) or not item.get("source_file_id") or not item.get("frozen_sha256"):
            raise ValueError("snapshot source is missing an identity or full frozen digest")
        source_id = str(item["source_file_id"])
        if source_id in ids:
            raise ValueError("snapshot contains a duplicate source identity")
        ids.add(source_id)
    discovery = value.get("discovery")
    if isinstance(discovery, dict) and int(discovery.get("supported_files", -1)) != requested_count:
        raise ValueError("snapshot source count does not match its discovery denominator")
    if sources is not None and ids != {source.source_file_id for source in sources}:
        raise ValueError("snapshot source identities do not match the requested rebuild sources")


def source_from_snapshot(item: dict[str, Any]) -> SourceFile:
    raw_path = str(item["path"])
    raw_root = str(item.get("root") or Path(raw_path).parent)
    if raw_path == "~" or raw_path.startswith("~/"):
        path = Path(raw_path).expanduser()
    else:
        path = Path(raw_path)
    if raw_root == "~" or raw_root.startswith("~/"):
        root = Path(raw_root).expanduser()
    else:
        root = Path(raw_root)
    return SourceFile(
        path=path,
        root=root,
        adapter=str(item["adapter"]),
        agent_name=str(item.get("agent_name") or "Unknown"),
        frozen_size=int(item["frozen_size"]),
        mtime_ns=int(item.get("mtime_ns", 0)),
        inode=int(item.get("inode", 0)),
        source_file_id=str(item.get("source_file_id") or ""),
    )


def load_snapshot(path: Path) -> tuple[dict[str, Any], list[SourceFile]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_snapshot(value)
    sources = [source_from_snapshot(item) for item in value["sources"]]
    validate_snapshot(value, sources)
    return value, sources
