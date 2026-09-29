"""Opt-in, resumable append-only maintenance of exact human-managed Markdown files."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .locking import mutation_lock
from .sanitize import Sanitizer


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _root(path: Path) -> Path:
    supplied = path.expanduser()
    root = supplied.resolve()
    if supplied.is_symlink() or not root.is_dir() or root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("choose an existing bounded private directory, not a symlink or broad root")
    ancestors = (root, *root.parents)
    for ancestor in ancestors:
        markers = ["knowledge-index.json", ".git", "SKILL.md"]
        # Nested publication markers matter at a proposed root or when inside
        # its knowledge tree; do not probe unrelated system audit directories.
        if ancestor == root or ancestor / "knowledge" in ancestors:
            markers.extend(("knowledge/knowledge-index.json", "audit/completion-report.json"))
        if any((ancestor / marker).exists() for marker in markers):
            raise ValueError("manual maintenance cannot target a generated library or Skill/source repository")
    if (root / ".maintenance").is_symlink():
        raise ValueError("maintenance directory must not be a symlink")
    return root


def _target(root: Path, relative: str) -> Path:
    path = Path(relative)
    if (not path.parts or path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md"
            or any(part.startswith(".") for part in path.parts)):
        raise ValueError("target must be an existing, relative human-maintained Markdown page")
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlink targets are not allowed")
        if cursor.is_dir() and any((cursor / marker).exists() for marker in ("knowledge-index.json", "audit/completion-report.json", "SKILL.md", ".git")):
            raise ValueError("target descends into a generated library or source repository")
    if not cursor.is_file() or cursor.stat().st_size > 2_000_000:
        raise ValueError("manual page is missing or exceeds the bounded file size")
    return cursor


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("maintenance metadata must not be a symlink")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid maintenance metadata")
    return value


def _write(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise ValueError("refusing symlink write")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".maintenance-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _save(path: Path, value: dict[str, Any]) -> None:
    _write(path, _canonical(value) + b"\n")


def configure(root: Path, targets: list[str], authorization_ref: str) -> dict[str, Any]:
    root = _root(root)
    if not targets or not authorization_ref.strip():
        raise ValueError("exact targets and the user's authorization source are required")
    approved = sorted({_target(root, target).relative_to(root).as_posix() for target in targets})
    safe_ref = Sanitizer().sanitize_text(authorization_ref).strip()
    with mutation_lock(root, "manual-maintenance-configure"):
        value = {"version": 1, "enabled": True, "targets": approved, "authorization_ref": safe_ref}
        _save(root / ".maintenance" / "authorization.json", value)
    return {"status": "configured", "target_count": len(approved),
            "boundary": "This records host-verified consent; the command itself cannot grant consent or start a scheduler."}


def revoke(root: Path) -> dict[str, Any]:
    root = _root(root)
    with mutation_lock(root, "manual-maintenance-revoke"):
        path = root / ".maintenance" / "authorization.json"
        value = _read(path)
        value["enabled"] = False
        _save(path, value)
    return {"status": "revoked", "pending_items_retained": True}


def _permission(root: Path, target: str) -> dict[str, Any]:
    value = _read(root / ".maintenance" / "authorization.json")
    if value.get("version") != 1 or value.get("enabled") is not True or target not in value.get("targets", []):
        raise ValueError("maintenance permission is absent, revoked, or outside the approved target scope")
    return value


def _queue(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / ".maintenance" / "queue.json"
    value = _read(path) if path.exists() else {"version": 1, "items": {}}
    if value.get("version") != 1 or not isinstance(value.get("items"), dict):
        raise ValueError("invalid maintenance queue")
    return path, value


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in ("id", "target", "status", "created_at", "verified_at", "replaces", "replaced_by") if key in item}


def stage(root: Path, target: str, delta: str, source_ref: str, replaces: str | None = None) -> dict[str, Any]:
    root = _root(root)
    sanitizer = Sanitizer()
    text, source = sanitizer.sanitize_text(delta).strip(), sanitizer.sanitize_text(source_ref).strip()
    if not text or not source or len(text) > 100_000 or "<!-- maintenance:" in text:
        raise ValueError("a bounded sourced delta without reserved maintenance markers is required")
    with mutation_lock(root, "manual-maintenance-stage"):
        page = _target(root, target)
        target = page.relative_to(root).as_posix()
        authority = _permission(root, target)
        path, queue = _queue(root)
        payload = {"target": target, "text": text, "source_ref": source}
        before = page.read_bytes()
        before.decode("utf-8")
        identity = dict(payload)
        previous = queue["items"].get(replaces) if replaces else None
        if replaces:
            if not previous or previous["target"] != target or previous["status"] not in {"pending", "cancelled", "superseded"}:
                raise ValueError("restage requires a pending or cancelled item for the same target, never applying/applied work")
            if f"<!-- maintenance:{replaces} -->".encode() in before:
                raise ValueError("old delta is already present; inspect/recover it instead of restaging")
            identity.update(replaces=replaces, authorization_sha256=_digest(_canonical(authority)), before_sha256=_digest(before))
        item_id = "delta-" + _digest(_canonical(identity))[:32]
        if item_id in queue["items"]:
            return {**_summary(queue["items"][item_id]), "duplicate": True}
        if previous and previous["status"] == "superseded":
            raise ValueError("item already superseded; inspect its replacement before restaging")
        timestamp = datetime.now(timezone.utc).isoformat()
        block = f"\n\n<!-- maintenance:{item_id} -->\n{text}\n\nSource: {source}\nRecorded: {timestamp}\n<!-- maintenance:{item_id}:end -->\n"
        item = {"id": item_id, **payload, "block": block, "created_at": timestamp,
                "status": "pending", "authorization_sha256": _digest(_canonical(authority)),
                "before_sha256": _digest(before), "after_sha256": _digest(before + block.encode()),
                "block_sha256": _digest(block.encode())}
        queue["items"][item_id] = item
        if previous:
            item["replaces"] = replaces
            queue["items"][replaces] = {**_summary(previous), "status": "superseded", "replaced_by": item_id}
        _save(path, queue)
    return {**_summary(item), "redactions": dict(sanitizer.stats), "duplicate": False}


def pending(root: Path) -> dict[str, Any]:
    root = _root(root)
    _, queue = _queue(root)
    return {"items": [_summary(item) for item in queue["items"].values()], "bodies_returned": False}


def cancel(root: Path, item_id: str) -> dict[str, Any]:
    root = _root(root)
    with mutation_lock(root, "manual-maintenance-cancel"):
        path, queue = _queue(root)
        item = queue["items"].get(item_id)
        if not item or item["status"] in {"applied", "applying", "superseded"}:
            raise ValueError("cannot cancel unknown, applying or applied work; inspect the target and use its explicit edit contract")
        queue["items"][item_id] = {**_summary(item), "status": "cancelled"}
        _save(path, queue)
    return _summary(queue["items"][item_id])


def apply(root: Path, item_id: str, commit: bool = False) -> dict[str, Any]:
    root = _root(root)
    with mutation_lock(root, "manual-maintenance-apply"):
        path, queue = _queue(root)
        item = queue["items"].get(item_id)
        if not item or item["status"] in {"cancelled", "superseded"}:
            raise ValueError("unknown, cancelled or superseded maintenance item")
        page = _target(root, item["target"])
        authority = _permission(root, item["target"])
        if item["authorization_sha256"] != _digest(_canonical(authority)):
            raise ValueError("authorization changed; review and stage a newly authorized delta")
        current = page.read_bytes()
        if item["status"] == "applied":
            marker = f"<!-- maintenance:{item_id} -->".encode()
            end_marker = f"<!-- maintenance:{item_id}:end -->\n".encode()
            start = current.find(marker)
            end = current.find(end_marker, start)
            if start < 2 or end < 0 or _digest(current[start - 2:end + len(end_marker)]) != item["block_sha256"]:
                raise ValueError("previously applied delta changed; it will not be silently restored")
            return {**_summary(item), "already_applied": True}
        block = item["block"].encode()
        if _digest(block) != item["block_sha256"]:
            raise ValueError("pending delta content changed")
        current_hash = _digest(current)
        recovered = current_hash == item["after_sha256"]
        if not recovered and (current_hash != item["before_sha256"] or _digest(current + block) != item["after_sha256"]):
            raise ValueError("target changed after staging; reconcile without overwriting it")
        if not commit:
            return {**_summary(item), "dry_run": True, "recovery_available": recovered}
        item["status"] = "applying"
        _save(path, queue)
        if not recovered:
            # Writers outside this cooperative lock must be paused during commit.
            if page.read_bytes() != current:
                raise ValueError("target changed during maintenance")
            _write(page, current + block)
        if _digest(page.read_bytes()) != item["after_sha256"]:
            raise ValueError("target verification failed; delta remains pending recovery")
        item["status"], item["verified_at"] = "applied", datetime.now(timezone.utc).isoformat()
        for key in ("text", "source_ref", "block"):
            item.pop(key, None)
        _save(path, queue)
    return {**_summary(item), "recovered": recovered}
