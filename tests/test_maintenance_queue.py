from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_retrieval_gate import READER
from session_kb import maintenance as m


class MaintenanceQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.page = self.root / "project.md"
        self.page.write_text("# Existing project\n")
        m.configure(self.root, ["project.md"], "user-turn-permission")

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self):
        return m.stage(self.root, "project.md", "Prototype runs on the bench; field use remains unknown.", "user-turn-7")

    def test_stage_dry_run_apply_and_duplicate(self):
        before = self.page.read_bytes()
        item = self.stage()
        self.assertEqual(item["status"], "pending")
        self.assertEqual(self.page.read_bytes(), before)
        self.assertTrue(self.stage()["duplicate"])
        self.assertTrue(m.apply(self.root, item["id"])["dry_run"])
        self.assertEqual(self.page.read_bytes(), before)
        self.assertEqual(m.apply(self.root, item["id"], True)["status"], "applied")
        after = self.page.read_bytes()
        self.assertTrue(m.apply(self.root, item["id"], True)["already_applied"])
        self.assertEqual(self.page.read_bytes(), after)
        self.assertNotIn("Prototype", json.dumps(m.pending(self.root)))
        self.assertNotIn("Prototype", (self.root / ".maintenance/queue.json").read_text())

    def test_crash_after_page_write_recovers_without_duplicate(self):
        item = self.stage()
        save = m._save
        def fail_receipt(path, value):
            if any(v.get("status") == "applied" for v in value.get("items", {}).values()):
                raise OSError("interrupted before receipt")
            return save(path, value)
        with patch.object(m, "_save", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                m.apply(self.root, item["id"], True)
        after = self.page.read_bytes()
        self.assertEqual(m.pending(self.root)["items"][0]["status"], "applying")
        self.assertTrue(m.apply(self.root, item["id"], True)["recovered"])
        self.assertEqual(self.page.read_bytes(), after)

    def test_target_changed_never_overwrites(self):
        item = self.stage()
        self.page.write_text("User independently edited this page.\n")
        with self.assertRaisesRegex(ValueError, "target changed"):
            m.apply(self.root, item["id"], True)
        self.assertEqual(self.page.read_text(), "User independently edited this page.\n")

    def test_revocation_and_scope_stop_pending_writes(self):
        item = self.stage()
        m.revoke(self.root)
        with self.assertRaisesRegex(ValueError, "revoked"):
            m.apply(self.root, item["id"], True)
        self.assertNotIn("Prototype", self.page.read_text())
        m.cancel(self.root, item["id"])
        self.assertNotIn("Prototype", (self.root / ".maintenance/queue.json").read_text())
        m.configure(self.root, ["project.md"], "user-turn-permission")
        other = self.root / "other.md"
        other.write_text("# Other")
        with self.assertRaisesRegex(ValueError, "scope"):
            m.stage(self.root, "other.md", "Not authorized", "turn-8")

    def test_safe_targets_and_generated_library_are_rejected(self):
        for target in ("../outside.md", "/outside.md", ".session-rebuild/doc.md"):
            with self.assertRaises(ValueError):
                m.stage(self.root, target, "No", "turn-8")
        link = self.root / "link.md"
        link.symlink_to(self.page)
        with self.assertRaisesRegex(ValueError, "symlink"):
            m.configure(self.root, ["link.md"], "turn-8")
        (self.root / "knowledge-index.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "generated"):
            self.stage()

    def test_redaction_happens_before_queue_persistence(self):
        secret = "sk-" + "inventedSensitiveValue" * 3
        result = m.stage(self.root, "project.md", "API configuration used " + secret, "turn-8")
        self.assertNotIn(secret, (self.root / ".maintenance/queue.json").read_text())
        m.apply(self.root, result["id"], True)
        self.assertNotIn(secret, self.page.read_text())

    def test_applied_body_changes_are_not_silently_restored(self):
        item = self.stage()
        m.apply(self.root, item["id"], True)
        self.page.write_text(self.page.read_text().replace("Prototype", "Corrected prototype"))
        with self.assertRaisesRegex(ValueError, "changed"):
            m.apply(self.root, item["id"], True)

    def test_cooperative_lock_blocks_mutation(self):
        with m.mutation_lock(self.root, "other-writer"):
            with self.assertRaisesRegex(ValueError, "another"):
                self.stage()

    def test_explicit_restage_after_new_authorization_or_cancel(self):
        original = self.stage()
        m.revoke(self.root)
        m.configure(self.root, ["project.md"], "new-user-permission")
        self.assertEqual(self.stage()["id"], original["id"])
        with self.assertRaisesRegex(ValueError, "authorization changed"):
            m.apply(self.root, original["id"], True)
        m.cancel(self.root, original["id"])
        self.assertEqual(self.stage()["status"], "cancelled")
        self.page.write_text("# User-reviewed current baseline\n")
        replacement = m.stage(self.root, "project.md", "Reviewed result", "turn-9", replaces=original["id"])
        self.assertNotEqual(replacement["id"], original["id"])
        self.assertEqual(m.stage(self.root, "project.md", "Reviewed result", "turn-9", replaces=original["id"])["id"], replacement["id"])
        with self.assertRaisesRegex(ValueError, "superseded"):
            m.apply(self.root, original["id"], True)
        m.apply(self.root, replacement["id"], True)
        with self.assertRaisesRegex(ValueError, "never applying/applied"):
            m.stage(self.root, "project.md", "Reviewed result", "turn-9", replaces=replacement["id"])
        self.assertEqual(self.page.read_text().count("Reviewed result"), 1)
        self.assertTrue(self.page.read_text().startswith("# User-reviewed current baseline"))

    def test_generated_and_repository_subdirectory_roots_are_rejected(self):
        for marker in ("knowledge-index.json", ".git", "SKILL.md"):
            with tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                (parent / marker).write_text("{}")
                nested = parent / "nested" / "pages"
                nested.mkdir(parents=True)
                (nested / "page.md").write_text("# Generated or source file")
                with self.assertRaisesRegex(ValueError, "generated"):
                    m.configure(nested, ["page.md"], "turn-9")


if __name__ == "__main__":
    unittest.main()
