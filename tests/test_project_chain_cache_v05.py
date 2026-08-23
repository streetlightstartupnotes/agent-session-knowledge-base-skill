from __future__ import annotations

import json
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "agent-session-knowledge-rebuilder"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from session_kb.model import UnifiedEvent  # noqa: E402
from session_kb.review import (  # noqa: E402
    _hash_events,
    _hash_ids,
    _project_groups,
    cross_project_state_sha256,
    create_review_packet,
    create_review_template,
    distill_review,
    project_synthesis_sha256,
    semantic_note_sha256,
    validate_review,
)


def make_event(
    event_id: str,
    project_key: str,
    sequence: int,
    content: str,
    *,
    source_agent: str = "codex",
    working_dir: str = "/shared/worktree",
) -> UnifiedEvent:
    return UnifiedEvent(
        event_id=event_id,
        source_agent=source_agent,
        adapter=f"{source_agent}-fixture",
        source_file_id=f"source-{source_agent}",
        source_relpath=f"{source_agent}/session.jsonl",
        session_id=f"session-{source_agent}",
        logical_session_id=f"logical-{source_agent}",
        record_locator=f"line:{sequence + 1}",
        sequence=sequence,
        role="user" if sequence % 2 == 0 else "assistant",
        actor_kind="native_user" if sequence % 2 == 0 else "agent",
        event_type="message",
        evidence_grade="A" if sequence % 2 == 0 else "C",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        timestamp=f"2026-02-01T00:00:{sequence:02d}Z",
        project_key=project_key,
        project_label=project_key.rsplit(":", 1)[-1],
        working_dir=working_dir,
    )


def source_binding(group: list[UnifiedEvent], selected: list[UnifiedEvent]) -> dict:
    ids = [event.event_id for event in selected]
    return {
        "proposed_project_key": group[0].project_key,
        "proposed_project_event_set_sha256": _hash_events(group),
        "event_ids": ids,
        "event_ids_sha256": _hash_ids(ids),
        "event_set_sha256": _hash_events(selected),
    }


class CorrectableProjectChainAndChunkCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_kb(self, events: list[UnifiedEvent], run_id: str = "run-1") -> Path:
        kb = self.root / "kb"
        (kb / "audit").mkdir(parents=True, exist_ok=True)
        (kb / "audit" / "events.jsonl").write_text(
            "".join(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        (kb / "audit" / "completion-report.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
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
        return kb

    def membership(self, events: list[UnifiedEvent], assignments: list[dict]) -> dict:
        return {
            "override_version": 1,
            "basis_event_set_sha256": _hash_events(events),
            "assignments": assignments,
        }

    def test_same_directory_proposal_can_be_split_without_loss_or_duplication(self) -> None:
        events = [make_event(f"evt-{index}", "project:shared-directory", index, f"message {index}") for index in range(4)]
        kb = self.write_kb(events)
        assignments = [
            {
                "override_id": "membership-split-alpha",
                "operation": "split",
                "target_project_key": "project:manual-alpha",
                "rationale": "The first two events describe a separate user objective and artifact chain.",
                "evidence_basis": ["objective-boundary", "explicit-user-intent"],
                "evidence_event_ids": ["evt-0"],
                "evidence_event_set_sha256": _hash_events([events[0]]),
                "sources": [source_binding(events, events[:2])],
            },
            {
                "override_id": "membership-split-beta",
                "operation": "split",
                "target_project_key": "project:manual-beta",
                "rationale": "The last two events start an independent objective in the same directory.",
                "evidence_basis": ["objective-boundary", "explicit-user-intent"],
                "evidence_event_ids": ["evt-2"],
                "evidence_event_set_sha256": _hash_events([events[2]]),
                "sources": [source_binding(events, events[2:])],
            },
        ]
        review_path = self.root / "split-review.json"
        create_review_template(kb, review_path, membership_overrides=self.membership(events, assignments))
        review = json.loads(review_path.read_text(encoding="utf-8"))

        self.assertEqual({project["project_key"] for project in review["projects"]}, {"project:manual-alpha", "project:manual-beta"})
        self.assertEqual(review["project_membership"]["proposal"]["project_count"], 1)
        self.assertEqual(review["project_membership"]["result"]["project_count"], 2)
        self.assertEqual(review["project_membership"]["result"]["event_count"], len(events))
        self.assertEqual(len(review["project_membership"]["diff"]), len(events))
        self.assertIn("split", {item["operation"] for item in review["project_membership"]["operations"]})
        self.assertIn("reassign", {item["operation"] for item in review["project_membership"]["operations"]})
        packet = create_review_packet(kb, "project:manual-alpha", review_path=review_path)
        self.assertEqual([event["event_id"] for event in packet["ordered_events"]], ["evt-0", "evt-1"])

    def test_cross_agent_proposals_can_merge_into_one_stable_project(self) -> None:
        codex = [make_event("evt-codex", "project:codex-proposal", 0, "Start the shared project.", source_agent="codex")]
        clacky = [make_event("evt-clacky", "project:clacky-proposal", 1, "Continue the same project.", source_agent="clacky")]
        events = codex + clacky
        kb = self.write_kb(events)
        groups = _project_groups(events)
        assignment = {
            "override_id": "membership-cross-agent-continuation",
            "operation": "merge",
            "target_project_key": "project:stable-shared-chain",
            "rationale": "Both records explicitly form one continuation chain across Agent hosts.",
            "evidence_basis": ["continuation-lineage", "observed-handoff"],
            "evidence_event_ids": ["evt-codex", "evt-clacky"],
            "evidence_event_set_sha256": _hash_events(events),
            "sources": [
                source_binding(groups["project:codex-proposal"], groups["project:codex-proposal"]),
                source_binding(groups["project:clacky-proposal"], groups["project:clacky-proposal"]),
            ],
        }
        review_path = self.root / "merge-review.json"
        create_review_template(kb, review_path, membership_overrides=self.membership(events, [assignment]))
        review = json.loads(review_path.read_text(encoding="utf-8"))

        self.assertEqual([project["project_key"] for project in review["projects"]], ["project:stable-shared-chain"])
        self.assertEqual(review["projects"][0]["event_count"], 2)
        self.assertEqual({item["event_id"] for item in review["project_membership"]["diff"]}, {"evt-codex", "evt-clacky"})
        self.assertIn("merge", {item["operation"] for item in review["project_membership"]["operations"]})

    def test_stale_or_duplicate_membership_bindings_fail_closed(self) -> None:
        events = [make_event(f"evt-{index}", "project:proposal", index, f"message {index}") for index in range(2)]
        kb = self.write_kb(events)
        first = {
            "override_id": "membership-first",
            "operation": "split",
            "target_project_key": "project:first",
            "rationale": "First assignment.",
            "evidence_basis": ["objective-boundary"],
            "evidence_event_ids": ["evt-0"],
            "evidence_event_set_sha256": _hash_events([events[0]]),
            "sources": [source_binding(events, [events[0]])],
        }
        duplicate = {
            "override_id": "membership-duplicate",
            "operation": "split",
            "target_project_key": "project:second",
            "rationale": "Duplicate assignment must fail.",
            "evidence_basis": ["objective-boundary"],
            "evidence_event_ids": ["evt-0"],
            "evidence_event_set_sha256": _hash_events([events[0]]),
            "sources": [source_binding(events, [events[0]])],
        }
        with self.assertRaisesRegex(ValueError, "already assigned"):
            create_review_template(kb, self.root / "duplicate.json", membership_overrides=self.membership(events, [first, duplicate]))

        stale_plan = self.membership(events, [first])
        changed = list(events)
        changed[0] = make_event("evt-0", "project:proposal", 0, "changed semantic content")
        self.write_kb(changed, "run-2")
        with self.assertRaisesRegex(ValueError, "basis_event_set_sha256 is stale"):
            create_review_template(kb, self.root / "stale.json", membership_overrides=stale_plan)

    def test_distill_uses_effective_partition_and_publishes_membership_audit(self) -> None:
        events = [make_event("evt-only", "project:proposal", 0, "Continue the renamed project chain.")]
        kb = self.write_kb(events)
        assignment = {
            "override_id": "membership-stable-rename",
            "operation": "reassign",
            "target_project_key": "project:stable-renamed",
            "rationale": "The explicit request identifies the stable project chain despite a changed proposal key.",
            "evidence_basis": ["explicit-user-intent", "continuation-lineage"],
            "evidence_event_ids": ["evt-only"],
            "evidence_event_set_sha256": _hash_events(events),
            "sources": [source_binding(events, events)],
        }
        review_path = self.root / "distill-review.json"
        create_review_template(kb, review_path, membership_overrides=self.membership(events, [assignment]))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        project = review["projects"][0]
        project["semantic_status"] = "reviewed"
        project["reading_receipts"] = [create_review_packet(kb, project["project_key"], review_path=review_path)["receipt"]]
        project["link_analysis"] = {"status": "intentional-isolate", "rationale": "No second project chain exists."}
        project["completion"] = {
            "level": "requested",
            "status": "observed",
            "evidence_event_ids": ["evt-only"],
            "rationale": "The request is directly observed; no delivery is asserted.",
        }
        project["history"]["objective"] = [
            {"text": "Continue the stable renamed project chain.", "evidence_event_ids": ["evt-only"], "status": "observed"}
        ]
        review["actor_attributions"] = [
            {
                "attribution_id": "actor-renamed-primary",
                "scope": "project-user-lane",
                "project_key": project["project_key"],
                "actor_kind": "primary_user",
                "basis": ["native-user-lane", "role-context-review"],
                "evidence_event_ids": ["evt-only"],
                "rationale": "The reviewed native lane is the primary user in this synthetic chain.",
            }
        ]
        review["reviewer"] = {"id": "reviewer", "reviewed_at": "2026-02-03", "attestation": "Read the effective complete chain."}
        review["cross_project_recheck"].update(
            {"completed": True, "reviewed_at": "2026-02-03", "rationale": "Checked the corrected partition and found no other project."}
        )
        review["cross_project_recheck"]["checked_state_sha256"] = cross_project_state_sha256(review)
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(kb, review_path)
        self.assertEqual(errors, [])

        distill_review(kb, review_path)
        audit = json.loads((kb / "audit" / "project-membership.json").read_text(encoding="utf-8"))
        semantic_rows = [json.loads(line) for line in (kb / "audit" / "semantic-dispositions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(audit, review["project_membership"])
        self.assertEqual({row["project_key"] for row in semantic_rows}, {"project:stable-renamed"})

    def test_prior_membership_rebases_unchanged_events_but_does_not_guess_new_tail(self) -> None:
        original = [make_event("evt-old", "project:proposal", 0, "Established project membership.")]
        kb = self.write_kb(original, "run-1")
        assignment = {
            "override_id": "membership-old-stable",
            "operation": "reassign",
            "target_project_key": "project:stable",
            "rationale": "The reviewed request binds this record to the stable project.",
            "evidence_basis": ["explicit-user-intent"],
            "evidence_event_ids": ["evt-old"],
            "evidence_event_set_sha256": _hash_events(original),
            "sources": [source_binding(original, original)],
        }
        prior_path = self.root / "prior-membership.json"
        create_review_template(kb, prior_path, membership_overrides=self.membership(original, [assignment]))
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        prior["reviewer"] = {"id": "reviewer", "reviewed_at": "2026-02-03", "attestation": "Reviewed the corrected chain."}
        prior["projects"][0]["semantic_status"] = "reviewed"
        prior_path.write_text(json.dumps(prior, ensure_ascii=False, indent=2), encoding="utf-8")

        current_events = original + [make_event("evt-new", "project:proposal", 1, "New tail needs membership review.")]
        self.write_kb(current_events, "run-2")
        next_path = self.root / "rebased-membership.json"
        create_review_template(kb, next_path, prior_review=prior_path)
        current = json.loads(next_path.read_text(encoding="utf-8"))

        self.assertEqual(
            {project["project_key"]: project["event_count"] for project in current["projects"]},
            {"project:proposal": 1, "project:stable": 1},
        )
        self.assertEqual(current["project_membership"]["assignments"][0]["sources"][0]["event_ids"], ["evt-old"])
        self.assertEqual({item["event_id"] for item in current["project_membership"]["diff"]}, {"evt-old"})
        self.assertTrue(current["cross_project_recheck"]["required"])

    def test_tail_append_reuses_only_unchanged_chunks_and_requires_new_synthesis(self) -> None:
        events = [make_event(f"evt-{index}", "project:cache", index, f"message {index}") for index in range(4)]
        kb = self.write_kb(events, "run-1")
        prior_path = self.root / "prior.json"
        create_review_template(kb, prior_path)
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        prior["reviewer"] = {"id": "reviewer", "reviewed_at": "2026-02-02", "attestation": "Read complete chain."}
        project = prior["projects"][0]
        project["semantic_status"] = "reviewed"
        project["history"]["objective"] = [
            {"text": "Old free-text project synthesis must not carry after a change.", "evidence_event_ids": ["evt-0"], "status": "observed"}
        ]
        chunks = []
        receipts = []
        for start in (0, 2):
            packet = create_review_packet(kb, "project:cache", start_event=start, max_events=2)
            chunk = packet["semantic_chunk"]
            chunk["notes"] = [
                {
                    "text": f"Evidence-bound note for range {start}:{start + 2}.",
                    "evidence_event_ids": [packet["ordered_events"][0]["event_id"]],
                    "status": "observed",
                }
            ]
            chunk["notes"][0]["source_note_sha256"] = semantic_note_sha256(chunk["notes"][0])
            chunk["reviewed_at"] = "2026-02-02"
            chunk["attestation"] = "Read this exact hash-bound range."
            chunks.append(chunk)
            receipts.append(packet["receipt"])
        project["semantic_chunks"] = chunks
        project["reading_receipts"] = receipts
        prior_path.write_text(json.dumps(prior, ensure_ascii=False, indent=2), encoding="utf-8")

        changed = list(events)
        changed.append(make_event("evt-4", "project:cache", 4, "new tail event"))
        self.write_kb(changed, "run-2")
        next_path = self.root / "next.json"
        summary = create_review_template(kb, next_path, prior_review=prior_path)
        current = json.loads(next_path.read_text(encoding="utf-8"))
        current_project = current["projects"][0]

        self.assertEqual(summary["carried_forward"]["chunk_count"], 1)
        self.assertEqual(summary["carried_forward"]["partially_reused_project_keys"], ["project:cache"])
        self.assertEqual(len(current_project["semantic_chunks"]), 1)
        self.assertEqual(current_project["semantic_chunks"][0]["start_event"], 0)
        self.assertEqual(current_project["project_synthesis"]["reopened_ranges"], [{"start_event": 2, "end_event_exclusive": 5}])
        self.assertEqual(current_project["semantic_status"], "unreviewed")
        self.assertEqual(sum(len(items) for items in current_project["history"].values()), 0)

        _, _, pending_errors = validate_review(kb, next_path)
        self.assertTrue(any("full project_synthesis" in error for error in pending_errors))
        self.assertTrue(any("cross_project_recheck" in error for error in pending_errors))

        fresh = create_review_packet(kb, "project:cache", start_event=2, max_events=3)
        current_project["reading_receipts"].append(fresh["receipt"])
        current_project["semantic_status"] = "reviewed"
        current_project["link_analysis"] = {"status": "intentional-isolate", "rationale": "No second chain exists."}
        current_project["completion"] = {
            "level": "requested",
            "status": "observed",
            "evidence_event_ids": ["evt-4"],
            "rationale": "Only the new request is directly evidenced.",
        }
        current_project["history"]["objective"] = [
            {"text": "Current full-chain synthesis includes the changed range and tail.", "evidence_event_ids": ["evt-2", "evt-4"], "status": "observed"}
        ]
        current["actor_attributions"] = [
            {
                "attribution_id": "actor-current-cache-user",
                "scope": "project-user-lane",
                "project_key": "project:cache",
                "actor_kind": "primary_user",
                "basis": ["native-user-lane", "role-context-review"],
                "evidence_event_ids": ["evt-4"],
                "rationale": "The current complete chain identifies this native input lane as the primary user.",
            }
        ]
        current_project["project_synthesis"].update(
            {
                "status": "completed",
                "reviewed_at": "2026-02-03",
                "attestation": "Reused only the unchanged chunk, read the reopened range, then synthesized the complete current chain.",
                "rationale": "The old free-text project synthesis was discarded and rebuilt from current evidence.",
            }
        )
        current_project["project_synthesis"]["synthesis_sha256"] = project_synthesis_sha256(current_project)
        current["reviewer"] = {"id": "reviewer", "reviewed_at": "2026-02-03", "attestation": "Read reused evidence notes and every reopened event."}
        current["cross_project_recheck"].update(
            {"completed": True, "reviewed_at": "2026-02-03", "rationale": "Rechecked project boundaries and found no second chain."}
        )
        current["cross_project_recheck"]["checked_state_sha256"] = cross_project_state_sha256(current)
        next_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(kb, next_path)
        self.assertEqual(errors, [])

        current_project["semantic_chunks"][0]["notes"][0]["text"] = "Tampered carried note."
        next_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, tamper_errors = validate_review(kb, next_path)
        self.assertTrue(any("source_note_sha256" in error for error in tamper_errors))


if __name__ == "__main__":
    unittest.main()
