from __future__ import annotations

import base64
import binascii
import json
import ipaddress
import os
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any


SANITIZER_POLICY_VERSION = 4


DATA_URL_RE = re.compile(
    r"data:(?P<mime>[-\w.+/]*)(?:;[-\w.+]+(?:=[^;,\s]+)?)*;base64,(?P<data>[A-Za-z0-9+/_\-\r\n]{4,}={0,2})",
    re.IGNORECASE,
)
BASE64_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/_-])([A-Za-z0-9+/_-]{4096,}={0,2})(?![A-Za-z0-9+/_-])")
BASE64_LINE_RE = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")
CREDENTIAL_FIELD_RE_FRAGMENT = (
    r"(?:(?:[a-z0-9]+[_-])*(?:api[_-]?)?token|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|"
    r"auth(?:entication|orization)?[_-]?token|oauth[_-]?token|id[_-]?token|"
    r"(?:client|consumer|app|signing|webhook)[_-]?secret|"
    r"(?:aws[_-])?secret[_-]?access[_-]?key|secret[_-]?key|private[_-]?key|"
    r"password|passwd|secret|cookie|set[_-]?cookie)"
)
COOKIE_NAME_RE_FRAGMENT = r"(?:sessionid|csrftoken|csrf[_-]?token|jsessionid|phpsessid|connect\.sid)"
COOKIE_JAR_RE = re.compile(
    rf"(?is)\{{(?=[^{{}}\r\n]{{0,1024}}['\"]name['\"]\s*:\s*['\"][^'\"\r\n]{{1,256}}['\"])(?=[^{{}}\r\n]{{0,1024}}['\"]value['\"]\s*:\s*(?:['\"][^'\"\r\n]{{4,}}['\"]|[^\s,}}]{{4,}}))(?:(?=[^{{}}\r\n]{{0,1024}}['\"]name['\"]\s*:\s*['\"](?:__Secure-|__Host-)?{COOKIE_NAME_RE_FRAGMENT}['\"])(?=[^{{}}\r\n]{{0,1024}})|(?=[^{{}}\r\n]{{0,1024}}['\"](?:domain|path|httpOnly|secure|sameSite|expires|expirationDate|hostOnly|storeId)['\"]\s*:))[^{{}}\r\n]{{1,1024}}\}}"
)
COOKIE_ASSIGNMENT_RE = re.compile(
    rf"(?<![A-Za-z0-9-])['\"]?(?:sessionid|csrftoken|(?i:csrf[_-]?token|jsessionid|phpsessid|connect\.sid))['\"]?\s*[:=]\s*(?:['\"][^'\"\r\n]{{8,}}['\"]|[^\s,'\";}}\]]{{8,}})"
)

CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("BEARER", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("GOOGLE_KEY", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:(?:ENCRYPTED|RSA|EC|DSA|OPENSSH) )?PRIVATE KEY-----.*?-----END (?:(?:ENCRYPTED|RSA|EC|DSA|OPENSSH) )?PRIVATE KEY-----", re.DOTALL)),
    ("COOKIE", re.compile(r"(?i)\b(?:Cookie|Set-Cookie)\s*:\s*[^\r\n]{3,}")),
    ("COOKIE_JAR", COOKIE_JAR_RE),
    ("COOKIE_VALUE", COOKIE_ASSIGNMENT_RE),
    ("AUTHORIZATION", re.compile(r"(?i)\bAuthorization\s*:\s*[^\r\n]{6,}")),
    ("URI_USERINFO", re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,20}://[^/\s@]{0,128}:[^/\s@]{4,}@")),
    (
        "ASSIGNED_SECRET",
        re.compile(
            rf"(?i)(?<![A-Za-z0-9-])['\"]?{CREDENTIAL_FIELD_RE_FRAGMENT}['\"]?\s*[:=]\s*(?:['\"][^'\"\r\n]{{4,}}['\"]|[A-Za-z0-9._~+/=-]{{4,}})"
        ),
    ),
]
EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.])", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
INTERNATIONAL_PHONE_RE = re.compile(r"(?<![\w\d])\+[1-9]\d(?:[\s().-]*\d){7,13}(?!\d)")
SEPARATED_PHONE_RE = re.compile(r"(?<!\d)(?<!\d-)(?:\(\d{2,4}\)|\d{3,4})[ .-]\d{3,4}[ .-]\d{4}(?!\d)(?!-\d)")
IPV4_CANDIDATE_RE = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{1,3}){3}(?!\d)")
IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Fa-f:])\[?(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.]*(?:%[A-Za-z0-9_.-]+)?\]?(?![0-9A-Fa-f:])"
)
UNIX_HOME_PATH_RE = re.compile(r"(?P<prefix>/(?:Users|home)/)(?P<name>[^/\\\s\"'<>]+)")
WINDOWS_HOME_PATH_RE = re.compile(r"(?i)(?P<prefix>\b[A-Z]:[\\/]+Users[\\/]+)(?P<name>[^/\\\s\"'<>]+)")

RUNTIME_MARKERS = (
    "<environment_context>",
    "<permissions instructions>",
    "<recommended_plugins>",
    "# agents.md instructions",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<collaboration_mode>",
    "<skills_instructions>",
    "<codex_internal_context",
    "<turn_aborted>",
)
DELEGATION_MARKERS = ("<codex_delegation>", "<delegated_task>", "source_thread_id", "sub_agent_activity")
APPROVAL_MARKERS = (
    "<approval",
    "sandbox approval",
    "approval transcript",
    "approvals_reviewer",
    ">>> approval request start",
    "reviewed codex session id:",
)
IMPORTED_TRANSCRIPT_MARKERS = (
    "the following is the codex agent history",
    "the following is the codex agent history added since",
    "<external session imported>",
    ">>> transcript start",
    ">>> transcript delta start",
    "begin external transcript",
)
COMPACTION_MARKERS = (
    "[compressed conversation summary",
    "<summary>",
    "## previous session archives",
    "conversation summary",
)
EXTERNAL_MARKERS = ("quoted third-party", "third-party material", "external article", "reference article")
SYNTHETIC_MARKERS = (
    "short message ",
    "conversation message ",
    "this is a detailed message number ",
)
TEST_ROLE_PATTERNS = (
    re.compile(r"(?i)\bonly (?:reply|respond) (?:with )?(?:ok|yes|no)\b"),
    re.compile(r"只回复\s*[`'\"]?(?:ok|好的|是|否)[`'\"]?", re.IGNORECASE),
    re.compile(r"(?i)^\s*(?:you are|act as)\s+.{0,80}$"),
)


def _valid_base64(compact: str) -> bool:
    """Validate padded or unpadded standard/Base64URL without retaining bytes."""

    if len(compact) < 4 or len(compact) % 4 == 1:
        return False
    if BASE64_LINE_RE.fullmatch(compact) is None:
        return False
    # Mixed standard-only and URL-safe-only alphabets are not a canonical
    # encoding and are more likely to be an unrelated identifier.
    if ({"+", "/"} & set(compact)) and ({"-", "_"} & set(compact)):
        return False
    padded = compact + "=" * (-len(compact) % 4)
    try:
        base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return False
    return True


class Sanitizer:
    def __init__(self) -> None:
        self.stats: Counter[str] = Counter()

    def _binary_placeholder(self, raw: str, kind: str) -> str:
        compact = re.sub(r"\s+", "", raw)
        digest = sha256(compact.encode("ascii", errors="ignore")).hexdigest()
        estimated_bytes = (len(compact.rstrip("=")) * 3) // 4
        self.stats["binary_payloads_removed"] += 1
        self.stats["binary_characters_removed"] += len(raw)
        return f"[BINARY_REMOVED kind={kind} bytes~={estimated_bytes} sha256={digest}]"

    def _replace_data_url(self, match: re.Match[str]) -> str:
        return self._binary_placeholder(match.group("data"), match.group("mime") or "data-url")

    def _replace_base64(self, match: re.Match[str]) -> str:
        encoded_value = match.group(1)
        compact = re.sub(r"\s+", "", encoded_value)
        if not _valid_base64(compact):
            return encoded_value
        return self._binary_placeholder(encoded_value, "base64")

    def _replace_wrapped_base64(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        output: list[str] = []
        index = 0
        while index < len(lines):
            candidate = lines[index].strip()
            if len(candidate) < 40 or BASE64_LINE_RE.fullmatch(candidate) is None:
                output.append(lines[index])
                index += 1
                continue
            end = index
            compact_parts: list[str] = []
            while end < len(lines):
                part = lines[end].strip()
                if BASE64_LINE_RE.fullmatch(part) is None:
                    break
                # MIME wrapping normally uses long fixed-width lines followed
                # by one short terminal line. Admit that tail only after the
                # block has started, then stop so prose cannot be swept in as
                # an arbitrary sequence of short Base64-looking lines.
                if len(part) < 40:
                    if not compact_parts or len(part) < 2:
                        break
                    compact_parts.append(part)
                    end += 1
                    break
                compact_parts.append(part)
                end += 1
            compact = "".join(compact_parts)
            if len(compact) >= 256 and sum(len(part) >= 40 for part in compact_parts) >= 2 and _valid_base64(compact):
                if lines[end - 1].endswith("\r\n"):
                    trailing_newline = "\r\n"
                elif lines[end - 1].endswith("\n"):
                    trailing_newline = "\n"
                elif lines[end - 1].endswith("\r"):
                    trailing_newline = "\r"
                else:
                    trailing_newline = ""
                output.append(self._binary_placeholder(compact, "base64") + trailing_newline)
                index = end
                continue
            output.extend(lines[index:end])
            index = end
        return "".join(output)

    @staticmethod
    def _is_generic_home_name(value: str) -> bool:
        """Keep documentation placeholders while treating real account names as private."""

        candidate = value.strip()
        return bool(
            re.fullmatch(r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%[A-Za-z_][A-Za-z0-9_]*%|\{+[^{}]+\}+|\[+[^\[\]]+\]+)", candidate)
        )

    def _replace_foreign_home(self, match: re.Match[str]) -> str:
        if self._is_generic_home_name(match.group("name")):
            return match.group(0)
        self.stats["home_path_redactions"] += 1
        return "~"

    def _replace_non_global_ip(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        candidate = raw.strip("[]")
        if "%" in candidate:
            candidate = candidate.split("%", 1)[0]
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            return raw
        if address.is_global:
            return raw
        self.stats["private_ip_redactions"] += 1
        return "[REDACTED:PRIVATE_IP]"

    def _replace_known_home(self, text: str, home_value: str) -> str:
        normalized = home_value.rstrip("/\\")
        if not normalized or normalized in {"/", "\\"}:
            return text
        flags = re.IGNORECASE if re.match(r"^[A-Za-z]:[\\/]", normalized) else 0
        pattern = re.compile(re.escape(normalized) + r"(?=$|[/\\])", flags)
        text, count = pattern.subn("~", text)
        self.stats["home_path_redactions"] += count
        return text

    def sanitize_text(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                value = str(value)
        text = value.replace("\x00", "[NUL]")
        home_values = {str(Path.home())}
        if os.environ.get("USERPROFILE"):
            home_values.add(os.environ["USERPROFILE"])
        for home_value in sorted(home_values, key=len, reverse=True):
            text = self._replace_known_home(text, home_value)
        text = WINDOWS_HOME_PATH_RE.sub(self._replace_foreign_home, text)
        text = UNIX_HOME_PATH_RE.sub(self._replace_foreign_home, text)
        text = DATA_URL_RE.sub(self._replace_data_url, text)
        text = self._replace_wrapped_base64(text)
        text = BASE64_TOKEN_RE.sub(self._replace_base64, text)
        for label, pattern in CREDENTIAL_PATTERNS:
            text, count = pattern.subn(f"[REDACTED:{label}]", text)
            self.stats["credential_redactions"] += count
        text, count = EMAIL_RE.subn("[REDACTED:EMAIL]", text)
        self.stats["email_redactions"] += count
        text, count = PHONE_RE.subn("[REDACTED:PHONE]", text)
        self.stats["phone_redactions"] += count
        text, count = INTERNATIONAL_PHONE_RE.subn("[REDACTED:PHONE]", text)
        self.stats["phone_redactions"] += count
        text, count = SEPARATED_PHONE_RE.subn("[REDACTED:PHONE]", text)
        self.stats["phone_redactions"] += count
        text = IPV4_CANDIDATE_RE.sub(self._replace_non_global_ip, text)
        text = IPV6_CANDIDATE_RE.sub(self._replace_non_global_ip, text)
        return text

    def sanitize_mapping(self, value: Any) -> Any:
        if isinstance(value, dict):
            cookie_name = str(value.get("name") or "")
            lowered_keys = {str(key).lower() for key in value}
            cookie_metadata = {
                "domain",
                "path",
                "httponly",
                "secure",
                "samesite",
                "expires",
                "expirationdate",
                "hostonly",
                "storeid",
            }
            cookie_jar = bool(
                cookie_name
                and "value" in value
                and (
                    re.fullmatch(COOKIE_NAME_RE_FRAGMENT, cookie_name, re.IGNORECASE) is not None
                    or cookie_name.lower().startswith(("__secure-", "__host-"))
                    or bool(lowered_keys & cookie_metadata)
                )
            )
            result: dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered in {"encrypted_content", "data", "blob", "bytes", "raw_image", "audio"}:
                    raw = str(item)
                    result[str(key)] = self._binary_placeholder(raw, lowered) if len(raw) > 128 else "[BINARY_FIELD_REMOVED]"
                elif any(marker in lowered for marker in ("token", "password", "cookie", "secret", "credential")):
                    result[str(key)] = "[REDACTED:CREDENTIAL_FIELD]"
                    self.stats["credential_fields_redacted"] += 1
                elif cookie_jar and lowered == "value":
                    result[str(key)] = "[REDACTED:COOKIE_VALUE]"
                    self.stats["credential_fields_redacted"] += 1
                else:
                    result[str(key)] = self.sanitize_mapping(item)
            return result
        if isinstance(value, list):
            return [self.sanitize_mapping(item) for item in value]
        if isinstance(value, str):
            return self.sanitize_text(value)
        return value


def classify_flags(text: str, existing: list[str] | None = None) -> list[str]:
    flags = set(existing or [])
    lowered = text.lower().strip()
    if any(marker in lowered for marker in RUNTIME_MARKERS):
        flags.add("runtime_injection")
    if any(marker in lowered for marker in DELEGATION_MARKERS):
        flags.add("delegated_transcript")
    if any(marker in lowered for marker in APPROVAL_MARKERS):
        flags.add("nested_approval")
    if any(marker in lowered for marker in IMPORTED_TRANSCRIPT_MARKERS):
        flags.add("imported_transcript")
    if any(marker in lowered[:2000] for marker in COMPACTION_MARKERS):
        flags.add("compacted_summary")
    if any(marker in lowered for marker in EXTERNAL_MARKERS):
        flags.add("external_material")
    if any(lowered.startswith(marker) for marker in SYNTHETIC_MARKERS):
        flags.add("synthetic_fixture")
    if any(pattern.search(text[:2000]) for pattern in TEST_ROLE_PATTERNS):
        flags.add("test_role")
    if re.search(r"(?i)(?:^|[/\\])(?:memories?|memory|old[-_ ]?knowledge[-_ ]?base)(?:[/\\]|\b)", text[:8000]):
        flags.add("old_memory_reference")
    return sorted(flags)


def actor_kind(role: str, flags: list[str], hint: str | None = None) -> str:
    flag_set = set(flags)
    if "delegated_transcript" in flag_set or "nested_approval" in flag_set or "imported_transcript" in flag_set:
        return "orchestrator"
    if "external_material" in flag_set:
        return "third_party"
    if "test_role" in flag_set or "synthetic_fixture" in flag_set:
        return "test_actor"
    if "runtime_injection" in flag_set or role == "system":
        return "runtime"
    if hint:
        return hint
    if "subagent" in flag_set or "sidechain" in flag_set:
        return "subagent"
    if role == "user":
        return "unknown_user"
    if role == "assistant":
        return "primary_agent"
    if role == "tool":
        return "tool"
    return "unknown_user" if role == "unknown" else "observer"


def evidence_grade(role: str, event_type: str, actor: str) -> str:
    if role == "user" and actor == "primary_user":
        return "A"
    if event_type in {"tool_call", "tool_result", "patch", "browser", "device", "attachment", "status", "delivery", "error"}:
        return "B"
    if role == "assistant" and actor in {"primary_agent", "subagent"}:
        return "C"
    return "D"
