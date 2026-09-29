from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from .model import UnifiedEvent


IDENTITY_RE = re.compile(
    r"(?:我是|我叫|我在|我负责|我的(?:工作|职业|目标|方向|身份)|目前|现在|正在|想做|希望成为|"
    r"\bI\s+(?:am|work|live|lead|build|want)|\bmy\s+(?:name|role|goal|work|direction))",
    re.IGNORECASE,
)
CORRECTION_RE = re.compile(r"我说的是|不是这个|不对|纠正|改成|回顾.*历史|you misunderstood|I meant", re.IGNORECASE)
BOUNDARY_RE = re.compile(r"不要|不能|只要|只需要|先别|必须|禁止|停在|确认后|do not|must|only", re.IGNORECASE)
VALIDATION_RE = re.compile(r"验证|测试|真实|完成|交付|检查|发布|安装|可用|verify|test|complete|deliver", re.IGNORECASE)
EXPRESSION_RE = re.compile(r"表达|写法|文风|AI味|枚举|原意|口语|节奏|喜欢|反对|voice|style|wording", re.IGNORECASE)


def _excerpt(text: str, limit: int = 6000) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    digest = sha256(cleaned.encode("utf-8")).hexdigest()
    return cleaned[:limit] + f"\n\n[TRUNCATED rendered_chars={limit} full_sha256={digest}]"


def _quote_card(event: UnifiedEvent, limit: int = 6000) -> str:
    timestamp = event.timestamp if event.timestamp is not None else "unknown-time"
    header = f"### {timestamp} · `{event.event_id}` · evidence {event.evidence_grade}"
    content = _excerpt(event.content, limit=limit)
    return f"{header}\n\n> " + content.replace("\n", "\n> ") + "\n"


def _slug(label: str, key: str) -> str:
    normalized = unicodedata.normalize("NFKC", label).strip().lower()
    chars: list[str] = []
    for char in normalized:
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    stem = "".join(chars).strip("-")[:48] or "project"
    return f"{stem}-{sha256(key.encode('utf-8')).hexdigest()[:10]}.md"


def tokenize(text: str) -> list[str]:
    # Unicode lexical matching, not translation or linguistic segmentation.
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    fragment = ""
    script = ""

    def flush() -> None:
        if len(fragment) >= 2:
            tokens.append(fragment)
            if script == "cjk" and len(fragment) >= 3:
                tokens.extend(fragment[i : i + 2] for i in range(len(fragment) - 1))

    for char in normalized:
        code = ord(char)
        cjk = (0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x9FFF
               or 0x20000 <= code <= 0x3134F)
        category = unicodedata.category(char)
        kind = "cjk" if cjk else "word" if category[0] in "LN" else ""
        if fragment and (category[0] == "M" or (script == "word" and char in "_.-")):
            fragment += char
        elif kind:
            if script and script != kind:
                flush()
                fragment = ""
            fragment += char
            script = kind
        else:
            flush()
            fragment, script = "", ""
    flush()
    return tokens


def _keywords(text: str, limit: int = 120) -> list[str]:
    counts = Counter(tokenize(text))
    return [token for token, _ in counts.most_common(limit)]


def render_evidence_rules(stats: dict[str, Any], run_id: str) -> str:
    return f"""# Evidence and reading rules

This knowledge base was generated from a frozen Agent-session snapshot `{run_id}`. It is evidence-bound, not a claim that every candidate statement is a confirmed personal fact.

## Frozen scope

| Item | Count |
| --- | ---: |
| Supported source files | {stats.get('source_files', 0)} |
| Parsed files | {stats.get('parsed_files', 0)} |
| Retained unified events | {stats.get('retained_events', 0)} |
| Duplicate events removed | {stats.get('duplicates_removed', 0)} |
| Excluded runtime/summary records | {stats.get('excluded_records', 0)} |
| Parse errors | {stats.get('parse_errors', 0)} |
| Binary payloads removed | {stats.get('binary_payloads_removed', 0)} |
| Credential/contact redactions | {stats.get('sensitive_redactions', 0)} |

## Evidence grades

- **A**: direct statement from a confidently isolated primary user, still bounded by date and context.
- **B**: observable tool, patch, browser, device, status, or delivery evidence. A narrow check proves only what it observed.
- **C**: visible Agent diagnosis or completion statement without matching observation.
- **D**: ambiguous, delegated, imported, test-role, third-party, or unresolved material.

## Isolation rules

Compacted summaries, hidden reasoning, system/developer injection, old-memory reads, and nested approval transcripts are excluded from the evidence corpus. Subagents, client prompts, reference articles, imported sessions, and test roles are never promoted as the primary user. Binary data is represented by a length/hash placeholder; credentials and private contacts are redacted.

## Completion rules

Keep the original request, later correction, implementation evidence, observed validation scope, failure/fallback history, delivery state, and remaining work separate. A file existing, a test passing, a draft being filled, and a public result being reachable are distinct states.

The identity and collaboration documents contain conservative candidates until reviewed against event ids. Project dossiers are evidence ledgers intended for granular reconstruction, not polished success summaries.
"""


def render_identity(events: list[UnifiedEvent]) -> str:
    candidates = [
        event
        for event in events
        if event.actor_kind == "primary_user"
        and event.role == "user"
        and event.evidence_grade == "A"
        and IDENTITY_RE.search(event.content)
        and not set(event.flags) & {"runtime_injection", "delegated_transcript", "nested_approval", "external_material", "test_role", "old_memory_reference"}
    ]
    lines = [
        "# Identity and current direction",
        "",
        "This file contains direct-statement candidates. It does not automatically turn first-person text into biography. Review the surrounding project chain, date, and role provenance before promoting any claim.",
        "",
        "## Direct-statement candidates",
        "",
    ]
    if not candidates:
        lines.append("No safely isolated identity or current-direction candidate was detected. Do not infer one from Agent text or project titles.\n")
    else:
        for event in candidates[:300]:
            lines.append(_quote_card(event))
    lines.extend(
        [
            "## Promotion checklist",
            "",
            "A maintained fact needs the primary user's direct statement or matching observable evidence, a date/context boundary, conflict review against later corrections, and an event id. Time-sensitive metrics remain dated rather than silently updated.",
            "",
        ]
    )
    return "\n".join(lines)


def render_collaboration(events: list[UnifiedEvent]) -> str:
    buckets: dict[str, list[UnifiedEvent]] = defaultdict(list)
    for event in events:
        if event.actor_kind != "primary_user" or event.role != "user" or event.evidence_grade != "A":
            continue
        if set(event.flags) & {"runtime_injection", "delegated_transcript", "nested_approval", "external_material", "test_role"}:
            continue
        content = event.content
        if CORRECTION_RE.search(content):
            buckets["Corrections and scope changes"].append(event)
        if BOUNDARY_RE.search(content):
            buckets["Boundaries and confirmation gates"].append(event)
        if VALIDATION_RE.search(content):
            buckets["Completion and validation expectations"].append(event)
        if EXPRESSION_RE.search(content):
            buckets["Expression and writing feedback"].append(event)
    lines = [
        "# Collaboration and expression rules",
        "",
        "These are dated evidence cards, not frequency-based personality claims. Repeated transcriptions are deduplicated; one short instruction confirms only its immediate context.",
        "",
    ]
    for heading in (
        "Corrections and scope changes",
        "Boundaries and confirmation gates",
        "Completion and validation expectations",
        "Expression and writing feedback",
    ):
        lines.extend([f"## {heading}", ""])
        items = buckets.get(heading, [])
        if not items:
            lines.append("No safely isolated candidate detected.\n")
        else:
            seen: set[str] = set()
            for event in items[:250]:
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                lines.append(_quote_card(event))
    lines.extend(
        [
            "## Maintenance rule",
            "",
            "Promote a collaboration rule only after reading the full surrounding request and later corrections. Preserve where the rule applies, where it does not, and whether it reflects risk, task scope, content taste, or a one-off test.",
            "",
        ]
    )
    return "\n".join(lines)


def render_project(label: str, key: str, events: list[UnifiedEvent]) -> str:
    ordered = sorted(events, key=lambda event: (str(event.timestamp or ""), event.logical_session_id, event.sequence, event.event_id))
    agents = sorted({event.source_agent for event in ordered})
    sessions = sorted({event.logical_session_id for event in ordered})
    first_time = next((event.timestamp for event in ordered if event.timestamp is not None), "unknown")
    last_time = next((event.timestamp for event in reversed(ordered) if event.timestamp is not None), "unknown")
    primary_user = [event for event in ordered if event.actor_kind == "primary_user" and event.role == "user"]
    isolated_user = [event for event in ordered if event.role == "user" and event.actor_kind != "primary_user"]
    implementation = [event for event in ordered if event.event_type in {"message", "tool_call", "tool_result", "patch", "browser", "device", "attachment"} and event.role != "user"]
    failures = [event for event in ordered if event.event_type == "error" or "fallback" in event.flags]
    delivery = [event for event in ordered if event.event_type in {"status", "delivery"}]
    lines = [
        f"# {label}",
        "",
        "## Evidence scope",
        "",
        f"- Project key: `{key}`",
        f"- Agents: {', '.join(agents) or 'unknown'}",
        f"- Logical session chains: {len(sessions)}",
        f"- Retained events: {len(ordered)}",
        f"- Observable time range: {first_time} to {last_time}",
        "",
        "This dossier preserves the granular event chain. It does not infer a smooth completion story from titles or Agent claims.",
        "",
        "## Primary-user requests and corrections",
        "",
    ]
    if not primary_user:
        lines.append("No confidently isolated primary-user event was retained. Keep project ownership and intent unresolved.\n")
    else:
        lines.extend(_quote_card(event, limit=8000) for event in primary_user)
    lines.extend(["## Implementation, observations, and visible Agent text", ""])
    if not implementation:
        lines.append("No retained implementation or observation event.\n")
    else:
        for event in implementation:
            timestamp = event.timestamp if event.timestamp is not None else "unknown-time"
            tool = f" · `{event.tool_name}`" if event.tool_name else ""
            lines.append(f"### {timestamp} · {event.event_type}{tool} · `{event.event_id}` · evidence {event.evidence_grade}\n")
            lines.append(_excerpt(event.content, limit=8000) + "\n")
    lines.extend(["## Errors and fallbacks", ""])
    if not failures:
        lines.append("No retained error/fallback event. This does not prove the project had no failures; inspect excluded and parser audit scope.\n")
    else:
        lines.extend(_quote_card(event, limit=8000) for event in failures)
    lines.extend(["## Delivery and end-state evidence", ""])
    if not delivery:
        lines.append("No explicit delivery/status event was retained. Do not label the project complete.\n")
    else:
        lines.extend(_quote_card(event, limit=8000) for event in delivery[-40:])
    lines.extend(["## Isolated or ambiguous user-role material", ""])
    if not isolated_user:
        lines.append("None retained.\n")
    else:
        for event in isolated_user:
            lines.append(f"- `{event.event_id}` actor `{event.actor_kind}` flags `{', '.join(event.flags)}`: {_excerpt(event.content, 600)}")
        lines.append("")
    return "\n".join(lines)


def render_knowledge(events: list[UnifiedEvent], stats: dict[str, Any], run_id: str) -> tuple[dict[str, str], dict[str, Any]]:
    documents: dict[str, str] = {}
    documents["00-evidence-rules.md"] = render_evidence_rules(stats, run_id)
    documents["01-identity-and-current-direction.md"] = render_identity(events)
    documents["02-collaboration-and-expression.md"] = render_collaboration(events)

    projects: dict[str, list[UnifiedEvent]] = defaultdict(list)
    labels: dict[str, str] = {}
    for event in events:
        key = event.project_key or f"session:{event.logical_session_id}"
        projects[key].append(event)
        labels.setdefault(key, event.project_label or event.session_title or f"Session chain {event.logical_session_id}")
    project_entries: list[dict[str, Any]] = []
    for key in sorted(projects, key=lambda item: (labels[item].lower(), item)):
        label = labels[key]
        filename = _slug(label, key)
        relative = f"projects/{filename}"
        content = render_project(label, key, projects[key])
        documents[relative] = content
        project_entries.append(
            {
                "path": relative,
                "title": label,
                "type": "project",
                "project_key": key,
                "event_count": len(projects[key]),
                "keywords": _keywords(label + "\n" + "\n".join(event.content[:3000] for event in projects[key])),
            }
        )

    base_entries = []
    for path, doc_type in (
        ("00-evidence-rules.md", "evidence"),
        ("01-identity-and-current-direction.md", "identity"),
        ("02-collaboration-and-expression.md", "collaboration"),
    ):
        content = documents[path]
        base_entries.append(
            {
                "path": path,
                "title": content.splitlines()[0].lstrip("# "),
                "type": doc_type,
                "keywords": _keywords(content),
            }
        )
    index = {
        "index_version": 2,
        "semantic_status": "draft",
        "review_required": True,
        "run_id": run_id,
        "documents": base_entries + project_entries,
    }
    documents["knowledge-index.json"] = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return documents, index


def compatibility_markdown(adapter_counts: dict[str, int], verified_adapters: set[str] | None = None) -> str:
    verified = verified_adapters or set()
    rows = [
        ("Codex rollout JSONL", "codex-jsonl"),
        ("Clacky session JSON", "clacky-json"),
        ("Clacky chunk Markdown", "clacky-chunk"),
        ("Claude Code project JSONL", "claude-code-jsonl"),
        ("WorkBuddy project JSONL", "workbuddy-jsonl"),
        ("Neo Claude-runtime JSONL", "neo-claude-jsonl"),
        ("Cursor agent transcript JSONL", "cursor-agent-jsonl"),
    ]
    lines = [
        "# Runtime compatibility matrix",
        "",
        "This matrix reports the current run. An implemented adapter is not automatically a verified compatibility claim.",
        "",
        "| Agent / format | Adapter | Files recognized | Status |",
        "| --- | --- | ---: | --- |",
    ]
    for label, adapter in rows:
        count = adapter_counts.get(adapter, 0)
        if adapter in verified and count:
            status = "verified adapter; current sample parsed"
        elif count:
            status = "implemented; current sample parsed; verification evidence not attached"
        else:
            status = "implemented; no current sample"
        lines.append(f"| {label} | `{adapter}` | {count} | {status} |")
    lines.extend(
        [
            "| Unknown formats | none | 0 | unsupported until adapter and real-sample validation exist |",
            "",
        ]
    )
    return "\n".join(lines)
