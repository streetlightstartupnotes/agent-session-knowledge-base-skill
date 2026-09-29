from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from hashlib import sha256
from unittest.mock import patch

from test_retrieval_gate import READER
from test_retrieval_evolution_v05 import _published_kb, _eval_set
from test_project_chain_cache_v05 import make_event
from session_kb.context_units import RETRIEVAL_ENGINE_VERSION, selection_options, unit_view, validate_units
from session_kb.query import query_knowledge
from session_kb.review import create_review_template, create_review_packet, validate_review, distill_review, project_synthesis_sha256
from session_kb.verification import verify_retrieval_suite, _normalize_eval_cases, _suite_case_summary


def add_units(kb):
    path = kb / "knowledge/knowledge-index.json"
    index = json.loads(path.read_text())
    item = next(d for d in index["documents"] if d.get("project_key") == "project:alpha")
    item["context_units"] = [
        {"unit_id": "history:objective:0", "section": "objective", "text": "Build the alpha engine.",
         "status": "observed", "evidence_event_ids": ["evt-request"], "applies_to": "Alpha only"},
        {"unit_id": "history:delivery_and_state:0", "section": "delivery_and_state",
         "text": "The engine build passed; field operation remains unverified.", "status": "agent-reported",
         "evidence_event_ids": ["evt-report"], "applies_to": "bench build"},
        {"unit_id": "history:changes_and_corrections:0", "section": "changes_and_corrections",
         "text": "Use two retries, retaining the original timeout.", "before": "five retries", "after": "two retries",
         "status": "observed", "evidence_event_ids": ["evt-correction"], "applies_to": "network component"},
    ]
    item["current_state"] = {"goal": "history:objective:0", "delivery": "history:delivery_and_state:0",
                             "latest_correction": "history:changes_and_corrections:0"}
    path.write_text(json.dumps(index))
    return item


class ContextUnitTests(unittest.TestCase):
    def test_fact_view_does_not_emit_project_body_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            add_units(kb)
            suite = _eval_set()
            suite["selection_options"] = {"base_context": "none", "view": "facts", "max_facts": 1}
            report = verify_retrieval_suite(kb, suite)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["retrieval_engine_version"], RETRIEVAL_ENGINE_VERSION)
            result = READER.query(kb, "alpha retries", emit_content=True)
            project = next(d for d in result["documents"] if d["type"] == "project")
            self.assertNotIn("content", project)
            self.assertEqual(project["units"][0]["after"], "two retries")
            self.assertEqual(project["units"][0]["evidence_event_ids"], ["evt-correction"])
            self.assertEqual(project["omitted_count"], 2)
            self.assertTrue(result["usage_receipt"]["verified_profile_used"])
            self.assertNotIn("two retries", json.dumps(report))
            self.assertFalse(READER.query(kb, "alpha", view="documents")["usage_receipt"]["verified_profile_used"])

    def test_current_state_is_explicit_not_inferred_from_latest_array_position(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            add_units(kb)
            suite = _eval_set()
            suite["selection_options"] = {"base_context": "none", "view": "current"}
            self.assertEqual(verify_retrieval_suite(kb, suite)["status"], "passed")
            for reader in (READER.query, query_knowledge):
                result = reader(kb, "alpha engine", view="current", emit_content=True)
                project = next(d for d in result["documents"] if d["type"] == "project")
                self.assertEqual({u["state_field"] for u in project["units"]}, {"goal", "delivery", "latest_correction"})
                self.assertIn("accepted_baseline", project["missing_state_fields"])
                delivery = next(u for u in project["units"] if u["state_field"] == "delivery")
                self.assertEqual(delivery["status"], "agent-reported")

    def test_old_publication_reports_unavailable_without_fabricating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            verify_retrieval_suite(kb, _eval_set())
            result = READER.query(kb, "alpha", view="current", emit_content=True)
            project = next(d for d in result["documents"] if d["type"] == "project")
            self.assertEqual(project["context_status"], "unavailable")
            self.assertEqual(project["units"], [])

    def test_fact_suite_requires_actual_context_not_just_document_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            suite = _eval_set()
            suite["selection_options"] = {"view": "facts"}
            self.assertEqual(verify_retrieval_suite(kb, suite)["status"], "failed")
            with self.assertRaises(ValueError):
                READER.query(kb, "alpha")

    def test_related_fact_cannot_replace_missing_target_units_in_evaluation(self):
        for view in ("facts", "current"):
            suite = _eval_set()
            suite["selection_options"] = {"view": view}
            case = _normalize_eval_cases(suite)[0][0]
            result = {"match_status": "matched", "project_matches": 1, "documents": [
                {"type": "project", "project_key": "project:alpha", "units": []},
                {"type": "project", "project_key": "project:beta", "relationship": "related", "units": [{}]}]}
            self.assertFalse(_suite_case_summary(case, result)["passed"])
            case["expected_project_keys"] = []
            self.assertFalse(_suite_case_summary(case, result)["passed"])
            case["expected_document_types"] = ["collaboration"]
            self.assertFalse(_suite_case_summary(case, result)["passed"])
            result["documents"].append({"type": "collaboration", "units": [{}]})
            self.assertTrue(_suite_case_summary(case, result)["passed"])

    def test_budget_omits_whole_units_and_marks_unexpanded_conflicts(self):
        document = {"context_units": [{"unit_id": "claim-one", "text": "retry " * 300,
            "status": "disputed", "evidence_event_ids": ["evt-1"], "conflicts": ["claim-two"]}]}
        result = unit_view(document, {"retry"}, selection_options({"view": "facts", "max_chars": 256}))
        self.assertEqual(result["units"], [])
        self.assertEqual(result["omitted_count"], 1)
        result = unit_view(document, {"retry"}, selection_options({"view": "facts"}))
        self.assertEqual(result["unexpanded_claim_ids"], ["claim-two"])
        self.assertEqual(result["units"][0]["status"], "disputed")

    def test_selection_options_are_suite_wide_and_engine_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            suite = _eval_set()
            suite["cases"][0]["base_context"] = "none"
            with self.assertRaisesRegex(ValueError, "cannot override"):
                verify_retrieval_suite(kb, suite)
            verify_retrieval_suite(kb, _eval_set())
            path = kb / "audit/completion-report.json"
            completion = json.loads(path.read_text())
            completion["retrieval_engine_version"] = "an-old-engine"
            path.write_text(json.dumps(completion))
            with self.assertRaisesRegex(ValueError, "engine"):
                READER.query(kb, "alpha")

    def test_view_modules_are_identical_and_no_io_escape_remains(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "agent-knowledge-reader/scripts/context_units.py").read_bytes(),
                         (root / "agent-session-knowledge-rebuilder/scripts/session_kb/context_units.py").read_bytes())
        with patch.object(Path, "read_text", side_effect=AssertionError("read")):
            self.assertEqual(READER.query(Path("unused"), "alpha", context_sufficient=True, view="current")["match_status"], "skipped")

    def test_contract_three_requires_binding_but_legacy_two_can_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = _published_kb(Path(directory))
            verify_retrieval_suite(kb, _eval_set())
            report_path, completion_path = kb / "audit/retrieval-verification.json", kb / "audit/completion-report.json"
            report, completion = json.loads(report_path.read_text()), json.loads(completion_path.read_text())
            for value in (report, completion):
                value.pop("retrieval_engine_version")
                value.pop("selection_options")
            for contract in (3, 2):
                report["retrieval_contract_version"] = completion["retrieval_contract_version"] = contract
                completion["retrieval_verification_sha256"] = sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                report_path.write_text(json.dumps(report))
                completion_path.write_text(json.dumps(completion))
                if contract == 3:
                    with self.assertRaisesRegex(ValueError, "requires an engine"):
                        READER.query(kb, "alpha")
                else:
                    result = READER.query(kb, "alpha")
                    self.assertFalse(result["usage_receipt"]["verified_profile_used"])

    def test_current_state_reference_must_exist(self):
        for reference in ("missing", ["bad"]):
            with self.assertRaises(ValueError):
                validate_units({"context_units": [], "current_state": {"goal": reference}})

    def test_current_state_cannot_reactivate_stale_or_retracted_units(self):
        for status in ("stale", "retracted"):
            with self.assertRaisesRegex(ValueError, "inactive"):
                validate_units({"context_units": [{"unit_id": "old", "text": "Old goal", "status": status,
                    "evidence_event_ids": ["evt-old"]}], "current_state": {"goal": "old"}})

    def test_review_validates_state_and_distill_publishes_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "audit").mkdir()
            event = make_event("evt-goal", "project:alpha", 0, "Build the alpha engine.")
            (root / "audit/events.jsonl").write_text(json.dumps(event.to_dict()) + "\n")
            (root / "audit/completion-report.json").write_text(json.dumps({"run_id": "run-units", "gates": {
                key: True for key in ("frozen_snapshot", "transport_accounted", "parse_clean", "discovery_coverage_complete", "unsupported_formats_clear")}}))
            path = root / "review.json"
            create_review_template(root, path)
            review = json.loads(path.read_text())
            project = review["projects"][0]
            project.update(semantic_status="reviewed", reading_receipts=[create_review_packet(root, "project:alpha")["receipt"]],
                link_analysis={"status": "intentional-isolate", "rationale": "One synthetic project."},
                completion={"level": "requested", "status": "observed", "evidence_event_ids": ["evt-goal"], "rationale": "Only the request is observed."})
            project["history"]["objective"] = [{"text": event.content, "status": "observed", "evidence_event_ids": ["evt-goal"]}]
            before = project_synthesis_sha256(project)
            project["current_state"] = {"goal": {"section": "objective", "item_index": 0}}
            self.assertNotEqual(project_synthesis_sha256(project), before)
            review["reviewer"] = {"id": "synthetic-reviewer", "reviewed_at": "2026-02-02", "attestation": "Read the complete synthetic chain."}
            review["actor_attributions"] = [{"attribution_id": "actor-primary", "scope": "project-user-lane", "project_key": "project:alpha",
                "actor_kind": "primary_user", "basis": ["native-user-lane", "role-context-review"], "evidence_event_ids": ["evt-goal"], "rationale": "The complete synthetic record establishes the owner of this request."}]
            path.write_text(json.dumps(review))
            self.assertEqual(validate_review(root, path)[2], [])
            distill_review(root, path)
            index = json.loads((root / "knowledge/knowledge-index.json").read_text())
            published = next(d for d in index["documents"] if d.get("project_key") == "project:alpha")
            self.assertEqual(published["current_state"], {"goal": "history:objective:0"})
            self.assertEqual(published["context_units"][0]["evidence_event_ids"], ["evt-goal"])
            project["current_state"]["goal"]["item_index"] = 20
            path.write_text(json.dumps(review))
            self.assertTrue(any("missing history" in e for e in validate_review(root, path)[2]))


if __name__ == "__main__":
    unittest.main()
