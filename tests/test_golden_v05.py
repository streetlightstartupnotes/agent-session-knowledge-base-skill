from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent-session-knowledge-rebuilder" / "scripts"))

from session_kb.adapters import builtin_registry  # noqa: E402
from session_kb.golden import run_golden_manifest  # noqa: E402


class GoldenManifestV05Tests(unittest.TestCase):
    def test_public_synthetic_golden_pack_covers_every_declared_adapter(self) -> None:
        fixture = PROJECT_ROOT / "tests" / "fixtures" / "golden" / "v1"
        with tempfile.TemporaryDirectory() as directory:
            report = run_golden_manifest(
                builtin_registry(),
                fixture,
                fixture / "manifest.json",
                output_root=Path(directory),
            )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["fixture_kind"], "public-synthetic")
        self.assertEqual(report["case_count"], 7)
        self.assertEqual(report["failed_count"], 0)
        self.assertFalse(report["compatibility_claim_updated"])
        adapters = {
            adapter
            for case in report["cases"]
            for adapter in (case.get("adapter_counts") or {})
        }
        self.assertEqual(
            adapters,
            {
                "codex-jsonl",
                "clacky-json",
                "clacky-chunk",
                "claude-code-jsonl",
                "workbuddy-jsonl",
                "neo-claude-jsonl",
                "cursor-agent-jsonl",
            },
        )

    def test_manifest_rejects_source_escape(self) -> None:
        fixture = PROJECT_ROOT / "tests" / "fixtures" / "golden" / "v1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "golden_version": 1,
                        "cases": [
                            {
                                "id": "escape",
                                "source_root": "../outside",
                                "expected_adapters": {"codex-jsonl": 1},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsafe|escapes"):
                run_golden_manifest(builtin_registry(), root, manifest, output_root=root / "output")

    def test_unknown_file_makes_a_mixed_golden_case_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "rollout.jsonl").write_text(
                '{"type":"session_meta","payload":{"id":"synthetic-mixed"}}\n'
                '{"type":"event_msg","payload":{"type":"user_message","message":"Synthetic request."}}\n',
                encoding="utf-8",
            )
            (source / "unknown.jsonl").write_text('{"unrecognized_shape":true}\n', encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "golden_version": 1,
                        "fixture_kind": "private-user-held",
                        "cases": [
                            {
                                "id": "mixed",
                                "source_root": "source",
                                "expected_adapters": {"codex-jsonl": 1},
                                "minimum_events": 1,
                                "required_event_types": ["message"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = run_golden_manifest(builtin_registry(), root, manifest, output_root=root / "output")
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["cases"][0]["checks"]["unsupported_clear"])
            self.assertEqual(report["cases"][0]["unsupported_count"], 1)


if __name__ == "__main__":
    unittest.main()
