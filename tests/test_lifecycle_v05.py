from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent-session-knowledge-rebuilder" / "scripts"))

from session_kb.lifecycle import (  # noqa: E402
    LifecycleError,
    apply_lifecycle_plan,
    execute_lifecycle,
    list_due_revalidations,
    plan_lifecycle,
)
from session_kb.adapters import builtin_registry  # noqa: E402
from session_kb.discovery import discover, freeze_sources  # noqa: E402
from session_kb.pipeline import build_knowledge_base  # noqa: E402
import session_kb.lifecycle as lifecycle_module  # noqa: E402


def _load_reader():
    path = PROJECT_ROOT / "agent-knowledge-reader" / "scripts" / "read_knowledge.py"
    spec = importlib.util.spec_from_file_location("lifecycle_v05_reader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("reader module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


READER = _load_reader()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _fixture(base: Path, secret: str) -> tuple[Path, Path]:
    source = base / "original-agent-session.jsonl"
    source.write_text(json.dumps({"role": "user", "content": secret}) + "\n", encoding="utf-8")
    kb = base / "kb"
    audit = kb / "audit"
    review = kb / "review"
    knowledge = kb / "knowledge"
    (knowledge / "projects").mkdir(parents=True)
    audit.mkdir(parents=True)
    review.mkdir(parents=True)

    event = {
        "schema_version": "1.2",
        "event_id": "evt-sensitive",
        "source_agent": "fixture-agent",
        "adapter": "fixture-jsonl",
        "source_file_id": "source-one",
        "source_relpath": "private/session.jsonl",
        "session_id": "session-one",
        "logical_session_id": "session-one",
        "record_locator": "line:1",
        "sequence": 1,
        "role": "user",
        "actor_kind": "primary_user",
        "event_type": "message",
        "evidence_grade": "direct",
        "content": secret,
        "content_sha256": "f" * 64,
        "timestamp": "2026-01-01T00:00:00Z",
        "flags": [],
        "project_key": "project:sensitive",
        "project_label": "Sensitive launch",
        "metadata": {"copied_note": secret},
    }
    _jsonl(audit / "events.jsonl", [event])
    claim = {
        "claim_id": "claim-sensitive",
        "knowledge_type": "direction",
        "subject": "primary_user",
        "statement": f"Current direction includes {secret}",
        "status": "confirmed",
        "confidence": "high",
        "evidence_event_ids": ["evt-sensitive"],
        "observed_at": "2026-01-01T00:00:00+00:00",
        "applies_to": "current project",
    }
    feedback = {
        "feedback_id": "feedback-sensitive",
        "project_key": "project:sensitive",
        "kind": "outcome",
        "scope": "project",
        "statement": f"Delivered {secret}",
        "status": "observed",
        "evidence_event_ids": ["evt-sensitive"],
    }
    evolution = {
        "evolution_id": "evolution-sensitive",
        "status": "approved",
        "scope": "project",
        "rule_version": 1,
        "feedback_ids": ["feedback-sensitive"],
        "promoted_claim_id": "claim-sensitive",
        "proposed_rule": f"Remember {secret}",
        "validation_result": "pending",
    }
    _json(audit / "claims.json", [claim])
    _json(audit / "feedback-signals.json", [feedback])
    _json(audit / "rule-evolutions.json", [evolution])
    _json(
        audit / "relationships.json",
        [
            {
                "edge_id": "edge-sensitive",
                "source": "doc:projects/sensitive.md",
                "target": "claim:claim-sensitive",
                "status": "confirmed",
                "evidence_event_ids": ["evt-sensitive"],
            }
        ],
    )
    _jsonl(
        audit / "semantic-dispositions.jsonl",
        [{"event_id": "evt-sensitive", "project_key": "project:sensitive", "notes": secret}],
    )
    _json(
        audit / "completion-report.json",
        {
            "run_id": "run-one",
            "status": "complete",
            "gates": {
                "semantic_review_complete": True,
                "knowledge_graph_complete": True,
                "published_knowledge": True,
                "retrieval_related_match": True,
                "retrieval_unrelated_no_match": True,
            },
        },
    )
    _json(
        audit / "published-files.json",
        {
            "run_id": "run-one",
            "knowledge_paths": [
                "00-evidence-rules.md",
                "01-identity-and-current-direction.md",
                "02-collaboration-and-expression.md",
                "projects/sensitive.md",
                "knowledge-graph.json",
                "knowledge-index.json",
            ],
            "project_paths": ["projects/sensitive.md"],
        },
    )
    _json(audit / "retrieval-verification.json", {"status": "passed", "content_persisted": False})

    review_payload = {
        "review_version": 3,
        "run_id": "run-one",
        "base_claims": [claim],
        "feedback_signals": [feedback],
        "rule_evolutions": [evolution],
        "actor_attributions": [
            {
                "attribution_id": "actor-sensitive",
                "project_key": "project:sensitive",
                "evidence_event_ids": ["evt-sensitive"],
                "rationale": secret,
            }
        ],
        "project_relationships": [
            {
                "relationship_id": "rel-sensitive",
                "source_project_key": "project:sensitive",
                "target_project_key": "project:other",
                "status": "confirmed",
                "source_evidence_event_ids": ["evt-sensitive"],
                "target_evidence_event_ids": ["evt-other"],
                "rationale": secret,
            }
        ],
        "projects": [
            {
                "project_key": "project:sensitive",
                "title": "Sensitive launch",
                "aliases": [secret],
                "semantic_status": "reviewed",
                "rationale": secret,
                "event_exceptions": [],
                "history": {"objective": [{"text": secret, "evidence_event_ids": ["evt-sensitive"], "status": "observed"}]},
                "link_analysis": {"status": "linked", "rationale": secret},
                "completion": {"level": "artifact-created", "status": "observed", "evidence_event_ids": ["evt-sensitive"]},
            }
        ],
    }
    _json(review / "review.json", review_payload)

    base_docs = {
        "00-evidence-rules.md": "# Evidence rules\n\n## Related knowledge\n\n- stale link\n",
        "01-identity-and-current-direction.md": (
            "# Identity\n\n## claim-sensitive\n\n"
            f"Current direction includes {secret}\n\n- Evidence: `evt-sensitive`\n\n"
            "## Related knowledge\n\n- [Sensitive](projects/sensitive.md)\n"
        ),
        "02-collaboration-and-expression.md": f"# Collaboration\n\nRemember {secret}\n\n## Related knowledge\n\n- stale link\n",
        "projects/sensitive.md": f"# Sensitive launch\n\n{secret}\n\n## Related knowledge\n\n- stale link\n",
    }
    for relative, text in base_docs.items():
        path = knowledge / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    archive = knowledge / "archive" / "old" / "sensitive.md"
    archive.parent.mkdir(parents=True)
    archive.write_text(f"# Old copy\n\n{secret}\n", encoding="utf-8")

    documents = [
        {"path": "00-evidence-rules.md", "title": "Evidence rules", "type": "evidence", "keywords": ["evidence"]},
        {"path": "01-identity-and-current-direction.md", "title": "Identity", "type": "identity", "keywords": ["identity"]},
        {"path": "02-collaboration-and-expression.md", "title": "Collaboration", "type": "collaboration", "keywords": ["collaboration"]},
        {
            "path": "projects/sensitive.md",
            "title": "Sensitive launch",
            "type": "project",
            "project_key": "project:sensitive",
            "keywords": ["sensitive"],
        },
    ]
    _json(
        knowledge / "knowledge-index.json",
        {
            "index_version": 4,
            "semantic_status": "published",
            "run_id": "run-one",
            "documents": documents,
            "graph": {"path": "knowledge-graph.json", "nodes": 5, "edges": 2},
        },
    )
    _json(
        knowledge / "knowledge-graph.json",
        {
            "graph_version": 2,
            "semantic_status": "published",
            "run_id": "run-one",
            "nodes": [
                {"id": "doc:00-evidence-rules.md", "kind": "document", "path": "00-evidence-rules.md"},
                {"id": "doc:01-identity-and-current-direction.md", "kind": "document", "path": "01-identity-and-current-direction.md"},
                {"id": "doc:02-collaboration-and-expression.md", "kind": "document", "path": "02-collaboration-and-expression.md"},
                {
                    "id": "doc:projects/sensitive.md",
                    "kind": "document",
                    "path": "projects/sensitive.md",
                    "project_key": "project:sensitive",
                    "title": secret,
                },
                {
                    "id": "claim:claim-sensitive",
                    "kind": "claim",
                    "claim_id": "claim-sensitive",
                    "document_path": "01-identity-and-current-direction.md",
                    "title": secret,
                },
            ],
            "edges": [
                {
                    "edge_id": "edge-sensitive",
                    "source": "doc:projects/sensitive.md",
                    "target": "claim:claim-sensitive",
                    "status": "confirmed",
                    "evidence_event_ids": ["evt-sensitive"],
                },
                {
                    "edge_id": "edge-unrelated",
                    "source": "doc:00-evidence-rules.md",
                    "target": "doc:01-identity-and-current-direction.md",
                    "status": "confirmed",
                    "evidence_event_ids": ["evt-other"],
                },
            ],
        },
    )
    return kb, source


class LifecycleV05Tests(unittest.TestCase):
    def test_lifecycle_discards_derived_units_and_stale_state_pointers(self) -> None:
        for action in ("forget", "retract"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                body = "a removable reviewed conclusion"
                kb, _ = _fixture(Path(directory), body)
                index_path = kb / "knowledge/knowledge-index.json"
                index = json.loads(index_path.read_text())
                for document in index["documents"]:
                    document["context_units"] = [{"unit_id": "claim-sensitive", "text": body}]
                    document["current_state"] = {"goal": "claim-sensitive"}
                _json(index_path, index)
                plan = plan_lifecycle(kb, action, claim_ids=["claim-sensitive"])
                apply_lifecycle_plan(kb, plan, commit=True)
                after = json.loads(index_path.read_text())
                self.assertEqual(after["semantic_status"], "lifecycle-pending-redistill")
                self.assertTrue(all("context_units" not in d and "current_state" not in d for d in after["documents"]))
                with self.assertRaises(ValueError):
                    READER.query(kb, "sensitive", view="facts", emit_content=True)

    def test_forget_tombstone_survives_incremental_reparse_of_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source_root.mkdir()
            source = source_root / "rollout.jsonl"
            records = [
                {"type": "session_meta", "payload": {"id": "forget-rebuild", "cwd": "/synthetic/forget"}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "Original removable sentence."}},
            ]
            _jsonl(source, records)
            output = root / "kb"
            registry = builtin_registry()

            discovery = discover(registry, roots=[source_root], output_dir=output)
            snapshot = freeze_sources(discovery.sources)
            snapshot["discovery"] = discovery.to_dict()
            build_knowledge_base(registry, discovery.sources, output, discovery=discovery, snapshot=snapshot)
            event_id = json.loads((output / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])["event_id"]
            execute_lifecycle(output, "forget", event_ids=[event_id], dry_run=False)

            records[-1]["payload"]["message"] = "Changed removable sentence after rebuilding."
            _jsonl(source, records)
            discovery = discover(registry, roots=[source_root], output_dir=output)
            snapshot = freeze_sources(discovery.sources)
            snapshot["discovery"] = discovery.to_dict()
            build_knowledge_base(
                registry,
                discovery.sources,
                output,
                discovery=discovery,
                snapshot=snapshot,
                incremental=True,
            )
            rebuilt_events = (output / "audit" / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("Original removable sentence", rebuilt_events)
            self.assertNotIn("Changed removable sentence", rebuilt_events)
            tombstone = json.loads((output / "audit" / "lifecycle-tombstones.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertTrue(tombstone["affected_identifier_sha256"]["event_locators"])

    def test_event_forget_does_not_expand_to_every_event_in_its_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source_root.mkdir()
            source = source_root / "rollout.jsonl"
            records = [
                {"type": "session_meta", "payload": {"id": "forget-one-event", "cwd": "/synthetic/shared-project"}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "Remove only this sentence."}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "Keep this sibling sentence."}},
            ]
            _jsonl(source, records)
            output = root / "kb"
            registry = builtin_registry()
            discovery = discover(registry, roots=[source_root], output_dir=output)
            snapshot = freeze_sources(discovery.sources)
            snapshot["discovery"] = discovery.to_dict()
            build_knowledge_base(registry, discovery.sources, output, discovery=discovery, snapshot=snapshot)
            rows = [json.loads(line) for line in (output / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            removed_id = next(row["event_id"] for row in rows if "Remove only" in row["content"])
            execute_lifecycle(output, "forget", event_ids=[removed_id], dry_run=False)

            discovery = discover(registry, roots=[source_root], output_dir=output)
            snapshot = freeze_sources(discovery.sources)
            snapshot["discovery"] = discovery.to_dict()
            build_knowledge_base(registry, discovery.sources, output, discovery=discovery, snapshot=snapshot, incremental=True)
            rebuilt = (output / "audit" / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("Remove only this sentence", rebuilt)
            self.assertIn("Keep this sibling sentence", rebuilt)
            tombstone = json.loads((output / "audit" / "lifecycle-tombstones.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(tombstone["affected_identifier_sha256"]["projects"], [])

    def test_dry_run_is_default_and_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb, source = _fixture(Path(directory), "private-value-for-dry-run")
            before = _tree_bytes(kb)
            source_before = source.read_bytes()

            result = execute_lifecycle(kb, "forget", event_ids=["evt-sensitive"])

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["action"], "forget")
            self.assertTrue(result["impact"]["requires_redistill"])
            self.assertTrue(result["impact"]["requires_retrieval_gates"])
            self.assertEqual(_tree_bytes(kb), before)
            self.assertEqual(source.read_bytes(), source_before)

    def test_forget_commit_removes_generated_body_and_indexed_project_not_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            forgotten_body = "forget-this-private-body" + chr(64) + "example" + ".invalid"
            kb, source = _fixture(Path(directory), forgotten_body)
            source_before = source.read_bytes()
            plan = plan_lifecycle(kb, "forget", source_file_ids=["source-one"], reason=forgotten_body)

            result = apply_lifecycle_plan(kb, plan, commit=True)

            self.assertEqual(result["status"], "applied")
            self.assertFalse(result["source_sessions_mutated"])
            self.assertFalse(result["backup_erasure_guaranteed"])
            self.assertEqual(source.read_bytes(), source_before)
            index = json.loads((kb / "knowledge" / "knowledge-index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["semantic_status"], "lifecycle-pending-redistill")
            self.assertNotIn("projects/sensitive.md", {item["path"] for item in index["documents"]})
            self.assertFalse((kb / "knowledge" / "projects" / "sensitive.md").exists())
            completion = json.loads((kb / "audit" / "completion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(completion["status"], "needs_redistill")
            self.assertFalse(completion["gates"]["retrieval_related_match"])
            remaining_events = (kb / "audit" / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("evt-sensitive", remaining_events)
            self.assertNotIn("session-one", remaining_events)
            for relative, payload in _tree_bytes(kb).items():
                self.assertNotIn(forgotten_body.encode(), payload, relative)

            tombstones = [
                json.loads(line)
                for line in (kb / "audit" / "lifecycle-tombstones.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            serialized = json.dumps(tombstones[-1], ensure_ascii=False)
            self.assertEqual(tombstones[-1]["action"], "forget")
            self.assertNotIn(forgotten_body, serialized)
            self.assertNotIn("source-one", serialized)
            self.assertNotIn("evt-sensitive", serialized)
            self.assertIn("affected_identifier_sha256", tombstones[-1])

    def test_retract_keeps_provenance_but_deactivates_claim_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb, _ = _fixture(Path(directory), "retractable statement body")
            result = execute_lifecycle(kb, "retract", claim_ids="claim-sensitive", dry_run=False)

            self.assertEqual(result["status"], "applied")
            claims = json.loads((kb / "audit" / "claims.json").read_text(encoding="utf-8"))
            self.assertEqual(claims[0]["status"], "retracted")
            self.assertEqual(claims[0]["evidence_event_ids"], ["evt-sensitive"])
            identity = (kb / "knowledge" / "01-identity-and-current-direction.md").read_text(encoding="utf-8")
            self.assertNotIn("## claim-sensitive", identity)
            graph = json.loads((kb / "knowledge" / "knowledge-graph.json").read_text(encoding="utf-8"))
            self.assertNotIn("claim:claim-sensitive", {node["id"] for node in graph["nodes"]})
            tombstone = json.loads((kb / "audit" / "lifecycle-tombstones.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(tombstone["selectors"]["claim_ids"], ["claim-sensitive"])
            self.assertEqual(tombstone["affected"]["claims"], ["claim-sensitive"])

    def test_schedule_and_due_revalidation_include_no_claim_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private_body = "body-must-not-appear-in-due-report"
            kb, _ = _fixture(Path(directory), private_body)
            execute_lifecycle(
                kb,
                "schedule",
                claim_ids=["claim-sensitive"],
                observed_at="2026-01-01",
                checked_at="2026-02-01T00:00:00Z",
                recheck_after="2026-03-01T00:00:00Z",
                retain_until="2026-04-01T00:00:00Z",
                dry_run=False,
            )

            early = list_due_revalidations(kb, as_of="2026-02-15T00:00:00Z")
            self.assertEqual(early["due_count"], 0)
            due = list_due_revalidations(kb, as_of="2026-03-15T00:00:00Z")
            self.assertEqual(due["due_count"], 1)
            self.assertEqual(due["due"][0]["due_reasons"], ["recheck_due"])
            self.assertEqual(due["due"][0]["checked_at"], "2026-02-01T00:00:00+00:00")
            self.assertNotIn(private_body, json.dumps(due, ensure_ascii=False))
            expired = list_due_revalidations(kb, as_of="2026-04-02T00:00:00Z")
            self.assertEqual(expired["due"][0]["due_reasons"], ["recheck_due", "retention_expired"])

    def test_plan_reports_all_scoped_impacts_and_unmatched_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb, _ = _fixture(Path(directory), "impact report body")
            plan = plan_lifecycle(
                kb,
                "forget",
                event_ids=["evt-sensitive", "evt-does-not-exist"],
            )
            self.assertEqual(plan["unmatched_selectors"]["event_ids"], ["evt-does-not-exist"])
            self.assertEqual(plan["impact"]["claims"], ["claim-sensitive"])
            self.assertEqual(plan["impact"]["rules"], ["evolution-sensitive"])
            self.assertEqual(plan["impact"]["projects"], ["project:sensitive"])
            self.assertIn("edge-sensitive", plan["impact"]["edges"])
            self.assertIn("projects/sensitive.md", plan["impact"]["documents"])

    def test_changed_state_refuses_stale_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb, _ = _fixture(Path(directory), "stale plan body")
            plan = plan_lifecycle(kb, "retract", claim_ids=["claim-sensitive"])
            (kb / "knowledge" / "00-evidence-rules.md").write_text("# changed\n", encoding="utf-8")
            with self.assertRaisesRegex(LifecycleError, "changed after planning"):
                apply_lifecycle_plan(kb, plan, commit=True)

    def test_interrupted_commit_is_reader_fail_closed_and_same_plan_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            forgotten_body = "transaction-private-body-that-must-disappear"
            kb, source = _fixture(Path(directory), forgotten_body)
            source_before = source.read_bytes()
            index_path = kb / "knowledge" / "knowledge-index.json"
            completion_path = kb / "audit" / "completion-report.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            for gate in READER.PUBLICATION_PREREQUISITE_GATES:
                completion["gates"][gate] = True
            completion["gates"]["unsupported_formats_clear"] = True
            completion["publication_manifest_sha256"] = READER.publication_manifest((kb / "knowledge").resolve(), index)
            _json(completion_path, completion)
            self.assertEqual(READER.query(kb, "Sensitive launch", min_score=1)["semantic_status"], "published")

            plan = plan_lifecycle(kb, "forget", event_ids=["evt-sensitive"])
            original_atomic = lifecycle_module._atomic_bytes
            live_write_count = 0
            resolved_kb = kb.resolve()

            def fail_fourth_live_write(path: Path, content: bytes) -> None:
                nonlocal live_write_count
                relative = path.relative_to(resolved_kb).as_posix()
                is_private_control = relative == "audit/lifecycle-transaction.json" or relative.startswith(
                    "audit/lifecycle-staging/"
                )
                if not is_private_control:
                    live_write_count += 1
                    if live_write_count == 4:
                        raise OSError("injected fourth atomic target-write failure")
                original_atomic(path, content)

            with patch("session_kb.lifecycle._atomic_bytes", side_effect=fail_fourth_live_write):
                with self.assertRaisesRegex(OSError, "fourth atomic"):
                    apply_lifecycle_plan(kb, plan, commit=True)

            interrupted = json.loads(completion_path.read_text(encoding="utf-8"))
            self.assertEqual(interrupted["status"], "lifecycle-applying")
            self.assertTrue(interrupted["lifecycle_transaction_pending"])
            self.assertFalse(interrupted["gates"]["published_knowledge"])
            self.assertNotIn("retrieval_profile", interrupted)
            with self.assertRaisesRegex(ValueError, "transaction is pending|gates are incomplete|status is not final"):
                READER.query(kb, "Sensitive launch", min_score=1)

            transaction_path = kb / "audit" / "lifecycle-transaction.json"
            staging_root = kb / "audit" / "lifecycle-staging"
            self.assertTrue(transaction_path.is_file())
            self.assertTrue(staging_root.is_dir())
            self.assertNotIn(forgotten_body.encode(), transaction_path.read_bytes())
            for staged in staging_root.rglob("*.stage"):
                self.assertNotIn(forgotten_body.encode(), staged.read_bytes(), staged.name)
            tombstones = kb / "audit" / "lifecycle-tombstones.jsonl"
            self.assertTrue(tombstones.is_file())
            self.assertNotIn(forgotten_body.encode(), tombstones.read_bytes())
            self.assertEqual(source.read_bytes(), source_before)

            recovered = apply_lifecycle_plan(kb, plan, commit=True)

            self.assertEqual(recovered["status"], "applied")
            self.assertTrue(recovered["transaction_recovered"])
            self.assertFalse(transaction_path.exists())
            self.assertFalse(staging_root.exists())
            self.assertTrue(tombstones.is_file())
            self.assertEqual(source.read_bytes(), source_before)
            for relative, payload in _tree_bytes(kb).items():
                self.assertNotIn(forgotten_body.encode(), payload, relative)

    def test_path_escape_and_symlink_targets_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            kb, _ = _fixture(base, "path safety body")
            outside = base / "outside.json"
            outside.write_text("[]\n", encoding="utf-8")
            claims = kb / "audit" / "claims.json"
            claims.unlink()
            claims.symlink_to(outside)
            with self.assertRaisesRegex(LifecycleError, "symlink"):
                plan_lifecycle(kb, "retract", claim_ids=["claim-sensitive"])
            self.assertEqual(outside.read_text(encoding="utf-8"), "[]\n")

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            kb, _ = _fixture(base, "index escape body")
            index_path = kb / "knowledge" / "knowledge-index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["documents"][0]["path"] = "../outside.md"
            _json(index_path, index)
            with self.assertRaisesRegex(LifecycleError, "unsafe generated path|escapes"):
                plan_lifecycle(kb, "retract", claim_ids=["claim-sensitive"])


if __name__ == "__main__":
    unittest.main()
