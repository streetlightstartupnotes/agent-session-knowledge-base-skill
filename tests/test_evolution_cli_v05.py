from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent-session-knowledge-rebuilder" / "scripts"))

import session_kb.cli as cli_module  # noqa: E402
from session_kb.cli import main  # noqa: E402
from session_kb.model import UnifiedEvent  # noqa: E402
from session_kb.review import (  # noqa: E402
    _hash_events,
    _hash_ids,
    create_review_packet,
    create_review_template,
    cross_project_state_sha256,
    validate_review,
)


def _event(event_id: str, sequence: int, role: str, content: str) -> UnifiedEvent:
    return UnifiedEvent(
        event_id=event_id,
        source_agent="synthetic",
        adapter="synthetic-fixture",
        source_file_id="source-synthetic",
        source_relpath="synthetic/session.jsonl",
        session_id="session-synthetic",
        logical_session_id="logical-synthetic",
        record_locator=f"line:{sequence}",
        sequence=sequence,
        role=role,
        actor_kind="native_user" if role == "user" else "primary_agent",
        event_type="message" if role == "user" else "delivery",
        evidence_grade="A" if role == "user" else "B",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        timestamp=f"2026-08-23T10:00:0{sequence}Z",
        project_key="project:proposal",
        project_label="Synthetic evolution",
        working_dir="/synthetic/evolution",
    )


class EvolutionCliV05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kb, self.review_path, self.proposal_path = self._review_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _review_fixture(self) -> tuple[Path, Path, Path]:
        events = [
            _event("event-user-before", 1, "user", "Do not overstate completion."),
            _event("event-agent-after", 2, "assistant", "The report stopped at the observed level."),
        ]
        kb = self.root / "kb"
        (kb / "audit").mkdir(parents=True)
        (kb / "audit" / "events.jsonl").write_text(
            "".join(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        (kb / "audit" / "completion-report.json").write_text(
            json.dumps(
                {
                    "run_id": "run-evolution-cli",
                    "gates": {
                        "frozen_snapshot": True,
                        "transport_accounted": True,
                        "parse_clean": True,
                        "discovery_coverage_complete": True,
                        "unsupported_formats_clear": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        event_ids = [event.event_id for event in events]
        membership = {
            "override_version": 1,
            "basis_event_set_sha256": _hash_events(events),
            "assignments": [
                {
                    "override_id": "membership-evolution-cli",
                    "operation": "reassign",
                    "target_project_key": "project:stable-evolution",
                    "rationale": "The explicit fixture intent binds both events to one stable project.",
                    "evidence_basis": ["explicit-user-intent"],
                    "evidence_event_ids": ["event-user-before"],
                    "evidence_event_set_sha256": _hash_events(events[:1]),
                    "sources": [
                        {
                            "proposed_project_key": "project:proposal",
                            "proposed_project_event_set_sha256": _hash_events(events),
                            "event_ids": event_ids,
                            "event_ids_sha256": _hash_ids(event_ids),
                            "event_set_sha256": _hash_events(events),
                        }
                    ],
                }
            ],
        }
        review_path = self.root / "review.json"
        create_review_template(kb, review_path, membership_overrides=membership)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        packet = create_review_packet(kb, "project:stable-evolution", review_path=review_path)
        project = review["projects"][0]
        project["semantic_status"] = "reviewed"
        project["reading_receipts"] = [packet["receipt"]]
        project["link_analysis"] = {"status": "intentional-isolate", "rationale": "Only one chain exists."}
        project["completion"] = {
            "level": "requested",
            "status": "observed",
            "evidence_event_ids": ["event-user-before"],
            "rationale": "Only the explicit request is claimed.",
        }
        project["history"]["objective"] = [
            {
                "text": "Stop completion reporting at observed evidence.",
                "evidence_event_ids": ["event-user-before"],
                "status": "observed",
            }
        ]
        review["actor_attributions"] = [
            {
                "attribution_id": "actor-evolution-cli-user",
                "scope": "project-user-lane",
                "project_key": "project:stable-evolution",
                "actor_kind": "primary_user",
                "basis": ["native-user-lane", "role-context-review"],
                "evidence_event_ids": ["event-user-before"],
                "rationale": "The native user lane is the primary user in this synthetic fixture.",
            }
        ]
        review["base_claims"] = [
            {
                "claim_id": "claim-evolution-cli-rule",
                "knowledge_type": "evidence_rule",
                "statement": "Completion reporting follows observed evidence.",
                "status": "confirmed",
                "confidence": "high",
                "evidence_event_ids": ["event-user-before"],
                "observed_at": "2026-08-23",
                "applies_to": "this synthetic project",
                "conflicts": [],
                "supersedes": [],
            }
        ]
        review["feedback_signals"] = [
            {
                "feedback_id": "feedback-evolution-cli",
                "project_key": "project:stable-evolution",
                "kind": "negative",
                "object": "completion",
                "scope": "project",
                "statement": "Do not claim completion beyond observed evidence.",
                "status": "observed",
                "evidence_event_ids": ["event-user-before"],
                "applies_to": "this synthetic project",
            }
        ]
        review["reviewer"] = {
            "id": "synthetic-reviewer",
            "reviewed_at": "2026-08-23",
            "attestation": "Read both events in the corrected chain.",
        }
        review["cross_project_recheck"].update(
            {
                "completed": True,
                "reviewed_at": "2026-08-23",
                "rationale": "The corrected partition contains only one project.",
            }
        )
        review["cross_project_recheck"]["checked_state_sha256"] = cross_project_state_sha256(review)
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _, _, errors = validate_review(kb, review_path)
        self.assertEqual(errors, [])

        proposal_path = self.root / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "feedback_ids": ["feedback-evolution-cli"],
                    "scope": "project",
                    "rule_version": 1,
                    "proposed_rule": "Report only the highest observed completion level.",
                    "rationale": "The feedback rejects unsupported completion claims.",
                    "expected_behavior_change": "A later report stops at the observed level.",
                }
            ),
            encoding="utf-8",
        )
        return kb, review_path, proposal_path

    def _main(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(list(arguments))
        return result, stdout.getvalue(), stderr.getvalue()

    def _propose(self) -> tuple[int, str, str]:
        return self._main(
            "evolution-propose",
            "--kb",
            str(self.kb),
            "--review",
            str(self.review_path),
            "--proposal",
            str(self.proposal_path),
        )

    def test_cli_reopens_bound_recheck_and_keeps_distill_closed(self) -> None:
        code, output, error = self._propose()
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["cross_project_recheck"]["status"], "reopened")
        saved = json.loads(self.review_path.read_text(encoding="utf-8"))
        recheck = saved["cross_project_recheck"]
        self.assertFalse(recheck["completed"])
        self.assertEqual(recheck["checked_state_sha256"], "")
        self.assertEqual(recheck["reopen_reason"], "evolution-state-changed")
        self.assertEqual(recheck["pending_evolution_mutations"][-1]["state_sha256"], cross_project_state_sha256(saved))
        _, _, draft_errors = validate_review(self.kb, self.review_path, allow_pending_cross_project_recheck=True)
        self.assertEqual(draft_errors, [])
        _, _, publish_errors = validate_review(self.kb, self.review_path)
        self.assertTrue(any("completed cross_project_recheck" in item for item in publish_errors))

        evolution_id = saved["rule_evolutions"][0]["evolution_id"]
        code, _, error = self._main(
            "evolution-decide",
            "--kb",
            str(self.kb),
            "--review",
            str(self.review_path),
            "--evolution-id",
            evolution_id,
            "--decision",
            "approve",
            "--approval-event-id",
            "event-user-before",
            "--promoted-claim-id",
            "claim-evolution-cli-rule",
        )
        self.assertEqual((code, error), (0, ""))

        code, _, error = self._main(
            "evolution-evaluate",
            "--kb",
            str(self.kb),
            "--review",
            str(self.review_path),
            "--evolution-id",
            evolution_id,
            "--result",
            "passed",
            "--baseline-event-id",
            "event-agent-after",
            "--validation-event-id",
            "event-user-before",
            "--observed-change",
            "This reversed comparison is invalid.",
        )
        self.assertEqual(code, 2)
        self.assertIn("not later", error)

        code, output, error = self._main(
            "evolution-evaluate",
            "--kb",
            str(self.kb),
            "--review",
            str(self.review_path),
            "--evolution-id",
            evolution_id,
            "--result",
            "passed",
            "--baseline-event-id",
            "event-user-before",
            "--validation-event-id",
            "event-agent-after",
            "--observed-change",
            "The later observable delivery stayed within the evidence boundary.",
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["cross_project_recheck"]["status"], "pending")
        code, _, error = self._main("distill", "--kb", str(self.kb), "--review", str(self.review_path))
        self.assertEqual(code, 2)
        self.assertIn("completed cross_project_recheck", error)

    def test_tampered_pending_evolution_state_blocks_next_cli_mutation(self) -> None:
        code, _, error = self._propose()
        self.assertEqual((code, error), (0, ""))
        saved = json.loads(self.review_path.read_text(encoding="utf-8"))
        saved["rule_evolutions"][0]["proposed_rule"] = "Tampered after the pending-state hash was recorded."
        self.review_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evolution_id = saved["rule_evolutions"][0]["evolution_id"]
        code, _, error = self._main(
            "evolution-decide",
            "--kb",
            str(self.kb),
            "--review",
            str(self.review_path),
            "--evolution-id",
            evolution_id,
            "--decision",
            "reject",
        )
        self.assertEqual(code, 2)
        self.assertIn("valid evolution-mutation reopen record", error)

    def test_validate_review_independently_rejects_reversed_behavior_evidence(self) -> None:
        code, _, error = self._propose()
        self.assertEqual((code, error), (0, ""))
        saved = json.loads(self.review_path.read_text(encoding="utf-8"))
        evolution_id = saved["rule_evolutions"][0]["evolution_id"]
        code, _, error = self._main(
            "evolution-decide",
            "--kb",
            str(self.kb),
            "--review",
            str(self.review_path),
            "--evolution-id",
            evolution_id,
            "--decision",
            "approve",
            "--approval-event-id",
            "event-user-before",
            "--promoted-claim-id",
            "claim-evolution-cli-rule",
        )
        self.assertEqual((code, error), (0, ""))
        saved = json.loads(self.review_path.read_text(encoding="utf-8"))
        evolution = saved["rule_evolutions"][0]
        evolution.update(
            {
                "status": "validated",
                "baseline_event_ids": ["event-agent-after"],
                "validation_event_ids": ["event-user-before"],
                "validation_result": "passed",
                "observed_behavior_change": "This manually reversed state must still fail validation.",
            }
        )
        saved["cross_project_recheck"]["pending_evolution_mutations"][-1]["state_sha256"] = cross_project_state_sha256(saved)
        self.review_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _, _, errors = validate_review(self.kb, self.review_path, allow_pending_cross_project_recheck=True)
        self.assertTrue(any("not later" in item for item in errors))

    def test_cli_loads_review_only_after_entering_mutation_lock(self) -> None:
        late_feedback_id = "feedback-added-under-lock"
        late_proposal = self.root / "late-proposal.json"
        late_proposal.write_text(
            json.dumps(
                {
                    "feedback_ids": [late_feedback_id],
                    "scope": "project",
                    "rule_version": 1,
                    "proposed_rule": "Use the state committed by the prior lock holder.",
                    "rationale": "The feedback becomes visible only after entering the mutation lock.",
                    "expected_behavior_change": "The proposal is based on the latest locked review state.",
                }
            ),
            encoding="utf-8",
        )

        @contextmanager
        def prior_writer_then_lock(*_args: object, **_kwargs: object):
            review = json.loads(self.review_path.read_text(encoding="utf-8"))
            review["feedback_signals"].append(
                {
                    "feedback_id": late_feedback_id,
                    "project_key": "project:stable-evolution",
                    "kind": "gap",
                    "object": "method",
                    "scope": "project",
                    "statement": "Reload the review after acquiring its mutation lock.",
                    "status": "observed",
                    "evidence_event_ids": ["event-user-before"],
                    "applies_to": "this synthetic project",
                }
            )
            review["cross_project_recheck"]["checked_state_sha256"] = cross_project_state_sha256(review)
            self.review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            yield

        with patch.object(cli_module, "mutation_lock", prior_writer_then_lock):
            code, output, error = self._main(
                "evolution-propose",
                "--kb",
                str(self.kb),
                "--review",
                str(self.review_path),
                "--proposal",
                str(late_proposal),
            )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["status"], "candidate-recorded")
        saved = json.loads(self.review_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["rule_evolutions"][0]["feedback_ids"], [late_feedback_id])


if __name__ == "__main__":
    unittest.main()
