from __future__ import annotations

import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent-session-knowledge-rebuilder" / "scripts"))

from session_kb.evolution_ops import (  # noqa: E402
    approval_queue,
    decide_rule_evolution,
    feedback_clusters,
    propose_rule_evolution,
    record_behavior_evaluation,
)
from session_kb.config import register_knowledge_base  # noqa: E402
from session_kb.verification import verify_retrieval_suite  # noqa: E402


def _load_reader():
    path = PROJECT_ROOT / "agent-knowledge-reader" / "scripts" / "read_knowledge.py"
    spec = importlib.util.spec_from_file_location("reader_v05", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("reader module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


READER = _load_reader()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _published_kb(root: Path) -> Path:
    knowledge = root / "knowledge"
    audit = root / "audit"
    (knowledge / "projects").mkdir(parents=True)
    audit.mkdir(parents=True)
    documents = [
        {"path": "00-evidence-rules.md", "title": "Evidence rules", "type": "evidence", "keywords": ["evidence"]},
        {"path": "01-identity.md", "title": "Identity", "type": "identity", "keywords": ["identity"]},
        {"path": "02-collaboration.md", "title": "Collaboration", "type": "collaboration", "keywords": ["collaboration"]},
        {
            "path": "projects/alpha.md",
            "title": "Alpha engine",
            "type": "project",
            "project_key": "project:alpha",
            "aliases": ["foundation engine"],
            "keywords": ["alpha", "engine", "foundation"],
        },
        {
            "path": "projects/beta.md",
            "title": "Beta board",
            "type": "project",
            "project_key": "project:beta",
            "aliases": ["beta dashboard"],
            "keywords": ["beta", "board", "dashboard"],
        },
    ]
    for item in documents:
        path = knowledge / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {item['title']}\n", encoding="utf-8")
    run_id = "run-suite-v05"
    _write_json(
        knowledge / "knowledge-index.json",
        {
            "index_version": 3,
            "semantic_status": "published",
            "run_id": run_id,
            "documents": documents,
            "graph": {"path": "knowledge-graph.json"},
        },
    )
    _write_json(
        knowledge / "knowledge-graph.json",
        {
            "graph_version": 1,
            "semantic_status": "published",
            "run_id": run_id,
            "nodes": [
                {
                    "id": f"doc:{item['path']}",
                    "kind": "document",
                    "path": item["path"],
                    "title": item["title"],
                    "document_type": item["type"],
                    **({"project_key": item["project_key"]} if item.get("project_key") else {}),
                }
                for item in documents
            ],
            "edges": [],
        },
    )
    _write_json(
        audit / "completion-report.json",
        {
            "report_version": 1,
            "run_id": run_id,
            "status": "needs_retrieval_verification",
            "gates": {
                "frozen_snapshot": True,
                "transport_accounted": True,
                "parse_clean": True,
                "discovery_coverage_complete": True,
                "unsupported_formats_clear": True,
                "semantic_review_complete": True,
                "knowledge_graph_complete": True,
                "published_knowledge": True,
            },
        },
    )
    return root


def _eval_set(*, wrong_expected: bool = False) -> dict:
    expected = ["project:beta"] if wrong_expected else ["project:alpha"]
    return {
        "eval_set_version": 1,
        "cases": [
            {"kind": "related", "task": "continue alpha engine", "expected_project_keys": expected},
            {"kind": "related", "task": "work on the foundation engine", "expected_project_keys": expected},
            {"kind": "hard_negative", "task": "quartz nebula calendar"},
            {"kind": "hard_negative", "task": "marble orchard recipe"},
        ],
    }


class RetrievalSuiteV05Tests(unittest.TestCase):
    def test_multiple_paraphrases_and_hard_negatives_unlock_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            raw_tasks = [item["task"] for item in _eval_set()["cases"]]
            report = verify_retrieval_suite(kb, _eval_set())
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["case_counts"], {"total": 4, "related": 2, "hard_negative": 2, "passed": 4, "failed": 0})
            serialized = json.dumps(report, ensure_ascii=False)
            for task in raw_tasks:
                self.assertNotIn(task, serialized)
            completion = json.loads((kb / "audit" / "completion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(completion["retrieval_contract_version"], 3)
            self.assertTrue(completion["gates"]["retrieval_related_suite"])
            self.assertTrue(completion["gates"]["retrieval_hard_negative_suite"])
            reader_result = READER.query(kb, "continue alpha engine", max_projects=1)
            self.assertEqual(reader_result["usage_receipt"]["task_sha256"], __import__("hashlib").sha256(b"continue alpha engine").hexdigest())
            self.assertEqual(reader_result["usage_receipt"]["selected_project_keys"], ["project:alpha"])

            completion["gates"]["retrieval_hard_negative_suite"] = False
            _write_json(kb / "audit" / "completion-report.json", completion)
            with self.assertRaisesRegex(ValueError, "suite gates"):
                READER.query(kb, "continue alpha engine", max_projects=1)
            with self.assertRaisesRegex(ValueError, "suite gates"):
                register_knowledge_base("suite-test", kb, Path(directory) / "registry.json")

    def test_missing_prerequisite_gate_cannot_be_repaired_by_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            completion_path = kb / "audit" / "completion-report.json"
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            completion["gates"].pop("transport_accounted")
            _write_json(completion_path, completion)
            with self.assertRaisesRegex(ValueError, "prerequisite gates.*transport_accounted"):
                verify_retrieval_suite(kb, _eval_set())
            unchanged = json.loads(completion_path.read_text(encoding="utf-8"))
            self.assertEqual(unchanged["status"], "needs_retrieval_verification")
            with self.assertRaisesRegex(ValueError, "prerequisite gates.*transport_accounted"):
                READER.query(kb, "continue alpha engine", max_projects=1)

    def test_unsupported_inputs_need_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            completion_path = kb / "audit" / "completion-report.json"
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            completion["gates"]["unsupported_formats_clear"] = False
            completion["gates"].pop("unsupported_formats_acknowledged", None)
            _write_json(completion_path, completion)
            with self.assertRaisesRegex(ValueError, "unsupported_formats_acknowledged"):
                verify_retrieval_suite(kb, _eval_set())

    def test_one_failed_expected_project_blocks_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            report = verify_retrieval_suite(kb, _eval_set(wrong_expected=True))
            self.assertEqual(report["status"], "failed")
            self.assertGreater(report["case_counts"]["failed"], 0)
            completion = json.loads((kb / "audit" / "completion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(completion["status"], "needs_retrieval_verification")

    def test_reader_and_registration_require_the_bound_suite_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kb = _published_kb(root / "kb")
            verify_retrieval_suite(kb, _eval_set())
            report_path = kb / "audit" / "retrieval-verification.json"
            original = report_path.read_bytes()
            report_path.unlink()
            with self.assertRaisesRegex(ValueError, "audit report is missing"):
                READER.query(kb, "continue alpha engine", max_projects=1)
            with self.assertRaisesRegex(ValueError, "audit report is missing"):
                register_knowledge_base("missing-audit", kb, root / "registry.json")

            report_path.write_bytes(original)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["cases"][0]["passed"] = False
            _write_json(report_path, report)
            with self.assertRaisesRegex(ValueError, "does not match"):
                READER.query(kb, "continue alpha engine", max_projects=1)
            with self.assertRaisesRegex(ValueError, "does not match"):
                register_knowledge_base("tampered-audit", kb, root / "registry.json")

    def test_suite_hash_binds_minimum_project_match_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            first = _eval_set()
            first["cases"][0]["min_project_matches"] = 0
            digest_zero = verify_retrieval_suite(kb, first)["suite_sha256"]
            second = _eval_set()
            second["cases"][0]["min_project_matches"] = 1
            digest_one = verify_retrieval_suite(kb, second)["suite_sha256"]
            self.assertNotEqual(digest_zero, digest_one)

    def test_cases_cannot_tune_query_parameters_to_make_negatives_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            attempted = _eval_set()
            attempted["cases"][2]["max_projects"] = 0
            with self.assertRaisesRegex(ValueError, "cannot override the suite retrieval profile"):
                verify_retrieval_suite(kb, attempted)

    def test_reader_defaults_to_the_profile_verified_for_the_whole_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            eval_set = _eval_set()
            eval_set["retrieval_profile"] = {"min_score": 5, "max_projects": 1, "max_related": 0}
            report = verify_retrieval_suite(kb, eval_set)
            self.assertEqual(report["retrieval_profile"], eval_set["retrieval_profile"])
            default_query = READER.query(kb, "continue alpha engine")
            self.assertEqual(default_query["usage_receipt"]["query_parameters"], eval_set["retrieval_profile"])
            self.assertTrue(default_query["usage_receipt"]["verified_profile_used"])
            overridden = READER.query(kb, "continue alpha engine", max_projects=2)
            self.assertFalse(overridden["usage_receipt"]["verified_profile_used"])

    def test_suite_requires_multiple_distinct_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            with self.assertRaisesRegex(ValueError, "at least 2"):
                verify_retrieval_suite(
                    kb,
                    {"cases": [{"kind": "related", "task": "alpha engine"}, {"kind": "hard_negative", "task": "quartz"}]},
                )

    def test_base_document_eval_does_not_require_a_project_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory) / "kb")
            report = verify_retrieval_suite(
                kb,
                {
                    "cases": [
                        {"kind": "related", "task": "who am I", "expected_document_types": ["identity"]},
                        {"kind": "related", "task": "review my current identity", "expected_document_types": ["identity"]},
                        {"kind": "hard_negative", "task": "quartz nebula calendar"},
                        {"kind": "hard_negative", "task": "marble orchard recipe"},
                    ]
                },
            )
            self.assertEqual(report["status"], "passed")
            related = [item for item in report["cases"] if item["kind"] == "related"]
            self.assertEqual([item["min_project_matches"] for item in related], [0, 0])


class EvolutionOperationsV05Tests(unittest.TestCase):
    def _behavior_events(self) -> list[dict]:
        return [
            {
                "event_id": "event-before",
                "logical_session_id": "logical-one",
                "sequence": 1,
                "timestamp": None,
            },
            {
                "event_id": "event-after-failed",
                "logical_session_id": "logical-one",
                "sequence": 2,
                "timestamp": None,
            },
            {
                "event_id": "event-after-passed",
                "logical_session_id": "logical-one",
                "sequence": 3,
                "timestamp": None,
            },
        ]

    def _review(self) -> dict:
        return {
            "feedback_signals": [
                {
                    "feedback_id": "feedback-one",
                    "project_key": "project:one",
                    "kind": "negative",
                    "object": "completion",
                    "scope": "global",
                    "statement": "Do not claim completion without observed delivery.",
                    "applies_to": "completion reporting",
                    "status": "observed",
                    "evidence_event_ids": ["event-feedback-one"],
                }
            ],
            "base_claims": [
                {
                    "claim_id": "claim-completion-rule",
                    "knowledge_type": "evidence_rule",
                    "status": "confirmed",
                    "statement": "Completion follows observed delivery evidence.",
                }
            ],
            "rule_evolutions": [],
        }

    def _proposal(self) -> dict:
        return {
            "feedback_ids": ["feedback-one"],
            "scope": "global",
            "rule_version": 1,
            "proposed_rule": "Require observed delivery evidence before reporting completion.",
            "rationale": "The cited feedback rejects unsupported completion claims.",
            "expected_behavior_change": "Later reports stop at the highest observed delivery level.",
        }

    def test_single_feedback_can_propose_but_cannot_silently_promote_global_rule(self) -> None:
        result = propose_rule_evolution(self._review(), self._proposal())
        evolution = result["evolution"]
        self.assertEqual(evolution["status"], "candidate")
        self.assertFalse(evolution["global_evidence_sufficient"])
        self.assertEqual(approval_queue(result["review"])["candidate_count"], 1)
        with self.assertRaisesRegex(ValueError, "global approval"):
            decide_rule_evolution(
                result["review"],
                evolution["evolution_id"],
                "approve",
                approval_event_ids=["event-approval"],
                promoted_claim_id="claim-completion-rule",
            )

    def test_explicit_approval_then_independent_behavior_evidence_validates(self) -> None:
        proposed = propose_rule_evolution(self._review(), self._proposal())
        evolution_id = proposed["evolution"]["evolution_id"]
        approved = decide_rule_evolution(
            proposed["review"],
            evolution_id,
            "approve",
            approval_event_ids=["event-approval"],
            promoted_claim_id="claim-completion-rule",
            explicit_global_approval=True,
        )
        failed = record_behavior_evaluation(
            approved,
            evolution_id,
            validation_result="failed",
            baseline_event_ids=["event-before"],
            validation_event_ids=["event-after-failed"],
            events=self._behavior_events(),
        )
        failed_item = failed["rule_evolutions"][0]
        self.assertEqual(failed_item["status"], "approved")
        self.assertEqual(failed_item["validation_result"], "failed")

        validated = record_behavior_evaluation(
            approved,
            evolution_id,
            validation_result="passed",
            baseline_event_ids=["event-before"],
            validation_event_ids=["event-after-passed"],
            events=self._behavior_events(),
            observed_behavior_change="The later report stopped at the observed delivery level.",
        )
        self.assertEqual(validated["rule_evolutions"][0]["status"], "validated")

    def test_behavior_evidence_rejects_reversed_same_session_order(self) -> None:
        proposed = propose_rule_evolution(self._review(), self._proposal())
        evolution_id = proposed["evolution"]["evolution_id"]
        approved = decide_rule_evolution(
            proposed["review"],
            evolution_id,
            "approve",
            approval_event_ids=["event-approval"],
            promoted_claim_id="claim-completion-rule",
            explicit_global_approval=True,
        )
        with self.assertRaisesRegex(ValueError, "not later"):
            record_behavior_evaluation(
                approved,
                evolution_id,
                validation_result="passed",
                baseline_event_ids=["event-after-passed"],
                validation_event_ids=["event-before"],
                events=self._behavior_events(),
                observed_behavior_change="This reversed comparison must be rejected.",
            )
        with self.assertRaisesRegex(ValueError, "not later"):
            record_behavior_evaluation(
                approved,
                evolution_id,
                validation_result="passed",
                baseline_event_ids=["event-before", "event-after-passed"],
                validation_event_ids=["event-after-failed"],
                events=self._behavior_events(),
                observed_behavior_change="After must be later than every baseline, not only the first one.",
            )

    def test_cross_session_behavior_order_requires_reliable_timestamps(self) -> None:
        proposed = propose_rule_evolution(self._review(), self._proposal())
        evolution_id = proposed["evolution"]["evolution_id"]
        approved = decide_rule_evolution(
            proposed["review"],
            evolution_id,
            "approve",
            approval_event_ids=["event-approval"],
            promoted_claim_id="claim-completion-rule",
            explicit_global_approval=True,
        )
        without_time = [
            {"event_id": "before", "logical_session_id": "one", "sequence": 9, "timestamp": None},
            {"event_id": "after", "logical_session_id": "two", "sequence": 1, "timestamp": None},
        ]
        with self.assertRaisesRegex(ValueError, "timezone-aware timestamps"):
            record_behavior_evaluation(
                approved,
                evolution_id,
                validation_result="passed",
                baseline_event_ids=["before"],
                validation_event_ids=["after"],
                events=without_time,
                observed_behavior_change="Cross-session order is not established.",
            )

        with_time = [
            {"event_id": "before", "logical_session_id": "one", "sequence": 9, "timestamp": "2026-08-23T09:00:00Z"},
            {"event_id": "after", "logical_session_id": "two", "sequence": 1, "timestamp": "2026-08-23T10:00:00+00:00"},
        ]
        validated = record_behavior_evaluation(
            approved,
            evolution_id,
            validation_result="passed",
            baseline_event_ids=["before"],
            validation_event_ids=["after"],
            events=with_time,
            observed_behavior_change="The later timestamp establishes the after observation.",
        )
        self.assertEqual(validated["rule_evolutions"][0]["status"], "validated")

    def test_feedback_clustering_is_exact_and_does_not_invent_semantic_merges(self) -> None:
        review = self._review()
        review["feedback_signals"].append(
            {
                **review["feedback_signals"][0],
                "feedback_id": "feedback-two",
                "project_key": "project:two",
                "evidence_event_ids": ["event-feedback-two"],
            }
        )
        clusters = feedback_clusters(review)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["distinct_project_count"], 2)
        self.assertTrue(clusters[0]["global_repetition_gate_met"])
        self.assertFalse(clusters[0]["semantic_merge_performed"])


if __name__ == "__main__":
    unittest.main()
