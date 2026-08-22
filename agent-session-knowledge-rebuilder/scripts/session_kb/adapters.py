from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

from .model import ParseResult, RawEvent, SourceFile, stable_hash


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return _json_text(block)
    for key in ("text", "content", "input_text", "output_text", "message"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [_block_text(block) for block in content]
        return "\n".join(part for part in parts if part)
    return _json_text(content)


def _safe_subset(record: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: record[key] for key in keys if key in record and record[key] is not None}


def _session_from_filename(path: Path) -> str:
    match = re.search(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", path.name, re.IGNORECASE)
    if match:
        return match.group(1)
    chunk = re.sub(r"-chunk-\d+$", "", path.stem)
    return chunk or stable_hash(path.name, length=20)


def _iter_jsonl(data: bytes, base_offset: int, result: ParseResult):
    cursor = base_offset
    for index, raw_line in enumerate(data.splitlines(keepends=True), start=1):
        start = cursor
        cursor += len(raw_line)
        stripped = raw_line.strip()
        if not stripped:
            result.last_complete_offset = cursor
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            incomplete_tail = not raw_line.endswith((b"\n", b"\r")) and cursor == base_offset + len(data)
            if incomplete_tail:
                result.excluded.append({"record_locator": f"byte:{start}", "reason": "incomplete_jsonl_tail"})
            else:
                result.errors.append(
                    {
                        "record_locator": f"byte:{start}",
                        "error": "json_decode_error",
                        "detail": f"line-relative {index}: {exc.msg}",
                    }
                )
            continue
        if not isinstance(record, dict):
            result.errors.append({"record_locator": f"byte:{start}", "error": "jsonl_record_not_object"})
            result.last_complete_offset = cursor
            continue
        result.records_seen += 1
        result.last_complete_offset = cursor
        yield record, f"byte:{start}"


class SessionAdapter(ABC):
    name = "base"
    agent_name = "Unknown"
    append_only = False

    @abstractmethod
    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        raise NotImplementedError

    @abstractmethod
    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        raise NotImplementedError


class CodexJSONLAdapter(SessionAdapter):
    name = "codex-jsonl"
    agent_name = "Codex"
    append_only = True

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if path.suffix.lower() != ".jsonl":
            return 0, "not JSONL"
        first = head.splitlines()[0] if head.splitlines() else b""
        try:
            record = json.loads(first)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0, "first record is not JSON"
        if isinstance(record, dict) and record.get("type") in {"session_meta", "response_item", "event_msg", "turn_context"} and "payload" in record:
            return 100, "Codex rollout envelope"
        return 0, "no Codex envelope"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        ctx = dict(context or {})
        base_offset = int(ctx.pop("_start_offset", 0))
        result = ParseResult(context=ctx, last_complete_offset=base_offset)
        session_id = str(ctx.get("session_id") or _session_from_filename(source.path))
        parent_id = ctx.get("parent_session_id")
        working_dir = ctx.get("working_dir")
        title = ctx.get("title")
        sequence = int(ctx.get("sequence", 0))

        def emit(locator: str, role: str, event_type: str, content: Any, **kwargs: Any) -> None:
            nonlocal sequence
            sequence += 1
            result.events.append(
                RawEvent(
                    session_id=session_id,
                    parent_session_id=parent_id,
                    sequence=sequence,
                    record_locator=locator,
                    timestamp=kwargs.pop("timestamp", None),
                    role=role,
                    event_type=event_type,
                    content=_json_text(content),
                    working_dir=working_dir,
                    session_title=title,
                    **kwargs,
                )
            )

        for record, locator in _iter_jsonl(data, base_offset, result):
            envelope_type = record.get("type")
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            timestamp = record.get("timestamp") or payload.get("timestamp")
            if envelope_type == "session_meta":
                session_id = str(payload.get("session_id") or payload.get("id") or session_id)
                parent_id = payload.get("parent_thread_id") or payload.get("parent_session_id") or parent_id
                working_dir = payload.get("cwd") or working_dir
                title = payload.get("title") or title
                result.excluded.append({"record_locator": locator, "reason": "session_metadata"})
                continue
            if envelope_type == "compacted":
                result.excluded.append({"record_locator": locator, "reason": "compacted_summary"})
                continue
            if envelope_type in {"turn_context", "world_state"}:
                result.excluded.append({"record_locator": locator, "reason": "runtime_context"})
                continue
            if envelope_type == "response_item":
                item_type = payload.get("type")
                if item_type == "message":
                    role = str(payload.get("role") or "unknown")
                    if role in {"developer", "system"}:
                        result.excluded.append({"record_locator": locator, "reason": "runtime_instruction"})
                        continue
                    visible_parts: list[str] = []
                    attachment_parts: list[Any] = []
                    for block in payload.get("content") or []:
                        if not isinstance(block, dict):
                            visible_parts.append(_json_text(block))
                            continue
                        block_type = block.get("type")
                        if block_type in {"input_text", "output_text", "text"}:
                            visible_parts.append(_block_text(block))
                        elif block_type in {"input_image", "image", "audio", "input_audio"}:
                            attachment_parts.append(_safe_subset(block, ("type", "mime_type", "image_url", "data", "audio")))
                    text = "\n".join(part for part in visible_parts if part)
                    if text:
                        emit(
                            locator,
                            role if role in {"user", "assistant"} else "unknown",
                            "message",
                            text,
                            timestamp=timestamp,
                            actor_hint="unknown_user" if role == "user" else None,
                            flags=["possible_duplicate"],
                            metadata={"transport_lane": "codex-response-item"},
                        )
                    for attachment in attachment_parts:
                        emit(
                            locator,
                            role if role in {"user", "assistant"} else "observer",
                            "attachment",
                            attachment,
                            timestamp=timestamp,
                            actor_hint="unknown_user" if role == "user" else None,
                            metadata={"transport_lane": "codex-response-item"},
                        )
                elif item_type in {"function_call", "custom_tool_call", "local_shell_call", "mcp_tool_call"}:
                    name = payload.get("name") or payload.get("tool_name") or item_type
                    content = payload.get("arguments") if "arguments" in payload else payload.get("input")
                    emit(
                        locator,
                        "assistant",
                        "patch" if name in {"apply_patch", "patch"} else "tool_call",
                        content,
                        timestamp=timestamp,
                        call_id=payload.get("call_id") or payload.get("id"),
                        tool_name=str(name),
                        metadata={"transport_lane": "codex-response-item"},
                    )
                elif item_type in {"function_call_output", "custom_tool_call_output", "mcp_tool_call_output"}:
                    emit(
                        locator,
                        "tool",
                        "tool_result",
                        payload.get("output") if "output" in payload else payload.get("result"),
                        timestamp=timestamp,
                        call_id=payload.get("call_id") or payload.get("id"),
                        metadata={"transport_lane": "codex-response-item"},
                    )
                elif item_type in {"web_search_call", "computer_call", "computer_tool_call"}:
                    emit(locator, "assistant", "browser", _safe_subset(payload, ("action", "query", "status", "url")), timestamp=timestamp)
                elif item_type in {"tool_search_call", "tool_search_output"}:
                    emit(
                        locator,
                        "assistant" if item_type.endswith("call") else "tool",
                        "tool_call" if item_type.endswith("call") else "tool_result",
                        _safe_subset(payload, ("query", "name", "result", "output", "status")),
                        timestamp=timestamp,
                        call_id=payload.get("call_id") or payload.get("id"),
                        tool_name=item_type,
                    )
                elif item_type == "agent_message":
                    emit(
                        locator,
                        "assistant",
                        "message",
                        payload.get("message") or payload.get("content") or _safe_subset(payload, ("text", "status")),
                        timestamp=timestamp,
                        actor_hint="subagent",
                        flags=["subagent"],
                        metadata={"transport_lane": "codex-inter-agent"},
                    )
                elif item_type == "reasoning":
                    result.excluded.append({"record_locator": locator, "reason": "hidden_reasoning_or_summary"})
                else:
                    result.excluded.append({"record_locator": locator, "reason": f"unsupported_codex_response_item:{item_type}"})
                continue
            if envelope_type == "event_msg":
                event_name = str(payload.get("type") or "")
                if event_name in {"agent_reasoning", "token_count", "context_compacted", "thread_settings_applied"}:
                    reason = "compacted_summary" if event_name == "context_compacted" else "runtime_or_reasoning"
                    result.excluded.append({"record_locator": locator, "reason": reason})
                elif event_name == "user_message":
                    emit(
                        locator,
                        "user",
                        "message",
                        payload.get("message") or payload.get("content"),
                        timestamp=timestamp,
                        actor_hint="primary_user",
                        metadata={"transport_lane": "codex-event-message"},
                    )
                elif event_name == "agent_message":
                    emit(
                        locator,
                        "assistant",
                        "message",
                        payload.get("message") or payload.get("content"),
                        timestamp=timestamp,
                        metadata={"transport_lane": "codex-event-message"},
                    )
                elif event_name in {"patch_apply_begin", "patch_apply_end"}:
                    emit(locator, "tool", "patch", _safe_subset(payload, ("changes", "stdout", "stderr", "status", "success")), timestamp=timestamp)
                elif "web_search" in event_name or "browser" in event_name:
                    emit(locator, "observer", "browser", _safe_subset(payload, ("query", "url", "status", "result")), timestamp=timestamp)
                elif "device" in event_name or "computer" in event_name:
                    emit(locator, "observer", "device", _safe_subset(payload, ("action", "status", "result", "device")), timestamp=timestamp)
                elif event_name in {"task_complete", "task_completed"}:
                    emit(locator, "observer", "delivery", payload.get("last_agent_message") or payload.get("message") or _safe_subset(payload, ("status", "result")), timestamp=timestamp)
                elif event_name in {"turn_aborted", "error"}:
                    emit(locator, "observer", "error", _safe_subset(payload, ("reason", "error", "message")), timestamp=timestamp)
                elif event_name in {"task_started", "goal_status", "thread_rolled_back"}:
                    emit(locator, "observer", "status", _safe_subset(payload, ("status", "goal", "reason")), timestamp=timestamp)
                elif event_name == "sub_agent_activity":
                    emit(
                        locator,
                        "observer",
                        "status",
                        _safe_subset(payload, ("status", "task_name", "message", "agent_id", "result")),
                        timestamp=timestamp,
                        actor_hint="subagent",
                        flags=["subagent"],
                    )
                elif event_name in {"mcp_tool_call_end", "web_search_end"}:
                    emit(locator, "observer", "browser", _safe_subset(payload, ("action", "query", "url", "status", "tool_name", "result")), timestamp=timestamp)
                elif event_name == "image_generation_end":
                    emit(locator, "observer", "attachment", _safe_subset(payload, ("saved_path", "status", "revised_prompt", "call_id")), timestamp=timestamp)
                else:
                    result.excluded.append({"record_locator": locator, "reason": f"unsupported_codex_event:{event_name}"})
                continue
            result.excluded.append({"record_locator": locator, "reason": f"unsupported_codex_envelope:{envelope_type}"})

        result.context.update(
            {
                "session_id": session_id,
                "parent_session_id": parent_id,
                "working_dir": working_dir,
                "title": title,
                "sequence": sequence,
            }
        )
        return result


class ClackyJSONAdapter(SessionAdapter):
    name = "clacky-json"
    agent_name = "Clacky"
    append_only = False

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if path.suffix.lower() != ".json":
            return 0, "not JSON"
        try:
            record = json.loads(head.decode("utf-8", errors="strict"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return (25, "possible large Clacky JSON") if b'"messages"' in head and b'"session_id"' in head else (0, "not a session object")
        if isinstance(record, dict) and isinstance(record.get("messages"), list) and ("session_id" in record or "agent_profile" in record):
            return 100, "Clacky session object"
        return 0, "no Clacky message list"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        result = ParseResult(last_complete_offset=len(data))
        try:
            record = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            result.errors.append({"record_locator": "document", "error": "json_decode_error", "detail": str(exc)})
            return result
        if not isinstance(record, dict) or not isinstance(record.get("messages"), list):
            result.errors.append({"record_locator": "document", "error": "not_clacky_session"})
            return result
        session_id = str(record.get("session_id") or _session_from_filename(source.path))
        working_dir = record.get("working_dir")
        project_id = record.get("project_id")
        title = record.get("name") or record.get("goal")
        profile = str(record.get("agent_profile") or "")
        delegated_profile = bool(re.search(r"(?i)(?:sub.?agent|worker|delegate|orchestrator|reviewer|approver|member)$", profile))
        root_time = record.get("created_at")
        sequence = 0
        for index, message in enumerate(record["messages"]):
            result.records_seen += 1
            locator = f"messages[{index}]"
            if not isinstance(message, dict):
                result.errors.append({"record_locator": locator, "error": "message_not_object"})
                continue
            role = str(message.get("role") or "unknown")
            if role == "system":
                result.excluded.append({"record_locator": locator, "reason": "runtime_instruction"})
                continue
            if message.get("compressed_summary"):
                result.excluded.append({"record_locator": locator, "reason": "compacted_summary"})
                continue
            if message.get("system_injected"):
                result.excluded.append({"record_locator": locator, "reason": "runtime_injection"})
                continue
            timestamp = message.get("timestamp") or root_time
            content = _message_text(message.get("content"))
            if content:
                sequence += 1
                result.events.append(
                    RawEvent(
                        session_id=session_id,
                        sequence=sequence,
                        record_locator=locator,
                        timestamp=timestamp,
                        role=role if role in {"user", "assistant", "tool"} else "unknown",
                        event_type="tool_result" if role == "tool" else "message",
                        content=content,
                        call_id=message.get("tool_call_id"),
                        working_dir=working_dir,
                        project_id=project_id,
                        session_title=title,
                        actor_hint=("subagent" if delegated_profile else "primary_user") if role == "user" else None,
                        flags=["subagent"] if delegated_profile else [],
                        metadata={"transport_lane": "clacky-json"},
                    )
                )
            if message.get("reasoning_content"):
                result.excluded.append({"record_locator": locator + ".reasoning_content", "reason": "hidden_reasoning"})
            for call_index, call in enumerate(message.get("tool_calls") or []):
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = function.get("name") or call.get("name") or "tool"
                arguments = function.get("arguments") if "arguments" in function else call.get("arguments")
                sequence += 1
                result.events.append(
                    RawEvent(
                        session_id=session_id,
                        sequence=sequence,
                        record_locator=f"{locator}.tool_calls[{call_index}]",
                        timestamp=timestamp,
                        role="assistant",
                        event_type="patch" if name in {"apply_patch", "patch"} else "tool_call",
                        content=_json_text(arguments),
                        call_id=call.get("id") or call.get("tool_call_id"),
                        tool_name=str(name),
                        working_dir=working_dir,
                        project_id=project_id,
                        session_title=title,
                        actor_hint="subagent" if delegated_profile else None,
                        flags=["subagent"] if delegated_profile else [],
                        metadata={"transport_lane": "clacky-json"},
                    )
                )
        result.context = {
            "session_id": session_id,
            "working_dir": working_dir,
            "project_id": project_id,
            "title": title,
            "sequence": sequence,
        }
        return result


class ClackyChunkAdapter(SessionAdapter):
    name = "clacky-chunk"
    agent_name = "Clacky"
    append_only = False
    heading = re.compile(r"^##\s+(User|Assistant)\s*$", re.MULTILINE | re.IGNORECASE)
    embedded_marker = re.compile(r"(?m)^(?P<call>_Tool calls?:.*?_\s*)$|^###\s+Tool Result:\s*(?P<result>[^\n]+)\s*$", re.IGNORECASE)

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if not re.search(r"-chunk-\d+\.md$", path.name, re.IGNORECASE):
            return 0, "not a Clacky chunk filename"
        text = head.decode("utf-8", errors="ignore")
        if self.heading.search(text):
            return 95, "Clacky User/Assistant chunk"
        return 20, "chunk filename without visible headings"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        text = data.decode("utf-8", errors="replace")
        result = ParseResult(last_complete_offset=len(data))
        frontmatter_session = re.search(r"(?m)^session_id:\s*['\"]?([^'\"\s]+)", text[:4000])
        session_id = frontmatter_session.group(1) if frontmatter_session else _session_from_filename(Path(re.sub(r"-chunk-\d+(?=\.md$)", "", str(source.path))))
        matches = list(self.heading.finditer(text))
        if not matches:
            result.errors.append({"record_locator": "document", "error": "chunk_headings_missing"})
            return result
        if matches[0].start() > 0 and text[: matches[0].start()].strip():
            result.excluded.append({"record_locator": "preamble", "reason": "chunk_summary_or_metadata"})
        sequence = 0
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.end() : end].strip()
            if not content:
                continue
            role = "user" if match.group(1).lower() == "user" else "assistant"
            result.records_seen += 1
            markers = list(self.embedded_marker.finditer(content))

            def emit_part(part_text: str, part_role: str, event_type: str, part_index: int, label: str | None = None) -> None:
                nonlocal sequence
                clean = part_text.strip()
                if not clean:
                    return
                sequence += 1
                result.events.append(
                    RawEvent(
                        session_id=session_id,
                        sequence=sequence,
                        record_locator=f"section:{index + 1}.part:{part_index}",
                        role=part_role,
                        event_type=event_type,
                        content=clean,
                        actor_hint="unknown_user" if part_role == "user" else None,
                        tool_name=label,
                        flags=["possible_duplicate"] if event_type == "message" else [],
                        metadata={"transport_lane": "clacky-chunk"},
                    )
                )

            if not markers:
                emit_part(content, role, "message", 1)
                continue
            cursor = 0
            part_index = 0
            for marker_index, embedded in enumerate(markers):
                part_index += 1
                emit_part(content[cursor : embedded.start()], role, "message", part_index)
                part_index += 1
                if embedded.group("call") is not None:
                    call_text = embedded.group("call").strip().strip("_")
                    if call_text.lower().startswith("tool calls:"):
                        call_text = call_text.split(":", 1)[1].strip()
                    emit_part(call_text, "assistant", "tool_call", part_index, "clacky-embedded-tools")
                    cursor = embedded.end()
                    continue
                next_start = markers[marker_index + 1].start() if marker_index + 1 < len(markers) else len(content)
                body = content[embedded.end() : next_start].strip()
                emit_part(body, "tool", "tool_result", part_index, str(embedded.group("result") or "tool").strip())
                cursor = next_start
            if cursor < len(content):
                part_index += 1
                emit_part(content[cursor:], role, "message", part_index)
        result.context = {"session_id": session_id, "sequence": sequence}
        return result


class ClaudeCodeJSONLAdapter(SessionAdapter):
    name = "claude-code-jsonl"
    agent_name = "Claude Code"
    append_only = True

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if path.suffix.lower() != ".jsonl":
            return 0, "not JSONL"
        first = head.splitlines()[0] if head.splitlines() else b""
        try:
            record = json.loads(first)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0, "first record is not JSON"
        if isinstance(record, dict) and record.get("type") in {"mode", "user", "assistant", "permission-mode", "file-history-snapshot"} and ("sessionId" in record or "message" in record):
            return 98, "Claude Code project record"
        return 0, "no Claude Code record"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        ctx = dict(context or {})
        base_offset = int(ctx.pop("_start_offset", 0))
        result = ParseResult(context=ctx, last_complete_offset=base_offset)
        session_id = str(ctx.get("session_id") or _session_from_filename(source.path))
        parent_session_id = ctx.get("parent_session_id")
        working_dir = ctx.get("working_dir")
        title = ctx.get("title")
        sequence = int(ctx.get("sequence", 0))
        for record, locator in _iter_jsonl(data, base_offset, result):
            record_type = record.get("type")
            session_id = str(record.get("sessionId") or session_id)
            parent_session_id = record.get("parentSessionId") or parent_session_id
            working_dir = record.get("cwd") or working_dir
            timestamp = record.get("timestamp")
            sidechain = bool(record.get("isSidechain"))
            flags = ["sidechain", "subagent"] if sidechain else []
            if record_type == "ai-title":
                title = record.get("title") or record.get("content") or title
                result.excluded.append({"record_locator": locator, "reason": "session_title_metadata"})
                continue
            if record_type in {"mode", "permission-mode", "last-prompt"}:
                result.excluded.append({"record_locator": locator, "reason": "runtime_or_duplicate_metadata"})
                continue
            if record_type == "system":
                reason = "compacted_summary" if record.get("subtype") in {"compact_boundary", "summary"} else "runtime_instruction"
                result.excluded.append({"record_locator": locator, "reason": reason})
                continue
            if record_type in {"user", "assistant"}:
                message = record.get("message") if isinstance(record.get("message"), dict) else {}
                role = str(message.get("role") or record_type)
                content = message.get("content")
                blocks = content if isinstance(content, list) else [content]
                for block_index, block in enumerate(blocks):
                    block_locator = f"{locator}.content[{block_index}]"
                    if isinstance(block, str):
                        sequence += 1
                        result.events.append(RawEvent(session_id, sequence, block_locator, role, "message", block, timestamp, parent_session_id, "subagent" if sidechain else ("primary_user" if role == "user" else None), flags=flags, working_dir=working_dir, session_title=title, metadata={"transport_lane": "claude-code"}))
                        continue
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "thinking":
                        result.excluded.append({"record_locator": block_locator, "reason": "hidden_reasoning"})
                    elif block_type == "text":
                        sequence += 1
                        result.events.append(RawEvent(session_id, sequence, block_locator, role, "message", _block_text(block), timestamp, parent_session_id, "subagent" if sidechain else ("primary_user" if role == "user" else None), flags=flags, working_dir=working_dir, session_title=title, metadata={"transport_lane": "claude-code"}))
                    elif block_type == "tool_use":
                        sequence += 1
                        name = block.get("name") or "tool"
                        result.events.append(RawEvent(session_id, sequence, block_locator, "assistant", "patch" if name in {"Edit", "Write", "apply_patch"} else "tool_call", _json_text(block.get("input")), timestamp, parent_session_id, "subagent" if sidechain else None, block.get("id"), str(name), working_dir, None, title, flags))
                    elif block_type == "tool_result":
                        sequence += 1
                        result.events.append(RawEvent(session_id, sequence, block_locator, "tool", "error" if block.get("is_error") else "tool_result", _message_text(block.get("content")), timestamp, parent_session_id, "subagent" if sidechain else None, block.get("tool_use_id"), None, working_dir, None, title, flags))
                    elif block_type in {"image", "document", "attachment"}:
                        sequence += 1
                        result.events.append(RawEvent(session_id, sequence, block_locator, role, "attachment", _json_text(_safe_subset(block, ("type", "source", "name", "mime_type"))), timestamp, parent_session_id, flags=flags, working_dir=working_dir, session_title=title))
                    else:
                        result.excluded.append({"record_locator": block_locator, "reason": f"unsupported_claude_block:{block_type}"})
                continue
            if record_type == "attachment":
                sequence += 1
                result.events.append(RawEvent(session_id, sequence, locator, "user", "attachment", _json_text(_safe_subset(record, ("fileName", "filePath", "mimeType", "size"))), timestamp, parent_session_id, flags=flags, working_dir=working_dir, session_title=title))
            elif record_type == "file-history-snapshot":
                sequence += 1
                result.events.append(RawEvent(session_id, sequence, locator, "observer", "patch", _json_text(_safe_subset(record, ("snapshot", "messageId", "trackedFileBackups"))), timestamp, parent_session_id, flags=flags, working_dir=working_dir, session_title=title))
            elif record_type == "queue-operation":
                sequence += 1
                result.events.append(RawEvent(session_id, sequence, locator, "observer", "status", _json_text(_safe_subset(record, ("operation", "status"))), timestamp, parent_session_id, flags=flags, working_dir=working_dir, session_title=title))
            else:
                result.excluded.append({"record_locator": locator, "reason": f"unsupported_claude_record:{record_type}"})
        result.context.update({"session_id": session_id, "parent_session_id": parent_session_id, "working_dir": working_dir, "title": title, "sequence": sequence})
        return result


class WorkBuddyJSONLAdapter(SessionAdapter):
    name = "workbuddy-jsonl"
    agent_name = "WorkBuddy"
    append_only = True

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if path.suffix.lower() != ".jsonl":
            return 0, "not JSONL"
        first = head.splitlines()[0] if head.splitlines() else b""
        try:
            record = json.loads(first)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0, "first record is not JSON"
        if isinstance(record, dict) and record.get("type") in {"message", "function_call", "function_call_result", "reasoning", "ai-title"} and "sessionId" in record and ("providerData" in record or "role" in record):
            return 99, "WorkBuddy project record"
        return 0, "no WorkBuddy record"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        ctx = dict(context or {})
        base_offset = int(ctx.pop("_start_offset", 0))
        result = ParseResult(context=ctx, last_complete_offset=base_offset)
        session_id = str(ctx.get("session_id") or _session_from_filename(source.path))
        working_dir = ctx.get("working_dir")
        title = ctx.get("title")
        sequence = int(ctx.get("sequence", 0))
        for record, locator in _iter_jsonl(data, base_offset, result):
            record_type = record.get("type")
            session_id = str(record.get("sessionId") or session_id)
            working_dir = record.get("cwd") or working_dir
            timestamp = record.get("timestamp")
            if record_type == "ai-title":
                title = record.get("title") or record.get("content") or title
                result.excluded.append({"record_locator": locator, "reason": "session_title_metadata"})
            elif record_type == "message":
                role = str(record.get("role") or "unknown")
                blocks = record.get("content") if isinstance(record.get("content"), list) else [record.get("content")]
                text = "\n".join(_block_text(block) for block in blocks if _block_text(block))
                if text:
                    sequence += 1
                    result.events.append(RawEvent(session_id, sequence, locator, role, "message", text, timestamp, actor_hint="primary_user" if role == "user" else None, working_dir=working_dir, session_title=title, metadata={"transport_lane": "workbuddy"}))
            elif record_type == "function_call":
                sequence += 1
                name = record.get("name") or "tool"
                result.events.append(RawEvent(session_id, sequence, locator, "assistant", "patch" if name in {"apply_patch", "Edit", "Write"} else "tool_call", _json_text(record.get("arguments")), timestamp, call_id=record.get("callId") or record.get("id"), tool_name=str(name), working_dir=working_dir, session_title=title))
            elif record_type == "function_call_result":
                sequence += 1
                result.events.append(RawEvent(session_id, sequence, locator, "tool", "tool_result", _json_text(record.get("output")), timestamp, call_id=record.get("callId") or record.get("id"), tool_name=record.get("name"), working_dir=working_dir, session_title=title))
            elif record_type == "reasoning":
                result.excluded.append({"record_locator": locator, "reason": "hidden_reasoning"})
            elif record_type == "file-history-snapshot":
                sequence += 1
                result.events.append(RawEvent(session_id, sequence, locator, "observer", "patch", _json_text(_safe_subset(record, ("snapshot", "messageId", "files"))), timestamp, working_dir=working_dir, session_title=title))
            else:
                result.excluded.append({"record_locator": locator, "reason": f"unsupported_workbuddy_record:{record_type}"})
        result.context.update({"session_id": session_id, "working_dir": working_dir, "title": title, "sequence": sequence})
        return result


class NeoClaudeJSONLAdapter(ClaudeCodeJSONLAdapter):
    """Neo project sessions produced by its embedded Claude-compatible runtime."""

    name = "neo-claude-jsonl"
    agent_name = "Neo"

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if path.suffix.lower() != ".jsonl":
            return 0, "not JSONL"
        records: list[dict[str, Any]] = []
        for line in head.splitlines()[:32]:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                records.append(record)
        if not records:
            return 0, "no JSONL records"
        in_neo_root = any(part.lower() == ".neo" for part in path.parts) and "projects" in {part.lower() for part in path.parts}
        claude_score, _ = super().probe(path, head)
        has_queue = any(record.get("type") == "queue-operation" and record.get("sessionId") for record in records)
        has_external_sdk_message = any(
            record.get("type") in {"user", "assistant"}
            and record.get("sessionId")
            and record.get("entrypoint") == "sdk-ts"
            and record.get("userType") == "external"
            for record in records
        )
        if in_neo_root and (claude_score > 0 or has_queue or has_external_sdk_message):
            return 100, "Neo Claude-runtime project stream"
        if has_queue and has_external_sdk_message:
            return 97, "portable Neo Claude-runtime stream"
        return 0, "no Neo runtime signature"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        result = super().parse(data, source, context)
        for event in result.events:
            event.metadata = {**event.metadata, "transport_lane": "neo-claude-runtime"}
        return result


class CursorAgentJSONLAdapter(SessionAdapter):
    name = "cursor-agent-jsonl"
    agent_name = "Cursor"
    append_only = True

    def probe(self, path: Path, head: bytes) -> tuple[int, str]:
        if path.suffix.lower() != ".jsonl":
            return 0, "not JSONL"
        first = next((line for line in head.splitlines() if line.strip()), b"")
        try:
            record = json.loads(first)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0, "first record is not JSON"
        message = record.get("message") if isinstance(record, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not (
            isinstance(record, dict)
            and record.get("role") in {"user", "assistant"}
            and isinstance(content, list)
            and all(isinstance(block, dict) and "type" in block for block in content)
        ):
            return 0, "no Cursor transcript message"
        if "agent-transcripts" in {part.lower() for part in path.parts}:
            return 100, "Cursor agent transcript stream"
        return 96, "portable Cursor agent transcript stream"

    def parse(self, data: bytes, source: SourceFile, context: dict[str, Any] | None = None) -> ParseResult:
        ctx = dict(context or {})
        base_offset = int(ctx.pop("_start_offset", 0))
        result = ParseResult(context=ctx, last_complete_offset=base_offset)
        session_id = str(ctx.get("session_id") or _session_from_filename(source.path))
        sequence = int(ctx.get("sequence", 0))
        for record, locator in _iter_jsonl(data, base_offset, result):
            role = str(record.get("role") or "unknown")
            record_type = record.get("type")
            if role in {"user", "assistant"} and isinstance(record.get("message"), dict):
                content = record["message"].get("content")
                blocks = content if isinstance(content, list) else [content]
                for block_index, block in enumerate(blocks):
                    block_locator = f"{locator}.content[{block_index}]"
                    if isinstance(block, str):
                        sequence += 1
                        result.events.append(
                            RawEvent(
                                session_id,
                                sequence,
                                block_locator,
                                role,
                                "message",
                                block,
                                actor_hint="primary_user" if role == "user" else None,
                                metadata={"transport_lane": "cursor-agent-transcript"},
                            )
                        )
                        continue
                    if not isinstance(block, dict):
                        result.excluded.append({"record_locator": block_locator, "reason": "unsupported_cursor_block"})
                        continue
                    block_type = str(block.get("type") or "")
                    if block_type == "text":
                        text = _block_text(block)
                        if text:
                            sequence += 1
                            result.events.append(
                                RawEvent(
                                    session_id,
                                    sequence,
                                    block_locator,
                                    role,
                                    "message",
                                    text,
                                    actor_hint="primary_user" if role == "user" else None,
                                    metadata={"transport_lane": "cursor-agent-transcript"},
                                )
                            )
                    elif block_type == "tool_use":
                        sequence += 1
                        name = str(block.get("name") or "tool")
                        result.events.append(
                            RawEvent(
                                session_id,
                                sequence,
                                block_locator,
                                "assistant",
                                "patch" if name in {"apply_patch", "Edit", "Write"} else "tool_call",
                                _json_text(block.get("input")),
                                call_id=block.get("id"),
                                tool_name=name,
                                metadata={"transport_lane": "cursor-agent-transcript"},
                            )
                        )
                    elif block_type == "tool_result":
                        sequence += 1
                        result.events.append(
                            RawEvent(
                                session_id,
                                sequence,
                                block_locator,
                                "tool",
                                "error" if block.get("is_error") else "tool_result",
                                _message_text(block.get("content")),
                                call_id=block.get("tool_use_id") or block.get("id"),
                                metadata={"transport_lane": "cursor-agent-transcript"},
                            )
                        )
                    elif block_type in {"thinking", "reasoning"}:
                        result.excluded.append({"record_locator": block_locator, "reason": "hidden_reasoning"})
                    elif block_type in {"image", "file", "attachment"}:
                        sequence += 1
                        result.events.append(
                            RawEvent(
                                session_id,
                                sequence,
                                block_locator,
                                role,
                                "attachment",
                                _json_text(_safe_subset(block, ("type", "name", "mime_type", "size", "source"))),
                                actor_hint="primary_user" if role == "user" else None,
                                metadata={"transport_lane": "cursor-agent-transcript"},
                            )
                        )
                    else:
                        result.excluded.append({"record_locator": block_locator, "reason": f"unsupported_cursor_block:{block_type}"})
                continue
            if record_type == "turn_ended":
                sequence += 1
                result.events.append(
                    RawEvent(
                        session_id,
                        sequence,
                        locator,
                        "observer",
                        "status",
                        _json_text(_safe_subset(record, ("status", "reason"))),
                        metadata={"transport_lane": "cursor-agent-transcript"},
                    )
                )
            elif record_type in {"system", "summary", "compaction"}:
                result.excluded.append({"record_locator": locator, "reason": "compacted_summary" if record_type != "system" else "runtime_instruction"})
            else:
                result.excluded.append({"record_locator": locator, "reason": f"unsupported_cursor_record:{record_type or role}"})
        result.context.update({"session_id": session_id, "sequence": sequence})
        return result


class AdapterRegistry:
    def __init__(self) -> None:
        self.adapters: dict[str, SessionAdapter] = {}

    def register(self, adapter: SessionAdapter) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", adapter.name):
            raise ValueError(f"invalid adapter name: {adapter.name}")
        if adapter.name in self.adapters:
            raise ValueError(f"duplicate adapter: {adapter.name}")
        self.adapters[adapter.name] = adapter

    def get(self, name: str) -> SessionAdapter:
        try:
            return self.adapters[name]
        except KeyError as exc:
            raise ValueError(f"unknown adapter: {name}") from exc

    def identify(self, path: Path, head: bytes) -> tuple[SessionAdapter | None, list[dict[str, Any]]]:
        scores: list[tuple[int, SessionAdapter, str]] = []
        for adapter in self.adapters.values():
            score, reason = adapter.probe(path, head)
            if score > 0:
                scores.append((score, adapter, reason))
        scores.sort(key=lambda item: (-item[0], item[1].name))
        audit = [{"adapter": item[1].name, "score": item[0], "reason": item[2]} for item in scores]
        if not scores:
            return None, audit
        if len(scores) > 1 and scores[0][0] == scores[1][0]:
            return None, audit
        return scores[0][1], audit

    def load_directory(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = "session_kb_external_" + stable_hash(path.resolve(), length=16)
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"cannot load adapter module: {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise ValueError(f"adapter module lacks register(registry): {path}")
            register(self)


def builtin_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(CodexJSONLAdapter())
    registry.register(ClackyJSONAdapter())
    registry.register(ClackyChunkAdapter())
    registry.register(ClaudeCodeJSONLAdapter())
    registry.register(WorkBuddyJSONLAdapter())
    registry.register(NeoClaudeJSONLAdapter())
    registry.register(CursorAgentJSONLAdapter())
    return registry


def environment_root_hints() -> list[tuple[str, Path]]:
    home = Path.home()
    hints: list[tuple[str, Path]] = []

    def add(label: str, path: Path) -> None:
        expanded = path.expanduser()
        if (label, expanded) not in hints:
            hints.append((label, expanded))

    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    add("codex-sessions", codex_home / "sessions")
    add("codex-archived", codex_home / "archived_sessions")
    clacky_home = Path(os.environ.get("CLACKY_HOME", home / ".clacky"))
    add("clacky-sessions", clacky_home / "sessions")
    add("clacky-trash", clacky_home / "trash")
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    add("claude-projects", claude_home / "projects")
    workbuddy_home = Path(os.environ.get("WORKBUDDY_HOME", home / ".workbuddy"))
    add("workbuddy-projects", workbuddy_home / "projects")
    neo_home = Path(os.environ.get("NEO_HOME", home / ".neo"))
    add("neo-projects", neo_home / "projects")
    cursor_home = Path(os.environ.get("CURSOR_HOME", home / ".cursor"))
    add("cursor-agent-transcripts", cursor_home / "projects")
    grok_home = Path(os.environ.get("GROK_HOME", home / ".grok"))
    add("grok-sessions-unverified", grok_home / "sessions")

    if sys.platform == "darwin":
        platform_config_roots = [home / "Library" / "Application Support"]
    elif os.name == "nt" or sys.platform.startswith("win"):
        platform_config_roots = [
            Path(value)
            for value in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"))
            if value
        ]
    else:
        platform_config_roots = [
            Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")),
            Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")),
        ]

    portable_candidates = [
        ("opencode-candidate", Path(os.environ.get("OPENCODE_HOME", home / ".opencode"))),
        ("opencode-config-candidate", home / ".config" / "opencode"),
        ("opencode-data-candidate", home / ".local" / "share" / "opencode"),
        ("gemini-candidate", Path(os.environ.get("GEMINI_HOME", home / ".gemini"))),
        ("aider-candidate", Path(os.environ.get("AIDER_HOME", home / ".aider"))),
        ("goose-candidate", Path(os.environ.get("GOOSE_HOME", home / ".config" / "goose"))),
        ("goose-data-candidate", home / ".local" / "share" / "goose"),
        ("amp-candidate", Path(os.environ.get("AMP_HOME", home / ".amp"))),
        ("qwen-candidate", Path(os.environ.get("QWEN_HOME", home / ".qwen"))),
        ("kimi-candidate", Path(os.environ.get("KIMI_HOME", home / ".kimi"))),
        ("cline-candidate", Path(os.environ.get("CLINE_HOME", home / ".cline"))),
        ("continue-candidate", Path(os.environ.get("CONTINUE_HOME", home / ".continue"))),
        ("openhands-candidate", Path(os.environ.get("OPENHANDS_HOME", home / ".openhands"))),
        ("plandex-candidate", Path(os.environ.get("PLANDEX_HOME", home / ".plandex"))),
        ("gptme-candidate", Path(os.environ.get("GPTME_HOME", home / ".gptme"))),
    ]
    for label, path in portable_candidates:
        add(label, path)

    for raw in os.environ.get("AGENT_SESSION_ROOTS", "").split(os.pathsep):
        if raw.strip():
            add("environment-explicit", Path(raw.strip()))

    agent_words = re.compile(
        r"agent|codex|claude|clacky|workbuddy|grok|opencode|cursor|windsurf|gemini|trae|aider|cline|roo|continue|cody|copilot|goose|amp|qwen|kimi|neo|sheetagent|mirasim|crush|zed|openhands|plandex|gptme|tabby|sweep",
        re.IGNORECASE,
    )
    try:
        for child in home.iterdir():
            if child.is_dir() and agent_words.search(child.name):
                add("environment-candidate", child)
    except OSError:
        pass
    for app_root in platform_config_roots:
        for editor in ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf", "Trae"):
            add("application-candidate", app_root / editor / "User" / "globalStorage")
        if not app_root.is_dir():
            continue
        try:
            for child in app_root.iterdir():
                if child.is_dir() and agent_words.search(child.name):
                    add("application-candidate", child)
        except OSError:
            pass
    return hints
