from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "agent-session-knowledge-rebuilder"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from session_kb.adapters import builtin_registry, environment_root_hints  # noqa: E402
from session_kb.config import default_registry_path, output_guidance, register_knowledge_base, validate_output_location  # noqa: E402
from session_kb.discovery import discover, freeze_sources  # noqa: E402
from session_kb.inventory import fingerprint_unknown  # noqa: E402
from session_kb.model import REQUIRED_EVENT_FIELDS  # noqa: E402
from session_kb.pipeline import build_knowledge_base  # noqa: E402
from session_kb.query import query_knowledge  # noqa: E402
from session_kb.release import release_check  # noqa: E402
from session_kb.review import create_review_packet, create_review_template, distill_review, validate_review  # noqa: E402
from session_kb.verification import verify_retrieval, verify_retrieval_suite  # noqa: E402


READER_PATH = PROJECT_ROOT / "agent-knowledge-reader" / "scripts" / "read_knowledge.py"
READER_SPEC = importlib.util.spec_from_file_location("agent_knowledge_reader_test", READER_PATH)
assert READER_SPEC and READER_SPEC.loader
READER = importlib.util.module_from_spec(READER_SPEC)
READER_SPEC.loader.exec_module(READER)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def codex_message(role: str, text: str) -> dict:
    block_type = "output_text" if role == "assistant" else "input_text"
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": block_type, "text": text}]},
    }


def codex_visible_event(role: str, text: str) -> dict:
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "type": "event_msg",
        "payload": {"type": "user_message" if role == "user" else "agent_message", "message": text},
    }


class SessionKnowledgeBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = builtin_registry()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self, source_root: Path, output: Path | None = None, incremental: bool = False, dry_run: bool = False):
        destination = output or self.root / "kb"
        discovery = discover(self.registry, roots=[source_root], output_dir=destination)
        snapshot = freeze_sources(discovery.sources)
        snapshot["discovery"] = discovery.to_dict()
        result = build_knowledge_base(
            self.registry,
            discovery.sources,
            destination,
            discovery=discovery,
            snapshot=snapshot,
            incremental=incremental,
            dry_run=dry_run,
        )
        return destination, discovery, result

    def test_format_recognition_and_unknown_report(self) -> None:
        fixture = self.root / "formats"
        codex = fixture / "rollout.jsonl"
        write_jsonl(codex, [{"type": "session_meta", "payload": {"id": "codex-1", "cwd": "/work"}}])
        clacky = fixture / "clacky.json"
        clacky.write_text(json.dumps({"session_id": "clacky-1", "messages": []}), encoding="utf-8")
        chunk = fixture / "2026-01-01-00-00-00-12345678-1234-1234-1234-123456789abc-chunk-1.md"
        chunk.write_text("## User\nhello\n\n## Assistant\nhi\n", encoding="utf-8")
        claude = fixture / "claude.jsonl"
        write_jsonl(claude, [{"type": "mode", "sessionId": "claude-1", "mode": "default"}])
        workbuddy = fixture / "workbuddy.jsonl"
        write_jsonl(
            workbuddy,
            [{"type": "message", "sessionId": "workbuddy-1", "providerData": {}, "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        )
        neo = fixture / ".neo" / "projects" / "sample" / "neo.jsonl"
        write_jsonl(
            neo,
            [
                {
                    "type": "user",
                    "sessionId": "neo-1",
                    "entrypoint": "sdk-ts",
                    "userType": "external",
                    "message": {"role": "user", "content": [{"type": "text", "text": "hello from neo"}]},
                }
            ],
        )
        cursor = fixture / ".cursor" / "projects" / "sample" / "agent-transcripts" / "cursor.jsonl"
        write_jsonl(
            cursor,
            [{"role": "user", "message": {"content": [{"type": "text", "text": "hello from cursor"}]}}],
        )
        unknown = fixture / "active_sessions.json"
        unknown.write_text("[]", encoding="utf-8")

        expected = {
            codex: "codex-jsonl",
            clacky: "clacky-json",
            chunk: "clacky-chunk",
            claude: "claude-code-jsonl",
            workbuddy: "workbuddy-jsonl",
            neo: "neo-claude-jsonl",
            cursor: "cursor-agent-jsonl",
        }
        for path, name in expected.items():
            adapter, _ = self.registry.identify(path, path.read_bytes()[:131072])
            self.assertIsNotNone(adapter, path)
            self.assertEqual(adapter.name, name)
        discovery = discover(self.registry, roots=[fixture])
        self.assertEqual(len(discovery.sources), 7)
        self.assertTrue(any(item.get("path", "").endswith("active_sessions.json") for item in discovery.unsupported))

    def test_neo_and_cursor_unified_events_and_exclusions(self) -> None:
        fixture = self.root / "new-adapters"
        neo = fixture / ".neo" / "projects" / "sample" / "neo.jsonl"
        write_jsonl(
            neo,
            [
                {
                    "type": "user",
                    "sessionId": "neo-session",
                    "entrypoint": "sdk-ts",
                    "userType": "external",
                    "cwd": "/work/neo-project",
                    "message": {"role": "user", "content": [{"type": "text", "text": "Build the Neo project."}]},
                },
                {
                    "type": "assistant",
                    "sessionId": "neo-session",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "neo hidden reasoning"},
                            {"type": "tool_use", "id": "neo-call", "name": "Edit", "input": {"path": "app.py"}},
                        ],
                    },
                },
                {
                    "type": "user",
                    "sessionId": "neo-session",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "neo-call", "content": "edit applied"}],
                    },
                },
            ],
        )
        cursor = fixture / ".cursor" / "projects" / "sample" / "agent-transcripts" / "cursor.jsonl"
        write_jsonl(
            cursor,
            [
                {"role": "user", "message": {"content": [{"type": "text", "text": "Build the Cursor project."}]}},
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {"type": "reasoning", "text": "cursor hidden reasoning"},
                            {"type": "tool_use", "id": "cursor-call", "name": "terminal", "input": {"command": "test"}},
                            {"type": "tool_result", "tool_use_id": "cursor-call", "content": "tests passed"},
                        ]
                    },
                },
                {"type": "turn_ended", "status": "success"},
            ],
        )
        output, _, result = self._build(fixture)
        self.assertEqual(result["stats"]["adapter_counts"], {"cursor-agent-jsonl": 1, "neo-claude-jsonl": 1})
        events = read_jsonl(output / "audit" / "events.jsonl")
        by_adapter = {adapter: [event for event in events if event["adapter"] == adapter] for adapter in ("neo-claude-jsonl", "cursor-agent-jsonl")}
        self.assertTrue({"message", "patch", "tool_result"}.issubset({event["event_type"] for event in by_adapter["neo-claude-jsonl"]}))
        self.assertTrue({"message", "tool_call", "tool_result", "status"}.issubset({event["event_type"] for event in by_adapter["cursor-agent-jsonl"]}))
        combined = "\n".join(event["content"] for event in events)
        self.assertNotIn("hidden reasoning", combined)
        exclusions = read_jsonl(output / "audit" / "excluded.jsonl")
        self.assertGreaterEqual(sum(item.get("reason") == "hidden_reasoning" for item in exclusions), 2)

    def test_cross_platform_discovery_roots_and_registry_locations(self) -> None:
        cases = (
            ("darwin", "macos", "Library/Application Support"),
            ("win32", "windows", "AppData/Roaming"),
            ("linux", "linux", ".config"),
        )
        for platform_name, family, registry_fragment in cases:
            with self.subTest(platform=platform_name):
                fake_home = self.root / platform_name / "home"
                appdata = fake_home / "AppData" / "Roaming"
                xdg_config = fake_home / ".config"
                application_root = fake_home / "Library" / "Application Support" if platform_name == "darwin" else (appdata if platform_name == "win32" else xdg_config)
                (application_root / "NicheAgent").mkdir(parents=True, exist_ok=True)
                environment = {
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(fake_home / "AppData" / "Local"),
                    "XDG_CONFIG_HOME": str(xdg_config),
                    "XDG_DATA_HOME": str(fake_home / ".local" / "share"),
                }
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch("session_kb.adapters.Path.home", return_value=fake_home),
                    patch("session_kb.config.Path.home", return_value=fake_home),
                    patch("session_kb.adapters.sys.platform", platform_name),
                    patch("session_kb.config.sys.platform", platform_name),
                ):
                    hints = environment_root_hints()
                    registry_path = default_registry_path()
                labels = {label for label, _ in hints}
                self.assertTrue({"codex-sessions", "clacky-sessions", "claude-projects", "neo-projects", "cursor-agent-transcripts"}.issubset(labels))
                self.assertIn("application-candidate", labels)
                self.assertEqual(family, "windows" if platform_name == "win32" else ("macos" if platform_name == "darwin" else "linux"))
                self.assertIn(registry_fragment, registry_path.as_posix())

    def test_unknown_fingerprint_never_emits_record_values(self) -> None:
        secret_value = "do-not-emit-" + "value"
        unknown = self.root / "unknown.jsonl"
        write_jsonl(
            unknown,
            [
                {"type": secret_value, "role": secret_value, "content": secret_value, "nested": {"private": secret_value}},
                {"type": secret_value, "content": [secret_value]},
            ],
        )
        fingerprint = fingerprint_unknown(unknown)
        serialized = json.dumps(fingerprint, ensure_ascii=False)
        self.assertNotIn(secret_value, serialized)
        self.assertEqual(fingerprint["value_types_by_key"]["content"], ["list", "str"])
        self.assertIn("nested", fingerprint["top_level_keys"])

    def test_environment_auto_discovery_uses_probes_not_fixed_machine_state(self) -> None:
        fake_home = self.root / "portable-home"
        source = fake_home / ".codex" / "sessions" / "2026" / "01" / "01" / "rollout.jsonl"
        write_jsonl(source, [{"type": "session_meta", "payload": {"id": "portable", "cwd": "/portable/project"}}])
        environment = {
            "CODEX_HOME": str(fake_home / ".codex"),
            "CLACKY_HOME": str(fake_home / ".clacky"),
            "CLAUDE_CONFIG_DIR": str(fake_home / ".claude"),
            "WORKBUDDY_HOME": str(fake_home / ".workbuddy"),
            "AGENT_SESSION_ROOTS": "",
        }
        with patch.dict("os.environ", environment, clear=False), patch("session_kb.adapters.Path.home", return_value=fake_home):
            discovery = discover(self.registry)
        self.assertEqual(len(discovery.sources), 1)
        self.assertEqual(discovery.sources[0].adapter, "codex-jsonl")
        self.assertEqual(discovery.sources[0].path, source)

    def test_bounded_discovery_reports_coverage_gap_not_false_unsupported(self) -> None:
        fixture = self.root / "bounded"
        first = fixture / "a-rollout.jsonl"
        second = fixture / "b-session.json"
        write_jsonl(first, [{"type": "session_meta", "payload": {"id": "bounded-codex"}}])
        second.parent.mkdir(parents=True, exist_ok=True)
        second.write_text(json.dumps({"session_id": "bounded-clacky", "messages": []}), encoding="utf-8")
        discovery = discover(self.registry, roots=[fixture], max_files=1)
        self.assertEqual(len(discovery.sources), 1)
        self.assertEqual(discovery.unsupported, [])
        self.assertEqual(discovery.coverage_gaps[0]["reason"], "max_files_limit_reached")
        self.assertEqual(discovery.roots[0]["scan_status"], "truncated_by_max_files")
        snapshot = freeze_sources(discovery.sources)
        snapshot["discovery"] = discovery.to_dict()
        output = self.root / "bounded-kb"
        result = build_knowledge_base(self.registry, discovery.sources, output, discovery=discovery, snapshot=snapshot)
        self.assertFalse(result["completion"]["gates"]["discovery_coverage_complete"])
        self.assertTrue((output / "audit" / "coverage-gaps.json").is_file())

    def test_explicit_custom_adapter_registration_contract(self) -> None:
        adapter_dir = self.root / "adapters"
        adapter_dir.mkdir()
        (adapter_dir / "sample_adapter.py").write_text(
            "from session_kb.adapters import SessionAdapter\n"
            "from session_kb.model import ParseResult\n"
            "class SampleAdapter(SessionAdapter):\n"
            "    name='sample-jsonl'\n"
            "    agent_name='Sample Agent'\n"
            "    append_only=True\n"
            "    def probe(self, path, head): return (90, 'sample') if path.suffix == '.sample' else (0, 'no')\n"
            "    def parse(self, data, source, context=None): return ParseResult(last_complete_offset=len(data))\n"
            "def register(registry): registry.register(SampleAdapter())\n",
            encoding="utf-8",
        )
        registry = builtin_registry()
        registry.load_directory(adapter_dir)
        self.assertEqual(registry.get("sample-jsonl").agent_name, "Sample Agent")

    def test_unified_events_and_summary_exclusion(self) -> None:
        fixture = self.root / "codex"
        source = fixture / "rollout.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": "s-1", "session_id": "s-1", "cwd": "/work/project-alpha"}},
            codex_message("developer", "runtime-only"),
            codex_message("user", "I am building Project Alpha."),
            codex_message("assistant", "I will implement it."),
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec", "arguments": "run tests", "call_id": "c-1"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "tests passed", "call_id": "c-1"}},
            {"type": "event_msg", "payload": {"type": "patch_apply_end", "changes": ["a.py"]}},
            {"type": "event_msg", "payload": {"type": "web_search_end", "query": "docs", "status": "ok"}},
            {"type": "event_msg", "payload": {"type": "device_connected", "device": "simulator", "status": "ok"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "delivered"}},
            {"type": "event_msg", "payload": {"type": "context_compacted", "summary": "must not survive"}},
        ]
        write_jsonl(source, records)
        output, _, _ = self._build(fixture)
        events = read_jsonl(output / "audit" / "events.jsonl")
        event_types = {event["event_type"] for event in events}
        self.assertTrue({"message", "tool_call", "tool_result", "patch", "browser", "device", "delivery"}.issubset(event_types))
        for event in events:
            self.assertTrue(REQUIRED_EVENT_FIELDS.issubset(event))
        all_text = "\n".join(event["content"] for event in events)
        self.assertNotIn("runtime-only", all_text)
        self.assertNotIn("must not survive", all_text)
        exclusions = read_jsonl(output / "audit" / "excluded.jsonl")
        self.assertTrue(any(item["reason"] == "compacted_summary" for item in exclusions))

    def test_clacky_json_chunk_deduplication(self) -> None:
        fixture = self.root / "clacky"
        fixture.mkdir()
        uuid = "12345678-1234-1234-1234-123456789abc"
        base = f"2026-01-01-00-00-00-{uuid}"
        (fixture / f"{base}.json").write_text(
            json.dumps(
                {
                    "session_id": uuid,
                    "working_dir": "/work/dedup-project",
                    "messages": [
                        {"role": "user", "content": "Build the dedup project."},
                        {"role": "assistant", "content": "Implemented the dedup project."},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (fixture / f"{base}-chunk-1.md").write_text(
            "## User\nBuild the dedup project.\n\n## Assistant\nImplemented the dedup project.\n",
            encoding="utf-8",
        )
        output, _, result = self._build(fixture)
        events = read_jsonl(output / "audit" / "events.jsonl")
        self.assertEqual(sum(event["content"] == "Build the dedup project." for event in events), 1)
        self.assertEqual(sum(event["content"] == "Implemented the dedup project." for event in events), 1)
        self.assertGreaterEqual(result["stats"]["duplicates_removed"], 2)

    def test_parent_child_sessions_merge_into_one_logical_chain(self) -> None:
        fixture = self.root / "continuation"
        write_jsonl(
            fixture / "parent.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "parent-session"}},
                codex_visible_event("user", "Start the continuation-safe project."),
            ],
        )
        write_jsonl(
            fixture / "child.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {"id": "child-session", "parent_session_id": "parent-session"},
                },
                codex_visible_event("assistant", "Continue and deliver the same project."),
            ],
        )

        output, _, _ = self._build(fixture)
        events = read_jsonl(output / "audit" / "events.jsonl")
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event["logical_session_id"] for event in events}), 1)
        self.assertEqual(len({event["project_key"] for event in events}), 1)
        review_summary = create_review_template(output)
        review = json.loads(Path(review_summary["review_file"]).read_text(encoding="utf-8"))
        self.assertEqual(len(review["projects"]), 1)
        self.assertEqual(review["projects"][0]["event_count"], 2)

    def test_clacky_chunk_embedded_calls_and_results_are_normalized(self) -> None:
        fixture = self.root / "clacky-chunk-tools"
        fixture.mkdir()
        chunk = fixture / "2026-01-01-00-00-00-abcdef12-chunk-1.md"
        chunk.write_text(
            "---\nsession_id: abcdef1234567890\n---\n\n# Session Chunk 1\n\n"
            "## User\n\nPlease inspect the project.\n\n"
            "### Tool Result: tool\n\n```\npreloaded observation\n```\n\n"
            "## Assistant\n\n_Tool calls: terminal | {\"command\":\"run tests\"}_\n\n"
            "Running the check now.\n\n### Tool Result: terminal\n\n```\ntests passed\n```\n",
            encoding="utf-8",
        )
        output, _, _ = self._build(fixture)
        events = read_jsonl(output / "audit" / "events.jsonl")
        types = [event["event_type"] for event in events]
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertTrue(any(event["session_id"] == "abcdef1234567890" for event in events))
        user_event = next(event for event in events if "inspect the project" in event["content"])
        self.assertNotIn("preloaded observation", user_event["content"])

    def test_identity_isolation_across_delegation_sidechain_and_test_role(self) -> None:
        fixture = self.root / "identity"
        codex = fixture / "codex.jsonl"
        write_jsonl(
            codex,
            [
                {"type": "session_meta", "payload": {"id": "primary", "cwd": "/work/identity"}},
                codex_message("user", "I am a product designer and I am building an editor."),
                codex_visible_event("user", "I am a product designer and I am building an editor."),
                codex_message("user", "<codex_delegation><input>I am a client banker.</input></codex_delegation>"),
                codex_visible_event("user", "<codex_delegation><input>I am a client banker.</input></codex_delegation>"),
                codex_visible_event("user", "Only reply with OK. I am a test doctor."),
            ],
        )
        claude = fixture / "claude.jsonl"
        write_jsonl(
            claude,
            [
                {
                    "type": "user",
                    "sessionId": "sidechain",
                    "isSidechain": True,
                    "message": {"role": "user", "content": [{"type": "text", "text": "I am a customer lawyer."}]},
                }
            ],
        )
        output, _, _ = self._build(fixture)
        identity = (output / "knowledge" / "01-identity-and-current-direction.md").read_text(encoding="utf-8")
        self.assertNotIn("product designer", identity)
        self.assertIn("No safely isolated identity", identity)
        self.assertNotIn("client banker", identity)
        self.assertNotIn("test doctor", identity)
        self.assertNotIn("customer lawyer", identity)
        events = read_jsonl(output / "audit" / "events.jsonl")
        actor_by_fragment = {fragment: next(event["actor_kind"] for event in events if fragment in event["content"]) for fragment in ("product designer", "test doctor", "customer lawyer")}
        self.assertEqual(actor_by_fragment["product designer"], "native_user")
        self.assertEqual(actor_by_fragment["test doctor"], "test_actor")
        self.assertEqual(actor_by_fragment["customer lawyer"], "subagent")
        self.assertFalse(any("client banker" in event["content"] for event in events))
        dispositions = read_jsonl(output / "audit" / "dispositions.jsonl")
        self.assertTrue(any(item.get("reason") == "delegated_transcript" for item in dispositions))

    def test_privacy_and_binary_are_removed_from_every_artifact(self) -> None:
        token = "gh" + "p_" + "A" * 30
        email = "private" + "@example.com"
        phone = "138" + "0013" + "8000"
        assigned_value = "pass" + "word=" + "HiddenValue42"
        header_value = "Cook" + "ie: session=abc123456789"
        slack = "xoxb-" + "A" * 24
        private_ip = ".".join(("192", "168", "23", "42"))
        binary = base64.b64encode(b"\x89PNG" + b"x" * 5000).decode("ascii")
        fixture = self.root / (email + "-" + phone)
        fixture.mkdir()
        (fixture / "session.json").write_text(
            json.dumps(
                {
                    "session_id": "privacy-1",
                    "messages": [
                        {"role": "user", "content": f"{token} {email} {phone} {assigned_value} {header_value} {slack} {private_ip} {Path.home()}/private/file"},
                        {"role": "assistant", "content": binary},
                    ],
                }
            ),
            encoding="utf-8",
        )
        output, _, result = self._build(fixture)
        combined = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in output.rglob("*") if path.is_file())
        for secret in (token, email, phone, "HiddenValue42", "abc123456789", slack, private_ip, str(Path.home()), binary):
            self.assertNotIn(secret, combined)
        self.assertIn("[BINARY_REMOVED", combined)
        self.assertIn("[REDACTED:EMAIL]", combined)
        self.assertGreater(result["stats"]["sensitive_redactions"], 0)
        self.assertGreater(result["stats"]["binary_payloads_removed"], 0)

    def test_incremental_freeze_reads_only_verified_tail(self) -> None:
        fixture = self.root / "incremental"
        source = fixture / "rollout.jsonl"
        write_jsonl(
            source,
            [
                {"type": "session_meta", "payload": {"id": "inc-1", "session_id": "inc-1", "cwd": "/work/incremental"}},
                codex_message("user", "First incremental message."),
            ],
        )
        output, _, first = self._build(fixture)
        first_review = create_review_template(output)
        first_review_path = Path(first_review["review_file"])
        first_review_text = first_review_path.read_text(encoding="utf-8")
        first_size = source.stat().st_size
        with source.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(codex_message("assistant", "Second incremental message.")) + "\n")
        _, _, second = self._build(fixture, output=output, incremental=True)
        self.assertLess(second["stats"]["incremental_bytes_parsed"], first_size)
        self.assertEqual(second["stats"]["append_verification_bytes_read"], first_size)
        self.assertEqual(second["stats"]["parsed_prefix_integrity_bytes_read"], first_size)
        self.assertEqual(second["stats"]["incremental_integrity_bytes_read"], 2 * first_size)
        self.assertEqual(second["stats"]["incremental_bytes_read"], source.stat().st_size + first_size)
        events = read_jsonl(output / "audit" / "events.jsonl")
        self.assertEqual(sum("incremental message" in event["content"] for event in events), 2)
        snapshots = list((output / "audit" / "snapshots").glob("*.json"))
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(first["stats"]["retained_events"], 1)
        self.assertEqual(second["stats"]["retained_events"], 2)
        next_review = create_review_template(output)
        self.assertNotEqual(Path(next_review["review_file"]), first_review_path)
        self.assertEqual(first_review_path.read_text(encoding="utf-8"), first_review_text)
        self.assertEqual(next_review["run_id"], second["run_id"])

    def test_incremental_rewrite_falls_back_to_audited_full_reparse(self) -> None:
        fixture = self.root / "rewrite"
        source = fixture / "rollout.jsonl"
        write_jsonl(
            source,
            [
                {"type": "session_meta", "payload": {"id": "rewrite-1", "cwd": "/work/rewrite"}},
                codex_visible_event("user", "Original retained request."),
            ],
        )
        output, _, _ = self._build(fixture)
        write_jsonl(
            source,
            [
                {"type": "session_meta", "payload": {"id": "rewrite-1", "cwd": "/work/rewrite"}},
                codex_visible_event("user", "Replacement retained request after source rewrite."),
            ],
        )
        _, _, second = self._build(fixture, output=output, incremental=True)
        self.assertEqual(second["stats"]["reparsed_files"], 1)
        self.assertGreater(second["stats"]["full_reparse_bytes_read"], 0)
        errors = read_jsonl(output / "audit" / "errors.jsonl")
        self.assertTrue(any(item.get("error") == "incremental_fallback_full_reparse" for item in errors))
        combined = "\n".join(event["content"] for event in read_jsonl(output / "audit" / "events.jsonl"))
        self.assertIn("Replacement retained request", combined)
        self.assertNotIn("Original retained request", combined)

    def test_rebuild_session_that_mentions_its_own_output_is_quarantined(self) -> None:
        fixture = self.root / "self-rebuild"
        output = self.root / "self-generated-kb"
        marker = f"Run session_kb.py and inspect {output.resolve()}/audit/events.jsonl"
        write_jsonl(
            fixture / "rollout.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "self-1", "cwd": "/work/self"}},
                codex_visible_event("user", marker),
                codex_visible_event("assistant", "Starting the reconstruction."),
            ],
        )
        result_output, _, result = self._build(fixture, output=output)
        self.assertEqual(result["stats"]["retained_events"], 0)
        self.assertEqual(read_jsonl(result_output / "audit" / "events.jsonl"), [])
        exclusions = read_jsonl(result_output / "audit" / "excluded.jsonl")
        self.assertTrue(any(item.get("reason") == "self_reconstruction_session" for item in exclusions))

    def test_output_contract_query_and_dry_run(self) -> None:
        fixture = self.root / "output"
        source = fixture / "workbuddy.jsonl"
        write_jsonl(
            source,
            [
                {
                    "type": "message",
                    "sessionId": "portfolio-1",
                    "providerData": {},
                    "cwd": "/work/portfolio-project",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Continue the portfolio interface project."}],
                },
                {
                    "type": "message",
                    "sessionId": "portfolio-1",
                    "providerData": {},
                    "cwd": "/work/portfolio-project",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Delivered the portfolio interface draft."}],
                },
            ],
        )
        output, _, _ = self._build(fixture)
        required = [
            output / "knowledge" / "00-evidence-rules.md",
            output / "knowledge" / "01-identity-and-current-direction.md",
            output / "knowledge" / "02-collaboration-and-expression.md",
            output / "knowledge" / "knowledge-index.json",
            output / "audit" / "stats.json",
            output / "audit" / "events.jsonl",
            output / "audit" / "dispositions.jsonl",
            output / "audit" / "completion-report.json",
            output / "audit" / "unsupported-formats.json",
            output / "audit" / "coverage-gaps.json",
            output / "audit" / "compatibility-matrix.md",
        ]
        self.assertTrue(all(path.is_file() for path in required))
        self.assertTrue(any((output / "knowledge" / "projects").glob("*.md")))
        with self.assertRaises(ValueError):
            query_knowledge(output, "continue the portfolio interface", max_projects=1)
        review_path = output / "review" / "review.json"
        create_review_template(output, review_path)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewer"] = {
            "id": "test-reviewer",
            "reviewed_at": "2026-01-02T00:00:00Z",
            "attestation": "I read every ordered event in each project chain and preserved uncertainty.",
        }
        events = read_jsonl(output / "audit" / "events.jsonl")
        for project in review["projects"]:
            project_events = [event for event in events if event["project_key"] == project["project_key"]]
            project["semantic_status"] = "reviewed"
            project["reading_receipts"] = [create_review_packet(output, project["project_key"])["receipt"]]
            project["link_analysis"] = {
                "status": "intentional-isolate",
                "rationale": "Only one reviewed project chain exists in this fixture.",
            }
            project["completion"] = {
                "level": "requested",
                "status": "unverified",
                "evidence_event_ids": [project_events[0]["event_id"]],
                "rationale": "The user request is observed; stronger layers remain represented separately in the history.",
            }
            project["history"]["objective"] = [
                {
                    "text": "Continue the portfolio interface project.",
                    "evidence_event_ids": [project_events[0]["event_id"]],
                    "status": "observed",
                }
            ]
            project["history"]["delivery_and_state"] = [
                {
                    "text": "The visible Agent reported a delivered draft; no independent interaction check was recorded.",
                    "evidence_event_ids": [project_events[-1]["event_id"]],
                    "status": "agent-reported",
                }
            ]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, review_errors = validate_review(output, review_path)
        self.assertEqual(review_errors, [])
        distilled = distill_review(output, review_path)
        self.assertEqual(distilled["status"], "needs_retrieval_verification")
        project_key = review["projects"][0]["project_key"]
        verification = verify_retrieval_suite(
            output,
            {
                "cases": [
                    {"kind": "related", "task": "continue the portfolio interface", "expected_project_keys": [project_key]},
                    {"kind": "related", "task": "portfolio interface project", "expected_project_keys": [project_key]},
                    {"kind": "hard_negative", "task": "unrelated quantum botany archive"},
                    {"kind": "hard_negative", "task": "ceramic orchard forecast"},
                ]
            },
        )
        self.assertEqual(verification["status"], "passed")
        query = query_knowledge(output, "continue the portfolio interface", max_projects=1)
        self.assertEqual(query["documents"][0]["type"], "evidence")
        self.assertEqual(sum(item["type"] == "project" for item in query["documents"]), 1)
        self.assertTrue((output / "audit" / "semantic-dispositions.jsonl").is_file())
        completion = json.loads((output / "audit" / "completion-report.json").read_text(encoding="utf-8"))
        self.assertTrue(completion["gates"]["semantic_review_complete"])
        self.assertEqual(completion["semantic_dispositions"], len(events))
        registry_path = self.root / "reader-config" / "locations.json"
        registered = register_knowledge_base("portfolio", output, registry_path, make_default=True)
        self.assertEqual(registered["default"], "portfolio")
        self.assertEqual(READER.resolve_registered(None, registry_path), output.resolve())
        reader_result = READER.query(READER.resolve_registered("portfolio", registry_path), "continue the portfolio interface", max_projects=1)
        self.assertEqual(reader_result["project_matches"], 1)
        dry_output = self.root / "dry-run-output"
        _, _, dry = self._build(fixture, output=dry_output, dry_run=True)
        self.assertTrue(dry["dry_run"] if "dry_run" in dry else dry["stats"]["dry_run"])
        self.assertFalse(dry_output.exists())

    def test_output_location_is_guided_and_separated_from_sources(self) -> None:
        fixture = self.root / "source-sessions"
        source = fixture / "rollout.jsonl"
        write_jsonl(source, [{"type": "session_meta", "payload": {"id": "location-1"}}, codex_message("user", "Choose an output first.")])
        discovery = discover(self.registry, roots=[fixture])
        guidance = output_guidance("portable-kb", cwd=self.root / "private-project")
        self.assertIn("Confirm one path before rebuild writes anything", guidance["question"])
        self.assertEqual([item["id"] for item in guidance["options"]], ["personal", "project-local", "custom"])
        with self.assertRaises(ValueError):
            validate_output_location(fixture / "generated", discovery.sources)
        repository = self.root / "public-repository"
        (repository / ".git").mkdir(parents=True)
        with self.assertRaises(ValueError):
            validate_output_location(repository / "private-looking-output", discovery.sources)
        safe = validate_output_location(self.root / "separate-output", discovery.sources)
        self.assertEqual(safe, (self.root / "separate-output").resolve())

    def test_clacky_and_claude_native_summaries_are_excluded(self) -> None:
        fixture = self.root / "summaries"
        fixture.mkdir()
        (fixture / "clacky.json").write_text(
            json.dumps(
                {
                    "session_id": "clacky-summary",
                    "messages": [
                        {"role": "user", "content": "compressed secret conclusion", "compressed_summary": True},
                        {"role": "user", "content": "system injected profile", "system_injected": True},
                        {"role": "assistant", "content": "visible", "reasoning_content": "hidden chain"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        write_jsonl(
            fixture / "claude.jsonl",
            [
                {
                    "type": "assistant",
                    "sessionId": "claude-summary",
                    "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden thought"}, {"type": "text", "text": "visible claude"}]},
                }
            ],
        )
        write_jsonl(
            fixture / "workbuddy.jsonl",
            [
                {"type": "reasoning", "sessionId": "wb-summary", "providerData": {}, "content": "hidden workbuddy thought"},
                {"type": "message", "sessionId": "wb-summary", "providerData": {}, "role": "assistant", "content": [{"type": "output_text", "text": "visible workbuddy"}]},
            ],
        )
        output, _, _ = self._build(fixture)
        combined = "\n".join(event["content"] for event in read_jsonl(output / "audit" / "events.jsonl"))
        self.assertIn("visible", combined)
        for hidden in ("compressed secret conclusion", "system injected profile", "hidden chain", "hidden thought", "hidden workbuddy thought"):
            self.assertNotIn(hidden, combined)

    def test_nested_transcript_quarantine_and_transport_accounting(self) -> None:
        fixture = self.root / "nested"
        write_jsonl(
            fixture / "rollout.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "nested-1", "cwd": "/work/nested"}},
                codex_visible_event("user", "Build the visible project."),
                codex_visible_event(
                    "user",
                    "The following is the Codex agent history whose request action you are assessing.\n>>> TRANSCRIPT START\nI am a fake customer.\n>>> APPROVAL REQUEST START",
                ),
                {"type": "event_msg", "payload": {"type": "context_compacted", "summary": "invented compacted identity"}},
            ],
        )
        output, _, result = self._build(fixture)
        event_text = "\n".join(event["content"] for event in read_jsonl(output / "audit" / "events.jsonl"))
        self.assertIn("visible project", event_text)
        self.assertNotIn("fake customer", event_text)
        self.assertNotIn("invented compacted identity", event_text)
        dispositions = read_jsonl(output / "audit" / "dispositions.jsonl")
        self.assertTrue(any(item.get("reason") == "imported_transcript" for item in dispositions))
        self.assertEqual(result["stats"]["transport_records_unaccounted"], 0)

    def test_genuine_repetition_survives_transport_dedup(self) -> None:
        fixture = self.root / "repetition"
        write_jsonl(
            fixture / "rollout.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "repeat-1", "cwd": "/work/repeat"}},
                codex_visible_event("user", "Continue."),
                codex_visible_event("assistant", "First continuation done."),
                codex_visible_event("user", "Continue."),
                codex_visible_event("assistant", "Second continuation done."),
            ],
        )
        output, _, _ = self._build(fixture)
        events = read_jsonl(output / "audit" / "events.jsonl")
        self.assertEqual(sum(event["content"] == "Continue." for event in events), 2)

    def test_review_gate_rejects_unreviewed_projects_and_unsupported_claims(self) -> None:
        fixture = self.root / "review-gate"
        write_jsonl(
            fixture / "rollout.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "review-1", "cwd": "/work/review"}},
                codex_visible_event("assistant", "The user is definitely a surgeon."),
            ],
        )
        output, _, _ = self._build(fixture)
        review_path = output / "review.json"
        create_review_template(output, review_path)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewer"] = {"id": "reviewer", "reviewed_at": "2026-01-02", "attestation": "Read all retained events."}
        review["base_claims"] = [
            {
                "claim_id": "claim-surgeon",
                "knowledge_type": "identity",
                "subject": "primary_user",
                "statement": "The primary user is a surgeon.",
                "status": "confirmed",
                "confidence": "high",
                "evidence_event_ids": [read_jsonl(output / "audit" / "events.jsonl")[0]["event_id"]],
                "observed_at": "2026-01-01",
                "applies_to": "identity",
                "conflicts": [],
                "supersedes": [],
            }
        ]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertTrue(any("semantic_status" in error for error in errors))
        self.assertTrue(any("primary-user evidence" in error for error in errors))

    def test_evidence_bound_relationships_publish_reciprocal_links_and_expand_queries(self) -> None:
        fixture = self.root / "relationships"
        write_jsonl(
            fixture / "origin.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "origin-session", "cwd": "/work/origin"}},
                codex_visible_event("user", "Build a shared engine as the foundation for the dashboard."),
                codex_message("assistant", "Created the shared engine artifact."),
            ],
        )
        write_jsonl(
            fixture / "dashboard.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "dashboard-session", "cwd": "/work/dashboard"}},
                codex_visible_event("user", "Continue from the shared engine and use that artifact in the dashboard."),
                codex_message("assistant", "Integrated the shared engine into the dashboard draft."),
            ],
        )
        output, _, _ = self._build(fixture)
        review_path = output / "review" / "review.json"
        create_review_template(output, review_path)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewer"] = {
            "id": "relationship-reviewer",
            "reviewed_at": "2026-01-02T00:00:00Z",
            "attestation": "I read both complete project chains and checked the relation against both user-intent records.",
        }
        events = read_jsonl(output / "audit" / "events.jsonl")
        events_by_key: dict[str, list[dict]] = {}
        for event in events:
            events_by_key.setdefault(event["project_key"], []).append(event)
        projects_by_title = {project["title"]: project for project in review["projects"]}
        packet = create_review_packet(output, projects_by_title["origin"]["project_key"])
        self.assertEqual(packet["event_count"], len(packet["ordered_events"]))
        self.assertTrue(all(event["project_key"] == projects_by_title["origin"]["project_key"] for event in packet["ordered_events"]))
        self.assertNotIn("dashboard draft", json.dumps(packet, ensure_ascii=False))
        for title, project in projects_by_title.items():
            project_events = events_by_key[project["project_key"]]
            user_event = next(event for event in project_events if event["role"] == "user")
            project["semantic_status"] = "reviewed"
            project["reading_receipts"] = [create_review_packet(output, project["project_key"])["receipt"]]
            project["link_analysis"] = {
                "status": "linked",
                "rationale": "The user explicitly connects the shared artifact and continuation across both project chains.",
            }
            project["completion"] = {
                "level": "requested",
                "status": "unverified",
                "evidence_event_ids": [user_event["event_id"]],
                "rationale": "The request is directly observed; the fixture does not promote a stronger completion layer.",
            }
            project["history"]["objective"] = [
                {"text": user_event["content"], "evidence_event_ids": [user_event["event_id"]], "status": "observed"}
            ]
        origin = projects_by_title["origin"]
        dashboard = projects_by_title["dashboard"]
        origin_user = next(event for event in events_by_key[origin["project_key"]] if event["role"] == "user")
        dashboard_user = next(event for event in events_by_key[dashboard["project_key"]] if event["role"] == "user")
        relationship = {
            "relationship_id": "rel-origin-dashboard",
            "source_project_key": origin["project_key"],
            "target_project_key": dashboard["project_key"],
            "relation": "continued-as",
            "evidence_basis": ["explicit-user-intent", "shared-artifact"],
            "direction": "directed",
            "status": "confirmed",
            "confidence": "high",
            "source_evidence_event_ids": [origin_user["event_id"]],
            "target_evidence_event_ids": [dashboard_user["event_id"]],
            "rationale": "The first intent names the foundation artifact and the later intent explicitly continues from and reuses it.",
        }
        review["project_relationships"] = [{**relationship, "evidence_basis": ["keyword-overlap"]}]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, lexical_errors = validate_review(output, review_path)
        self.assertTrue(any("lexical-only" in error for error in lexical_errors))

        review["project_relationships"] = [relationship]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertEqual(errors, [])
        distilled = distill_review(output, review_path)
        self.assertEqual(distilled["published_project_relationships"], 1)
        graph = json.loads((output / "knowledge" / "knowledge-graph.json").read_text(encoding="utf-8"))
        graph_relation = next(edge for edge in graph["edges"] if edge["edge_id"] == "rel-origin-dashboard")
        self.assertEqual(graph_relation["evidence_basis"], ["explicit-user-intent", "shared-artifact"])
        self.assertTrue(graph_relation["source_anchor_nodes"])
        self.assertTrue(graph_relation["target_anchor_nodes"])
        self.assertGreaterEqual(sum(node["kind"] == "project_assertion" for node in graph["nodes"]), 2)
        index = json.loads((output / "knowledge" / "knowledge-index.json").read_text(encoding="utf-8"))
        path_by_key = {item["project_key"]: item["path"] for item in index["documents"] if item.get("type") == "project"}
        origin_text = (output / "knowledge" / path_by_key[origin["project_key"]]).read_text(encoding="utf-8")
        dashboard_text = (output / "knowledge" / path_by_key[dashboard["project_key"]]).read_text(encoding="utf-8")
        self.assertIn("[dashboard]", origin_text)
        self.assertIn("[origin]", dashboard_text)
        self.assertIn("rel-origin-dashboard", (output / "audit" / "relationships.json").read_text(encoding="utf-8"))
        query = query_knowledge(output, "shared engine foundation origin", max_projects=1, max_related=1, min_score=1)
        self.assertEqual(query["project_matches"], 1)
        self.assertEqual(query["related_documents"], 1)
        self.assertEqual(sum(item["type"] == "project" for item in query["documents"]), 2)

    def test_intentional_isolate_and_release_privacy_contract(self) -> None:
        fixture = self.root / "isolate"
        write_jsonl(
            fixture / "rollout.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "isolated", "cwd": "/work/isolated"}},
                codex_message("user", "Create a standalone experiment with no known project dependency."),
            ],
        )
        output, _, _ = self._build(fixture)
        review_path = output / "review.json"
        create_review_template(output, review_path)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewer"] = {"id": "reviewer", "reviewed_at": "2026-01-02", "attestation": "Read the complete isolated chain."}
        event = read_jsonl(output / "audit" / "events.jsonl")[0]
        project = review["projects"][0]
        project["semantic_status"] = "reviewed"
        project["reading_receipts"] = [create_review_packet(output, project["project_key"])["receipt"]]
        project["link_analysis"] = {"status": "intentional-isolate", "rationale": "No evidence in the frozen records connects this experiment to another reviewed chain."}
        project["completion"] = {
            "level": "requested",
            "status": "unverified",
            "evidence_event_ids": [event["event_id"]],
            "rationale": "The standalone request is observed and no stronger completion is claimed.",
        }
        project["history"]["objective"] = [{"text": event["content"], "evidence_event_ids": [event["event_id"]], "status": "observed"}]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        distill_review(output, review_path)
        link_audit = json.loads((output / "audit" / "link-audit.json").read_text(encoding="utf-8"))
        project_audit = next(item for item in link_audit if item.get("project_key"))
        self.assertTrue(project_audit["intentional_isolate"])

        public_tree = self.root / "public-tree"
        public_tree.mkdir()
        (public_tree / "SKILL.md").write_text("safe source\n", encoding="utf-8")
        self.assertEqual(release_check(public_tree)["status"], "passed")
        private_value = "owner" + "@example.org"
        (public_tree / "notes.md").write_text(f"contact={private_value}\n", encoding="utf-8")
        report = release_check(public_tree, deny_terms=[private_value])
        self.assertEqual(report["status"], "failed")
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(private_value, serialized)
        self.assertTrue({"email", "deny-term-1"}.issubset({item["kind"] for item in report["findings"]}))


if __name__ == "__main__":
    unittest.main()
