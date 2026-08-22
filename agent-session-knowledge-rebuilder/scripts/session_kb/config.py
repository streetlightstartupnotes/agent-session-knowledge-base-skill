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
        "guide_version": 1,
        "platform": platform_family(),
        "question": "Where should the generated knowledge base be stored? Confirm one path before rebuild writes anything.",
        "options": [
            {
                "id": "personal",
                "recommended": True,
                "path": str(personal),
                "tradeoff": "Easy to reuse across projects; keep it outside every Agent session root and outside a public repository.",
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
                "tradeoff": "Use an explicit absolute path on a writable disk or encrypted/private workspace.",
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
    target = (registry_path or default_registry_path()).expanduser()
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
