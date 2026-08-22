from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from . import SCHEMA_VERSION


PATH_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.])", re.IGNORECASE)
PATH_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
PATH_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})\b")


def redact_path_text(value: str) -> str:
    value = PATH_TOKEN_RE.sub("[REDACTED-CREDENTIAL]", value)
    value = PATH_EMAIL_RE.sub("[REDACTED-EMAIL]", value)
    value = PATH_PHONE_RE.sub("[REDACTED-PHONE]", value)
    return value


def stable_hash(*parts: object, length: int = 64) -> str:
    digest = sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\x00")
    return digest.hexdigest()[:length]


def display_path(path: Path) -> str:
    try:
        home = Path.home().resolve()
        resolved = path.expanduser().resolve()
        if resolved == home:
            return "~"
        if home in resolved.parents:
            return redact_path_text("~/" + resolved.relative_to(home).as_posix())
        return redact_path_text(resolved.as_posix())
    except (OSError, RuntimeError, ValueError):
        return redact_path_text(path.as_posix())


@dataclass
class SourceFile:
    path: Path
    root: Path
    adapter: str
    agent_name: str
    frozen_size: int
    mtime_ns: int
    inode: int
    source_file_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_file_id:
            self.source_file_id = stable_hash(self.path.expanduser().resolve(), length=20)

    @property
    def relative_path(self) -> str:
        try:
            return self.path.resolve().relative_to(self.root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return self.path.name

    def snapshot_dict(self) -> dict[str, Any]:
        return {
            "source_file_id": self.source_file_id,
            "path": display_path(self.path),
            "root": display_path(self.root),
            "relative_path": redact_path_text(self.relative_path),
            "adapter": self.adapter,
            "agent_name": self.agent_name,
            "frozen_size": self.frozen_size,
            "mtime_ns": self.mtime_ns,
            "inode": self.inode,
        }


@dataclass
class RawEvent:
    session_id: str
    sequence: int
    record_locator: str
    role: str
    event_type: str
    content: str = ""
    timestamp: str | int | float | None = None
    parent_session_id: str | None = None
    actor_hint: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    working_dir: str | None = None
    project_id: str | None = None
    session_title: str | None = None
    flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    events: list[RawEvent] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    last_complete_offset: int = 0
    records_seen: int = 0


@dataclass
class UnifiedEvent:
    event_id: str
    source_agent: str
    adapter: str
    source_file_id: str
    source_relpath: str
    session_id: str
    logical_session_id: str
    record_locator: str
    sequence: int
    role: str
    actor_kind: str
    event_type: str
    evidence_grade: str
    content: str
    content_sha256: str
    timestamp: str | int | float | None = None
    parent_session_id: str | None = None
    flags: list[str] = field(default_factory=list)
    call_id: str | None = None
    tool_name: str | None = None
    project_key: str | None = None
    project_label: str | None = None
    session_title: str | None = None
    working_dir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "UnifiedEvent":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: item for key, item in value.items() if key in allowed})


REQUIRED_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "source_agent",
    "adapter",
    "source_file_id",
    "source_relpath",
    "session_id",
    "logical_session_id",
    "record_locator",
    "sequence",
    "role",
    "actor_kind",
    "event_type",
    "evidence_grade",
    "content",
    "content_sha256",
    "flags",
}
