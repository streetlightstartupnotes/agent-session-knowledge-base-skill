from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def knowledge_base_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    return root.parent if root.name == "knowledge" and (root.parent / "audit").exists() else root


def lock_path(path: Path) -> Path:
    root = knowledge_base_root(path)
    return root.parent / f".{root.name}.agent-session-kb.lock"


@contextmanager
def file_mutation_lock(path: Path, operation: str) -> Iterator[Path]:
    """Hold a sibling lock for one standalone mutable file."""

    target_file = path.expanduser().resolve()
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target = target_file.with_name(f".{target_file.name}.agent-session-kb.lock")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            f"another mutation may be active; inspect the lock and verify the owning process before removing it: {target}"
        ) from exc
    try:
        payload = {
            "lock_version": 1,
            "operation": str(operation),
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "target_kind": "standalone-file",
        }
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield target
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def mutation_lock(path: Path, operation: str) -> Iterator[Path]:
    """Hold a portable fail-closed single-writer lock for one knowledge base."""

    root = knowledge_base_root(path)
    root.parent.mkdir(parents=True, exist_ok=True)
    target = lock_path(root)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            f"another knowledge-base mutation may be active; inspect the lock and verify the owning process before removing it: {target}"
        ) from exc
    try:
        payload = {
            "lock_version": 1,
            "operation": str(operation),
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield target
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
