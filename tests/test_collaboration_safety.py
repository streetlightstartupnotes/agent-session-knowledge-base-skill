"""Invented fixtures only; deterministic checks are not host-behavior evals."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-session-knowledge-rebuilder" / "scripts"))
from session_kb.sanitize import Sanitizer, actor_kind, classify_flags
from session_kb.release import SENSITIVE_PATTERNS
from session_kb.cli import rebuild_summary, make_parser


class CollaborationSafetyTests(unittest.TestCase):
    def test_summary_is_bounded_and_does_not_claim_review(self):
        result = {"run_id": "fixture", "event_count": 3, "excluded": [{"body": "x" * 1000}] * 1000,
                  "errors": [{"error": "fixture"}], "completion": {"status": "needs_semantic_review"}}
        summary = rebuild_summary(result, Path("private-fixture"), False)
        self.assertLess(len(json.dumps(summary)), 2000)
        self.assertEqual(summary["counts"]["excluded"], 1000)
        self.assertEqual(summary["counts"]["errors"], 1)
        self.assertEqual(summary["completion"]["status"], "needs_semantic_review")
        self.assertIsNone(rebuild_summary(result, Path("private-fixture"), True)["audit_directory"])
        self.assertTrue(make_parser().parse_args(["rebuild", "--output", "private-fixture", "--summary"]).summary)

    def test_vendor_and_proxy_login_material_removed_and_release_detected(self):
        values = ["ark-" + "FixtureX9" * 4, "apikey_" + "FixtureY8" * 4]
        values.extend(scheme + "://" + "EncodedFixture9" * 3 + "@example.invalid:443"
                      for scheme in ("ss", "ssr", "vmess", "vless", "trojan", "hy2", "tuic"))
        for value in values:
            with self.subTest(kind=value.split(":")[0][:7]):
                self.assertNotIn(value, Sanitizer().sanitize_text("configured " + value + " then failed"))
                self.assertTrue(any(p.search(value) for p in SENSITIVE_PATTERNS.values()))

    def test_labeled_values_keep_event_meaning_without_credentials(self):
        opaque = "InventedValue" + "9827"
        for label in ("密码是", "口令：", "密钥为", "API key:", "password="):
            source = "连接失败，" + label + opaque + "，用户稍后纠正目标"
            result = Sanitizer().sanitize_text(source)
            self.assertNotIn(opaque, result)
            self.assertIn("用户稍后纠正目标", result)
            self.assertTrue(any(p.search(source) for p in SENSITIVE_PATTERNS.values()))

    def test_mapping_keys_and_national_identifier(self):
        opaque = "InventedValue" + "9827"
        result = Sanitizer().sanitize_mapping({"api_key": opaque, "密码": opaque, "purpose": "test"})
        self.assertNotIn(opaque, json.dumps(result))
        identifier = "110101" + "19900101" + "123X"
        self.assertNotIn(identifier, Sanitizer().sanitize_text("ID: " + identifier))
        self.assertNotIn(identifier, Sanitizer().sanitize_text("身份证" + identifier + "已删除"))
        self.assertTrue(SENSITIVE_PATTERNS["national_id"].search(identifier))

    def test_opaque_and_explicit_reasoning_not_republished(self):
        value = json.dumps({"encrypted_content": "OpaqueFixture" * 20, "text": "visible outcome"})
        result = Sanitizer().sanitize_text(value)
        self.assertNotIn("OpaqueFixture", result)
        self.assertIn("visible outcome", result)
        plain = {"encrypted_content": "可见的代理交接，不是人类授权\nPreserve this message."}
        self.assertIn("可见的代理交接", Sanitizer().sanitize_text(json.dumps(plain, ensure_ascii=False)))
        self.assertEqual(plain, Sanitizer().sanitize_mapping(plain))
        self.assertEqual("[EXPLICIT_REASONING_REMOVED]visible", Sanitizer().sanitize_text("<think>not evidence</think>visible"))

    def test_exact_policy_and_fixture_not_primary_human(self):
        for body in ('{"outcome":"allow"}', '{"outcome":"deny","rationale":"test"}'):
            flags = classify_flags(body)
            self.assertIn("nested_approval", flags)
            self.assertNotEqual("primary_agent", actor_kind("assistant", flags))
        self.assertIn("synthetic_fixture", classify_flags("Response 2: " + "Detailed response " * 8))
        self.assertIn("synthetic_fixture", classify_flags("Response 1: " + "Answer " * 8))
        for body in ('Please explain {"outcome":"allow"}', 'Response 2: I reject the new layout',
                     '{"outcome":"allow","user_text":"Please continue"}', '{"outcome":{}}', '{"outcome":[]}'):
            self.assertNotIn("nested_approval", classify_flags(body))
            self.assertNotIn("synthetic_fixture", classify_flags(body))

    def test_wrappers_are_not_human_endorsement(self):
        for body in ("Base directory for this skill: /fixture", "<!-- maintenance-runner · crystallize · test -->"):
            self.assertIn("runtime_injection", classify_flags(body))
        self.assertIn("compacted_summary", classify_flags("This session is being continued from a previous conversation that ran out of context. summary"))


if __name__ == "__main__":
    unittest.main()
