from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .model import SourceFile
from .locking import file_mutation_lock
from .verification import publication_manifest


REGISTRY_VERSION = 1
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def platform_family() -> str:
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_registry_path() -> Path:
    override = os.environ.get("AGENT_KB_REGISTRY")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    family = platform_family()
    if family == "windows":
        base = Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or home / "AppData" / "Roaming")
    elif family == "macos":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "agent-session-knowledge-base" / "locations.json"


def _safe_name(name: str) -> str:
    cleaned = name.strip()
    if not NAME_RE.fullmatch(cleaned):
        raise ValueError("knowledge-base name must use 1-64 letters, digits, dots, underscores, or hyphens")
    return cleaned


def output_guidance(name: str, cwd: Path | None = None) -> dict[str, Any]:
    safe_name = _safe_name(name)
    home = Path.home()
    current = (cwd or Path.cwd()).expanduser().resolve()
    personal = home / "Documents" / "Agent Knowledge Bases" / safe_name
    project_local = current / ".agent-knowledge" / safe_name
    local_skills_root = Path(__file__).resolve().parents[3]
    project_unsafe = (
        _ancestor_marker(project_local.parent, ".git") is not None
        or _ancestor_marker(project_local.parent, "SKILL.md") is not None
        or project_local == local_skills_root
        or _contains(local_skills_root, project_local)
    )
    return {
        "guide_version": 2,
        "platform": platform_family(),
        "question": (
            "Where should the generated knowledge base be stored? Confirm one path before rebuild writes anything, "
            "then confirm that its sync, sharing, backup, access, and encryption exposure is acceptable for private transcript-derived data."
        ),
        "selection_rule": "Use only the path explicitly confirmed by the user; a suggested path is never assumed to be private or approved.",
        "privacy_confirmation": {
            "required": True,
            "prompt": "Before writing, ask the user to confirm each applicable storage risk for the selected path.",
            "checks": [
                {
                    "id": "cloud-sync",
                    "question": "Is this directory synchronized to a cloud account, and is that acceptable for the knowledge base?",
                },
                {
                    "id": "sharing",
                    "question": "Can another user, workspace member, repository, or shared folder read this directory?",
                },
                {
                    "id": "backup-retention",
                    "question": "Will backups retain deleted or superseded private material, and is that retention acceptable?",
                },
                {
                    "id": "encryption-and-access",
                    "question": "Are device or disk encryption and local file permissions appropriate for the sensitivity of the source sessions?",
                },
            ],
            "on_uncertainty": "Do not write yet; choose a dedicated local or encrypted private directory and confirm it explicitly.",
        },
        "options": [
            {
                "id": "personal",
                "recommended": True,
                "path": str(personal),
                "tradeoff": (
                    "Easy to reuse across projects; keep it outside every Agent session root and public repository, and verify whether the "
                    "Documents directory is cloud-synced, shared, or retained by backups before confirming it."
                ),
            },
            {
                "id": "project-local",
                "recommended": False,
                "available": not project_unsafe,
                "path": str(project_local),
                "tradeoff": (
                    "Unavailable because this project is inside a repository or Skill tree; choose the personal or custom option outside it."
                    if project_unsafe
                    else "Convenient for one private, non-version-controlled project."
                ),
            },
            {
                "id": "custom",
                "recommended": False,
                "path": None,
                "tradeoff": (
                    "Use an explicit absolute path on a writable disk or encrypted/private workspace; confirm its account access, sync, "
                    "sharing, and backup behavior."
                ),
            },
        ],
        "follow_up": "After publication, ask whether to register this path for $agent-knowledge-reader. Registration is a separate local write.",
        "registry_path": str(default_registry_path()),
    }


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _ancestor_marker(path: Path, marker: str) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / marker).exists():
            return candidate
    return None


def validate_output_location(output: Path, sources: Iterable[SourceFile]) -> Path:
    resolved = output.expanduser().resolve()
    home = Path.home().resolve()
    if resolved == home or resolved == Path(resolved.anchor):
        raise ValueError("output must be a dedicated directory, not the home or filesystem root")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("output exists and is not a directory")
    if (resolved / "SKILL.md").is_file() or (resolved / ".git").exists():
        raise ValueError("output cannot be a Skill or repository root; choose a dedicated private data directory")
    repository = _ancestor_marker(resolved.parent, ".git")
    if repository is not None:
        raise ValueError("output cannot be inside a version-controlled repository; choose a private data directory outside it")
    skill = _ancestor_marker(resolved.parent, "SKILL.md")
    if skill is not None:
        raise ValueError("output cannot be inside a Skill directory")
    local_skills_root = Path(__file__).resolve().parents[3]
    if resolved == local_skills_root or _contains(local_skills_root, resolved):
        raise ValueError("output cannot be inside the installed or source Skill-pack tree")
    for source in sources:
        source_path = source.path.expanduser().resolve()
        source_root = source.root.expanduser().resolve()
        if resolved == source_root or _contains(source_root, resolved):
            raise ValueError(f"output cannot be inside a source session root: {source_root}")
        if resolved == source_path or _contains(resolved, source_path):
            raise ValueError("output cannot contain a source session file; choose a narrower dedicated directory")
    return resolved


def load_registry(path: Path | None = None) -> dict[str, Any]:
    target = (path or default_registry_path()).expanduser()
    if not target.exists():
        return {"registry_version": REGISTRY_VERSION, "default": None, "knowledge_bases": {}}
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("registry_version") != REGISTRY_VERSION or not isinstance(value.get("knowledge_bases"), dict):
        raise ValueError(f"invalid knowledge-base registry: {target}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def register_knowledge_base(name: str, kb: Path, registry_path: Path | None = None, make_default: bool = False) -> dict[str, Any]:
    safe_name = _safe_name(name)
    root = kb.expanduser().resolve()
    index_path = root / "knowledge" / "knowledge-index.json"
    if not index_path.is_file() and (root / "knowledge-index.json").is_file():
        index_path = root / "knowledge-index.json"
        root = root.parent if root.name == "knowledge" else root
    if not index_path.is_file():
        raise ValueError("knowledge-index.json not found; rebuild and publish the knowledge base first")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("semantic_status") != "published":
        raise ValueError("only a reviewed, published knowledge base can be registered")
    completion_path = root / "audit" / "completion-report.json"
    if not completion_path.is_file():
        raise ValueError("completion-report.json not found; run retrieval verification before registration")
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    gates = completion.get("gates") if isinstance(completion.get("gates"), dict) else {}
    if completion.get("run_id") != index.get("run_id"):
        raise ValueError("completion report and knowledge index belong to different evidence runs")
    if completion.get("status") not in {"complete", "complete_with_unsupported_formats"}:
        raise ValueError("knowledge base is not finally verified; run verify-retrieval before registration")
    if gates.get("published_knowledge") is not True or gates.get("retrieval_related_match") is not True or gates.get("retrieval_unrelated_no_match") is not True:
        raise ValueError("publication or retrieval verification gates are incomplete")
    expected_manifest = str(completion.get("publication_manifest_sha256") or "")
    if not expected_manifest or publication_manifest(root, index)["sha256"] != expected_manifest:
        raise ValueError("published knowledge files do not match the verified publication manifest")
    target = (registry_path or default_registry_path()).expanduser()
    with file_mutation_lock(target, "register-kb"):
        registry = load_registry(target)
        registry["knowledge_bases"][safe_name] = {
            "path": str(root),
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "run_id": index.get("run_id"),
        }
        if make_default or not registry.get("default"):
            registry["default"] = safe_name
        _atomic_json(target, registry)
    return {"status": "registered", "name": safe_name, "kb": str(root), "registry": str(target), "default": registry.get("default")}


def list_knowledge_bases(registry_path: Path | None = None) -> dict[str, Any]:
    target = (registry_path or default_registry_path()).expanduser()
    registry = load_registry(target)
    return {"registry": str(target), **registry}
