from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent-session-knowledge-rebuilder" / "scripts"))

from session_kb.query import query_knowledge  # noqa: E402
from session_kb.verification import verify_retrieval  # noqa: E402


def _load_reader():
    path = PROJECT_ROOT / "agent-knowledge-reader" / "scripts" / "read_knowledge.py"
    spec = importlib.util.spec_from_file_location("retrieval_gate_reader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("reader module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


READER = _load_reader()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _published_kb(root: Path, *, run_id: str = "run-retrieval-1") -> Path:
    knowledge = root / "knowledge"
    audit = root / "audit"
    (knowledge / "projects").mkdir(parents=True)
    audit.mkdir(parents=True)
    for relative, content in {
        "00-evidence-rules.md": "# Evidence rules\n",
        "01-identity-and-current-direction.md": "# Identity\n",
        "02-collaboration-and-expression.md": "# Collaboration\n",
        "projects/alpha.md": "# Alpha engine\n",
        "projects/beta.md": "# Beta dashboard\n",
    }.items():
        (knowledge / relative).write_text(content, encoding="utf-8")
    documents = [
        {"path": "00-evidence-rules.md", "title": "Evidence rules", "type": "evidence", "keywords": ["evidence"]},
        {"path": "01-identity-and-current-direction.md", "title": "Identity", "type": "identity", "keywords": ["identity"]},
        {"path": "02-collaboration-and-expression.md", "title": "Collaboration", "type": "collaboration", "keywords": ["collaboration"]},
        {
            "path": "projects/alpha.md",
            "title": "Alpha engine",
            "type": "project",
            "project_key": "project:alpha",
            "aliases": ["foundation engine"],
            "keywords": ["alpha", "engine", "continue"],
        },
        {
            "path": "projects/beta.md",
            "title": "Beta dashboard",
            "type": "project",
            "project_key": "project:beta",
            "aliases": [],
            "keywords": ["beta", "dashboard"],
        },
    ]
    _write_json(
        knowledge / "knowledge-index.json",
        {"index_version": 3, "semantic_status": "published", "run_id": run_id, "documents": documents},
    )
    _write_json(
        knowledge / "knowledge-graph.json",
        {
            "graph_version": 1,
            "semantic_status": "published",
            "run_id": run_id,
            "nodes": [
                {
                    "id": "doc:00-evidence-rules.md",
                    "kind": "document",
                    "path": "00-evidence-rules.md",
                    "title": "Evidence rules",
                    "document_type": "evidence",
                },
                {
                    "id": "doc:01-identity-and-current-direction.md",
                    "kind": "document",
                    "path": "01-identity-and-current-direction.md",
                    "title": "Identity",
                    "document_type": "identity",
                },
                {
                    "id": "doc:02-collaboration-and-expression.md",
                    "kind": "document",
                    "path": "02-collaboration-and-expression.md",
                    "title": "Collaboration",
                    "document_type": "collaboration",
                },
                {
                    "id": "doc:projects/alpha.md",
                    "kind": "document",
                    "path": "projects/alpha.md",
                    "title": "Alpha engine",
                    "document_type": "project",
                    "project_key": "project:alpha",
                },
                {
                    "id": "doc:projects/beta.md",
                    "kind": "document",
                    "path": "projects/beta.md",
                    "title": "Beta dashboard",
                    "document_type": "project",
                    "project_key": "project:beta",
                },
            ],
            "edges": [
                {
                    "edge_id": "edge-alpha-beta",
                    "source": "doc:projects/alpha.md",
                    "target": "doc:projects/beta.md",
                    "relation": "continued-as",
                    "direction": "directed",
                    "status": "confirmed",
                    "confidence": "high",
                    "evidence_basis": ["explicit-user-intent", "shared-artifact"],
                    "evidence_event_ids": ["evt-alpha", "evt-beta"],
                }
            ],
        },
    )
    _write_json(
        audit / "completion-report.json",
        {
            "report_version": 1,
            "run_id": run_id,
            "status": "needs_retrieval_verification",
            "gates": {
                "unsupported_formats_clear": True,
                "semantic_review_complete": True,
                "knowledge_graph_complete": True,
                "published_knowledge": True,
                "retrieval_related_match": False,
                "retrieval_unrelated_no_match": False,
            },
        },
    )
    return root


class RetrievalGateTests(unittest.TestCase):
    def test_graph_document_nodes_must_stay_in_index_and_out_of_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            stale = kb / "knowledge" / "archive" / "old" / "stale.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("# stale private knowledge\n", encoding="utf-8")
            graph_path = kb / "knowledge" / "knowledge-graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["nodes"].append(
                {
                    "id": "doc:archive/old/stale.md",
                    "kind": "document",
                    "path": "archive/old/stale.md",
                    "title": "Stale",
                    "document_type": "project",
                }
            )
            graph["edges"].append(
                {
                    "edge_id": "edge-alpha-stale",
                    "source": "doc:projects/alpha.md",
                    "target": "doc:archive/old/stale.md",
                    "relation": "unsafe-stale-link",
                    "direction": "directed",
                    "status": "confirmed",
                }
            )
            _write_json(graph_path, graph)

            with self.assertRaisesRegex(ValueError, "archived knowledge|not in the published index"):
                verify_retrieval(kb, "alpha engine", "quartz nebula", expected_project_key="project:alpha")

    def test_query_and_manifest_use_the_same_indexed_graph_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            knowledge = kb / "knowledge"
            default_graph = json.loads((knowledge / "knowledge-graph.json").read_text(encoding="utf-8"))
            _write_json(knowledge / "indexed-graph.json", default_graph)
            index = json.loads((knowledge / "knowledge-index.json").read_text(encoding="utf-8"))
            index["graph"] = {"path": "indexed-graph.json"}
            _write_json(knowledge / "knowledge-index.json", index)
            default_graph["run_id"] = "tampered-unused-run"
            _write_json(knowledge / "knowledge-graph.json", default_graph)

            self.assertEqual(
                verify_retrieval(kb, "alpha engine", "quartz nebula", expected_project_key="project:alpha")["status"],
                "passed",
            )
            result = READER.query(kb, "alpha engine", emit_content=False)
            relationship = next(item for item in result["documents"] if item.get("relationship"))
            self.assertEqual(relationship["edge_relation"], "continued-as")

    def test_verification_is_content_free_and_unlocks_reader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            private_email = "person" + "@example.org"
            sensitive_assignment = "password" + "=" + "do-not-persist"
            related_task = f"continue alpha engine for {private_email}"
            unrelated_task = f"quartz nebula {sensitive_assignment}"
            result = verify_retrieval(kb, related_task, unrelated_task, expected_project_key="project:alpha")
            self.assertEqual(result["status"], "passed")
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(related_task, serialized)
            self.assertNotIn(unrelated_task, serialized)
            self.assertNotIn(private_email, serialized)
            self.assertNotIn(sensitive_assignment, serialized)
            self.assertFalse(result["content_persisted"])

            audit = json.loads((kb / "audit" / "retrieval-verification.json").read_text(encoding="utf-8"))
            self.assertEqual(audit, result)
            completion = json.loads((kb / "audit" / "completion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(completion["status"], "complete")
            self.assertTrue(completion["gates"]["retrieval_related_match"])
            self.assertTrue(completion["gates"]["retrieval_unrelated_no_match"])

            rebuilder_result = query_knowledge(kb, "continue alpha engine", max_projects=1, max_related=1)
            alpha = next(item for item in rebuilder_result["documents"] if item.get("project_key") == "project:alpha")
            beta = next(item for item in rebuilder_result["documents"] if item.get("project_key") == "project:beta")
            self.assertEqual(alpha["project_key"], "project:alpha")
            self.assertEqual(beta["edge_id"], "edge-alpha-beta")
            self.assertEqual(beta["confidence"], "high")
            self.assertEqual(beta["evidence_basis"], ["explicit-user-intent", "shared-artifact"])
            self.assertEqual(beta["evidence_event_ids"], ["evt-alpha", "evt-beta"])

            reader_result = READER.query(kb, "continue alpha engine", max_projects=1, max_related=1)
            self.assertEqual(reader_result["run_id"], "run-retrieval-1")
            related = next(item for item in reader_result["documents"] if item.get("edge_id") == "edge-alpha-beta")
            self.assertEqual(related["project_key"], "project:beta")
            self.assertEqual(related["confidence"], "high")
            self.assertEqual(related["evidence_event_ids"], ["evt-alpha", "evt-beta"])

            reverse_result = READER.query(kb, "beta dashboard", max_projects=1, max_related=1)
            reverse = next(item for item in reverse_result["documents"] if item.get("edge_id") == "edge-alpha-beta")
            self.assertEqual(reverse["project_key"], "project:alpha")
            self.assertEqual(reverse["traversal"], "reverse")
            self.assertEqual(reverse["relationship"], "reverse of continued-as")
            self.assertEqual(reverse["edge_relation"], "continued-as")
            self.assertEqual(reverse["edge_source_path"], "projects/alpha.md")
            self.assertEqual(reverse["edge_target_path"], "projects/beta.md")

    def test_failed_verification_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            result = verify_retrieval(kb, "quartz nebula", "alpha engine", expected_project_key="project:alpha")
            self.assertEqual(result["status"], "failed")
            completion = json.loads((kb / "audit" / "completion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(completion["status"], "needs_retrieval_verification")
            self.assertFalse(completion["gates"]["retrieval_related_match"])
            self.assertFalse(completion["gates"]["retrieval_unrelated_no_match"])
            with self.assertRaisesRegex(ValueError, "retrieval verification gates"):
                READER.query(kb, "alpha engine")

    def test_reader_rejects_legacy_and_run_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            with self.assertRaisesRegex(ValueError, "legacy-unverified"):
                READER.query(kb, "alpha engine")
            verify_retrieval(kb, "alpha engine", "quartz nebula", expected_project_key="project:alpha")
            completion_path = kb / "audit" / "completion-report.json"
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            completion["run_id"] = "different-run"
            _write_json(completion_path, completion)
            with self.assertRaisesRegex(ValueError, "run_id"):
                READER.query(kb, "alpha engine")


if __name__ == "__main__":
    unittest.main()
