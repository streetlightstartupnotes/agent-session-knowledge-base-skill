from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


TEXT_SUFFIXES = {
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
TEXT_FILE_NAMES = {".gitattributes", ".gitignore", "COPYING", "LICENSE", "Makefile", "NOTICE"}
FORBIDDEN_DIRECTORY_NAMES = {"__pycache__", "audit", "knowledge", "review", "test-output", "test_outputs"}
IGNORED_DIRECTORY_NAMES = {".git", ".pytest_cache", ".mypy_cache"}
FORBIDDEN_FILE_NAMES = {".DS_Store", ".env", "state.json", "snapshot.json", "smoke-report.json"}
FORBIDDEN_SESSION_SUFFIXES = {".db", ".jsonl", ".ndjson", ".sqlite", ".sqlite3"}
CREDENTIAL_FIELD_RE_FRAGMENT = (
    r"(?:(?:[a-z0-9]+[_-])*(?:api[_-]?)?token|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|"
    r"auth(?:entication|orization)?[_-]?token|oauth[_-]?token|id[_-]?token|"
    r"(?:client|consumer|app|signing|webhook)[_-]?secret|"
    r"(?:aws[_-])?secret[_-]?access[_-]?key|secret[_-]?key|private[_-]?key|"
    r"password|passwd|secret|cookie|set[_-]?cookie)"
)
COOKIE_NAME_RE_FRAGMENT = r"(?:sessionid|csrftoken|csrf[_-]?token|jsessionid|phpsessid|connect\.sid)"
DATA_URL_BASE64_RE = re.compile(
    r"(?i)data:[-\w.+/]*(?:;[-\w.+]+(?:=[^;,\s]+)?)*;base64,[A-Za-z0-9+/_\-\r\n]{4,}={0,2}"
)

SENSITIVE_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:(?:ENCRYPTED|OPENSSH|RSA|EC|DSA) )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "huggingface_token": re.compile(r"\bhf_[A-Za-z0-9]{8,}\b"),
    "gitlab_token": re.compile(r"\bglpat-[A-Za-z0-9_-]{8,}\b"),
    "provider_key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "authorization": re.compile(r"(?i)\bAuthorization\s*:\s*[^\r\n]{6,}"),
    "http_cookie_header": re.compile(r"(?i)\b(?:Cookie|Set-Cookie)\s*:\s*[^\r\n]{3,}"),
    "cookie_assignment": re.compile(
        rf"(?<![A-Za-z0-9-])['\"]?(?:sessionid|csrftoken|(?i:csrf[_-]?token|jsessionid|phpsessid|connect\.sid))['\"]?\s*[:=]\s*(?:['\"][^'\"\r\n]{{8,}}['\"]|[^\s,'\";}}\]]{{8,}})"
    ),
    "cookie_jar": re.compile(
        rf"(?is)\{{(?=[^{{}}\r\n]{{0,1024}}['\"]name['\"]\s*:\s*['\"][^'\"\r\n]{{1,256}}['\"])(?=[^{{}}\r\n]{{0,1024}}['\"]value['\"]\s*:\s*(?:['\"][^'\"\r\n]{{4,}}['\"]|[^\s,}}]{{4,}}))(?:(?=[^{{}}\r\n]{{0,1024}}['\"]name['\"]\s*:\s*['\"](?:__Secure-|__Host-)?{COOKIE_NAME_RE_FRAGMENT}['\"])(?=[^{{}}\r\n]{{0,1024}})|(?=[^{{}}\r\n]{{0,1024}}['\"](?:domain|path|httpOnly|secure|sameSite|expires|expirationDate|hostOnly|storeId)['\"]\s*:))[^{{}}\r\n]{{1,1024}}\}}"
    ),
    "uri_userinfo": re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,20}://[^/\s@]{0,128}:[^/\s@]{4,}@"),
    "assigned_credential": re.compile(
        rf"(?i)(?<![A-Za-z0-9-])['\"]?{CREDENTIAL_FIELD_RE_FRAGMENT}['\"]?\s*[:=]\s*(?:['\"][^'\"\r\n]{{4,}}['\"]|(?=[A-Za-z0-9._~+/=-]*(?:[0-9+=/-]))[A-Za-z0-9._~+/=-]{{4,}})"
    ),
    "env_credential": re.compile(
        r"(?<![A-Za-z0-9])(?:[A-Z0-9]+_)*(?:TOKEN|API_TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD|PASSWD)\s*=\s*[A-Za-z0-9._~+/-]{4,}"
    ),
    "email": re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.])", re.IGNORECASE),
    "phone_cn": re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    "phone_international": re.compile(r"(?<![\w\d])\+[1-9]\d(?:[\s().-]*\d){7,13}(?!\d)"),
    "phone_separated": re.compile(r"(?<!\d)(?<!\d-)(?:\(\d{2,4}\)|\d{3,4})[ .-]\d{3,4}[ .-]\d{4}(?!\d)(?!-\d)"),
    "unix_home_path": re.compile(r"/(?:Users|home)/[^/\s\"'<>]+"),
    "windows_home_path": re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s\"'<>]+"),
}
BASE64_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9+/_-])([A-Za-z0-9+/_-]{4096,}={0,2})(?![A-Za-z0-9+/_-])")
BASE64_LINE_RE = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")
IPV4_CANDIDATE_RE = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{1,3}){3}(?!\d)")
IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])\[?(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.]*(?:%[A-Za-z0-9_.-]+)?\]?(?![0-9A-Fa-f:])"
)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _decode_approved_text(data: bytes) -> str | None:
    """Return strict UTF-8 text, or None when bytes are unsafe to publish as text."""

    if b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(character) < 32 and character not in "\t\n\r\f" for character in text):
        return None
    return text


def _valid_base64(compact: str) -> bool:
    if len(compact) < 4 or len(compact) % 4 == 1 or BASE64_LINE_RE.fullmatch(compact) is None:
        return False
    if ({"+", "/"} & set(compact)) and ({"-", "_"} & set(compact)):
        return False
    padded = compact + "=" * (-len(compact) % 4)
    try:
        base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return False
    return True


def _contains_wrapped_base64(text: str) -> bool:
    for match in BASE64_CANDIDATE_RE.finditer(text):
        if _valid_base64(match.group(1)):
            return True
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        candidate = lines[index].strip()
        if len(candidate) < 40 or BASE64_LINE_RE.fullmatch(candidate) is None:
            index += 1
            continue
        parts: list[str] = []
        while index < len(lines):
            part = lines[index].strip()
            if BASE64_LINE_RE.fullmatch(part) is None:
                break
            if len(part) < 40:
                if not parts or len(part) < 2:
                    break
                parts.append(part)
                index += 1
                break
            parts.append(part)
            index += 1
        compact = "".join(parts)
        if len(compact) < 256 or sum(len(part) >= 40 for part in parts) < 2 or not _valid_base64(compact):
            continue
        return True
    return False


def _contains_non_global_ip(text: str) -> bool:
    for pattern in (IPV4_CANDIDATE_RE, IPV6_CANDIDATE_RE):
        for match in pattern.finditer(text):
            candidate = match.group(0).strip("[]")
            if "%" in candidate:
                candidate = candidate.split("%", 1)[0]
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if not address.is_global:
                return True
    return False


def release_check(root: Path, deny_terms: Iterable[str] = ()) -> dict[str, Any]:
    target = root.expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f"release root is not a directory: {target}")
    normalized_terms = [term for term in (str(value).strip() for value in deny_terms) if term]
    findings: list[dict[str, Any]] = []
    scanned = 0
    for path in sorted(target.rglob("*")):
        relative = _relative(path, target)
        path_term_indexes = [index for index, term in enumerate(normalized_terms, 1) if term.casefold() in relative.casefold()]
        reported_relative = (
            f"[REDACTED-DENY-PATH:{sha256(relative.encode('utf-8')).hexdigest()[:12]}]" if path_term_indexes else relative
        )
        if any(part in IGNORED_DIRECTORY_NAMES for part in path.relative_to(target).parts):
            continue
        if path.is_symlink():
            findings.append({"kind": "symlink-not-allowed", "path": reported_relative})
            continue
        if path.is_dir():
            if path.name in FORBIDDEN_DIRECTORY_NAMES or path.name.startswith("test-output"):
                findings.append({"kind": "generated-directory", "path": reported_relative})
            continue
        if not path.is_file():
            continue
        for index in path_term_indexes:
            findings.append({"kind": f"deny-term-{index}", "path": reported_relative, "location": "path"})
        if path.name in FORBIDDEN_FILE_NAMES or path.name.startswith(".env."):
            findings.append({"kind": "private-or-generated-file", "path": reported_relative})
        if path.suffix.lower() in FORBIDDEN_SESSION_SUFFIXES:
            findings.append({"kind": "session-or-state-artifact", "path": reported_relative})
        try:
            size = path.stat().st_size
        except OSError:
            findings.append({"kind": "unreadable-file", "path": reported_relative})
            continue
        if size > 5 * 1024 * 1024:
            findings.append({"kind": "oversized-file", "path": reported_relative})
            continue
        approved_type = path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_FILE_NAMES
        try:
            data = path.read_bytes()
        except OSError:
            findings.append({"kind": "unreadable-file", "path": reported_relative})
            continue
        text = _decode_approved_text(data) if approved_type else None
        if text is None:
            findings.append({"kind": "binary-or-unapproved-file-type", "path": reported_relative})
            continue
        scanned += 1
        # Long hexadecimal ids can contain phone-like digit runs. They carry no
        # contact information, so neutralize them before contact scanning.
        scan_text = re.sub(r"(?i)\b[0-9a-f]{24,}\b", "[HASH]", text)
        if DATA_URL_BASE64_RE.search(scan_text) or _contains_wrapped_base64(scan_text):
            findings.append({"kind": "raw_base64", "path": reported_relative})
        if _contains_non_global_ip(scan_text):
            findings.append({"kind": "private_ip", "path": reported_relative})
        for kind, pattern in SENSITIVE_PATTERNS.items():
            match = pattern.search(scan_text)
            if match:
                findings.append({"kind": kind, "path": reported_relative, "line": _line_number(scan_text, match.start())})
        # Deny terms are explicit private identifiers. Check the unmodified
        # decoded text so a hex-shaped codename cannot be neutralized away by
        # contact false-positive handling.
        folded = text.casefold()
        for index, term in enumerate(normalized_terms, 1):
            offset = folded.find(term.casefold())
            if offset >= 0:
                # Never echo a private deny term in the report.
                findings.append({"kind": f"deny-term-{index}", "path": reported_relative, "line": _line_number(text, offset)})
    return {
        "release_check_version": 1,
        "status": "passed" if not findings else "failed",
        "files_scanned": scanned,
        "findings": findings,
        "privacy_note": "Findings identify only type and location; matched secret or private text is never printed.",
    }
