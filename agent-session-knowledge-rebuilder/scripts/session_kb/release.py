from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable


TEXT_SUFFIXES = {
    "",
    ".css",
    ".gitignore",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_DIRECTORY_NAMES = {"__pycache__", "audit", "knowledge", "review", "test-output", "test_outputs"}
IGNORED_DIRECTORY_NAMES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
FORBIDDEN_FILE_NAMES = {".DS_Store", ".env", "state.json", "snapshot.json", "smoke-report.json"}
FORBIDDEN_SESSION_SUFFIXES = {".db", ".jsonl", ".ndjson", ".sqlite", ".sqlite3"}

SENSITIVE_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "authorization": re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    "cookie": re.compile(r"(?i)\b(?:Cookie|Set-Cookie)\s*:\s*[^\r\n]{3,}"),
    "assigned_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\b\s*[:=]\s*['\"]?[^\s,'\";}\]]{4,}"
    ),
    "email": re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.])", re.IGNORECASE),
    "phone_cn": re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    "phone_international": re.compile(r"(?<![\w\d])\+[1-9]\d(?:[\s().-]*\d){7,13}(?!\d)"),
    "unix_home_path": re.compile(r"/(?:Users|home)/[^/\s\"'<>]+"),
    "windows_home_path": re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"'<>]+"),
    "private_ip": re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"),
    "raw_base64": re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{4096,}={0,2}(?![A-Za-z0-9+/])"),
}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def release_check(root: Path, deny_terms: Iterable[str] = ()) -> dict[str, Any]:
    target = root.expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f"release root is not a directory: {target}")
    normalized_terms = [term for term in (str(value).strip() for value in deny_terms) if term]
    findings: list[dict[str, Any]] = []
    scanned = 0
    for path in sorted(target.rglob("*")):
        relative = _relative(path, target)
        if any(part in IGNORED_DIRECTORY_NAMES for part in path.relative_to(target).parts):
            continue
        if path.is_symlink():
            findings.append({"kind": "symlink-not-allowed", "path": relative})
            continue
        if path.is_dir():
            if path.name in FORBIDDEN_DIRECTORY_NAMES or path.name.startswith("test-output"):
                findings.append({"kind": "generated-directory", "path": relative})
            continue
        if not path.is_file():
            continue
        if path.name in FORBIDDEN_FILE_NAMES or path.name.startswith(".env."):
            findings.append({"kind": "private-or-generated-file", "path": relative})
        if path.suffix.lower() in FORBIDDEN_SESSION_SUFFIXES:
            findings.append({"kind": "session-or-state-artifact", "path": relative})
        if path.stat().st_size > 5 * 1024 * 1024:
            findings.append({"kind": "oversized-file", "path": relative})
            continue
        data = path.read_bytes()
        if b"\x00" in data or (path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".gitignore"):
            findings.append({"kind": "binary-or-unapproved-file-type", "path": relative})
            continue
        scanned += 1
        text = data.decode("utf-8", errors="replace")
        # Long hexadecimal ids can contain phone-like digit runs. They carry no
        # contact information, so neutralize them before contact scanning.
        scan_text = re.sub(r"(?i)\b[0-9a-f]{24,}\b", "[HASH]", text)
        for kind, pattern in SENSITIVE_PATTERNS.items():
            match = pattern.search(scan_text)
            if match:
                findings.append({"kind": kind, "path": relative, "line": _line_number(scan_text, match.start())})
        folded = scan_text.casefold()
        for index, term in enumerate(normalized_terms, 1):
            offset = folded.find(term.casefold())
            if offset >= 0:
                # Never echo a private deny term in the report.
                findings.append({"kind": f"deny-term-{index}", "path": relative, "line": _line_number(scan_text, offset)})
    return {
        "release_check_version": 1,
        "status": "passed" if not findings else "failed",
        "files_scanned": scanned,
        "findings": findings,
        "privacy_note": "Findings identify only type and location; matched secret or private text is never printed.",
    }
