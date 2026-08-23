from __future__ import annotations

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

from session_kb.adapters import builtin_registry  # noqa: E402
from session_kb.discovery import discover, freeze_sources, load_snapshot  # noqa: E402
from session_kb.pipeline import build_knowledge_base  # noqa: E402
import session_kb.pipeline as pipeline_module  # noqa: E402
from session_kb.locking import lock_path, mutation_lock  # noqa: E402
from session_kb.model import SourceFile  # noqa: E402
from session_kb.review import create_review_packet, create_review_template, distill_review, validate_review  # noqa: E402
from session_kb.verification import verify_retrieval  # noqa: E402


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def append_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def visible(role: str, text: str) -> dict:
    return {
        "timestamp": "2026-02-01T00:00:00Z",
        "type": "event_msg",
        "payload": {"type": "user_message" if role == "user" else "agent_message", "message": text},
    }


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class EvolutionAndIncrementalReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = builtin_registry()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self, source_root: Path, output: Path, incremental: bool = False) -> dict:
        discovery = discover(self.registry, roots=[source_root], output_dir=output)
        snapshot = freeze_sources(discovery.sources)
        snapshot["discovery"] = discovery.to_dict()
        return build_knowledge_base(
            self.registry,
            discovery.sources,
            output,
            discovery=discovery,
            snapshot=snapshot,
            incremental=incremental,
        )

    def prepare_review(self, output: Path, review_path: Path) -> tuple[dict, list[dict]]:
        create_review_template(output, review_path)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        events = read_jsonl(output / "audit" / "events.jsonl")
        review["reviewer"] = {
            "id": "synthetic-reviewer",
            "reviewed_at": "2026-02-02T00:00:00Z",
            "attestation": "Every ordered event in each hash-bound project chain was read.",
        }
        for project in review["projects"]:
            project_events = [event for event in events if event["project_key"] == project["project_key"]]
            user_event = next(event for event in project_events if event["role"] == "user")
            packet = create_review_packet(output, project["project_key"])
            project["semantic_status"] = "reviewed"
            project["reading_receipts"] = [packet["receipt"]]
            project["link_analysis"] = {
                "status": "intentional-isolate",
                "rationale": "No evidence-backed cross-project relationship was found in this synthetic chain.",
            }
            project["completion"] = {
                "level": "requested",
                "status": "observed",
                "evidence_event_ids": [user_event["event_id"]],
                "rationale": "The request is observed; no stronger completion layer is asserted by this fixture.",
            }
            project["history"]["objective"] = [
                {
                    "text": user_event["content"],
                    "evidence_event_ids": [user_event["event_id"]],
                    "status": "observed",
                }
            ]
            review["actor_attributions"].append(
                {
                    "attribution_id": f"actor-{len(review['actor_attributions']) + 1}",
                    "scope": "project-user-lane",
                    "project_key": project["project_key"],
                    "actor_kind": "primary_user",
                    "basis": ["native-user-lane", "role-context-review"],
                    "evidence_event_ids": [user_event["event_id"]],
                    "rationale": "The complete project context identifies this native input lane as the primary user for this chain.",
                }
            )
        return review, events

    def test_review_packet_supports_contiguous_hash_bound_ranges(self) -> None:
        source = self.root / "packet-source"
        path = source / "packet.jsonl"
        write_jsonl(
            path,
            [{"type": "session_meta", "payload": {"id": "packet-session", "cwd": "/work/packet"}}]
            + [visible("user" if index % 2 == 0 else "assistant", f"packet event {index}") for index in range(5)],
        )
        output = self.root / "packet-kb"
        self.build(source, output)
        project_key = read_jsonl(output / "audit" / "events.jsonl")[0]["project_key"]

        first = create_review_packet(output, project_key, start_event=0, max_events=2)
        second = create_review_packet(output, project_key, start_event=2, max_events=2)
        last = create_review_packet(output, project_key, start_event=4, max_events=2)
        whole = create_review_packet(output, project_key)

        self.assertEqual(first["event_ids_sha256"], second["event_ids_sha256"])
        self.assertEqual(second["range"]["previous_event_id"], first["range"]["last_event_id"])
        self.assertEqual(first["range"]["next_event_id"], second["range"]["first_event_id"])
        self.assertEqual(last["range"]["next_start_event"], None)
        self.assertFalse(first["range"]["complete_project"])
        self.assertTrue(whole["range"]["complete_project"])
        self.assertEqual(sum(len(packet["ordered_events"]) for packet in (first, second, last)), whole["event_count"])

    def test_mutation_lock_blocks_a_second_writer_and_cleans_up(self) -> None:
        kb = self.root / "locked-kb"
        with mutation_lock(kb, "first-writer") as active_lock:
            self.assertEqual(active_lock, lock_path(kb))
            self.assertTrue(active_lock.is_file())
            with self.assertRaisesRegex(ValueError, "another knowledge-base mutation"):
                with mutation_lock(kb, "second-writer"):
                    self.fail("the second writer must never acquire the lock")
        self.assertFalse(lock_path(kb).exists())

    def test_private_home_redaction_does_not_merge_distinct_project_identities(self) -> None:
        source = self.root / "two-accounts"
        first_account = "account" + "_alpha"
        second_account = "account" + "_beta"
        write_jsonl(
            source / "alpha.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "account-alpha", "cwd": "/home/" + first_account + "/work"}},
                visible("user", "First account project."),
            ],
        )
        write_jsonl(
            source / "beta.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "account-beta", "cwd": "/home/" + second_account + "/work"}},
                visible("user", "Second account project."),
            ],
        )
        output = self.root / "two-account-kb"
        self.build(source, output)
        events = read_jsonl(output / "audit" / "events.jsonl")

        self.assertEqual(len({event["project_key"] for event in events}), 2)
        self.assertEqual({event["working_dir"] for event in events}, {"~/work"})
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn(first_account, serialized)
        self.assertNotIn(second_account, serialized)

    def test_review_init_carries_only_hash_identical_projects(self) -> None:
        source = self.root / "carry-source"
        first_file = source / "first.jsonl"
        second_file = source / "second.jsonl"
        write_jsonl(
            first_file,
            [
                {"type": "session_meta", "payload": {"id": "first-session", "cwd": "/work/first"}},
                visible("user", "Build the unchanged first project as the foundation for the second project."),
            ],
        )
        write_jsonl(
            second_file,
            [
                {"type": "session_meta", "payload": {"id": "second-session", "cwd": "/work/second"}},
                visible("user", "Build the second project using the first project's foundation."),
            ],
        )
        output = self.root / "carry-kb"
        self.build(source, output)
        prior_path = output / "review" / "prior.json"
        prior, prior_events = self.prepare_review(output, prior_path)
        first_project, second_project = sorted(prior["projects"], key=lambda item: item["title"])
        first_event = next(event for event in prior_events if event["project_key"] == first_project["project_key"] and event["role"] == "user")
        second_event = next(event for event in prior_events if event["project_key"] == second_project["project_key"] and event["role"] == "user")
        for project in prior["projects"]:
            project["link_analysis"] = {
                "status": "linked",
                "rationale": "Both reviewed chains explicitly name the same foundation handoff.",
            }
        prior["base_claims"] = [
            {
                "claim_id": "claim-foundation-direction",
                "knowledge_type": "direction",
                "subject": "primary_user",
                "statement": "The first project provides a foundation for the second project.",
                "status": "confirmed",
                "confidence": "high",
                "evidence_event_ids": [first_event["event_id"]],
                "observed_at": "2026-02-01",
                "applies_to": "these two reviewed project chains",
                "conflicts": [],
                "supersedes": [],
            }
        ]
        prior["project_relationships"] = [
            {
                "relationship_id": "rel-first-second-foundation",
                "source_project_key": first_project["project_key"],
                "target_project_key": second_project["project_key"],
                "relation": "provides-foundation-for",
                "evidence_basis": ["explicit-user-intent", "observed-handoff"],
                "direction": "directed",
                "status": "confirmed",
                "confidence": "high",
                "source_evidence_event_ids": [first_event["event_id"]],
                "target_evidence_event_ids": [second_event["event_id"]],
                "rationale": "Both user messages explicitly describe the foundation handoff.",
            }
        ]
        prior_path.write_text(json.dumps(prior, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, prior_path)
        self.assertEqual(errors, [])

        append_jsonl(second_file, [visible("assistant", "The second project now has a new event.")])
        self.build(source, output, incremental=True)
        next_path = output / "review" / "next.json"
        summary = create_review_template(output, next_path, prior_review=prior_path)
        next_review = json.loads(next_path.read_text(encoding="utf-8"))
        statuses = {project["title"]: project["semantic_status"] for project in next_review["projects"]}

        self.assertEqual(summary["carried_forward"]["project_count"], 1)
        self.assertEqual(statuses["first"], "reviewed")
        self.assertEqual(statuses["second"], "unreviewed")
        self.assertEqual(len(next_review["actor_attributions"]), 1)
        self.assertEqual([claim["claim_id"] for claim in next_review["base_claims"]], ["claim-foundation-direction"])
        self.assertEqual(next_review["project_relationships"], [])
        self.assertTrue(next_review["carry_forward"]["requires_cross_project_recheck"])
        _, _, carry_errors = validate_review(output, next_path)
        self.assertTrue(any("cross_project_recheck" in error for error in carry_errors))
        next_review["carry_forward"]["project_count"] = 0
        next_path.write_text(json.dumps(next_review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, tampered_carry_errors = validate_review(output, next_path)
        self.assertTrue(any("does not match actual carried project markers" in error for error in tampered_carry_errors))
        self.assertTrue(any("completed cross_project_recheck" in error for error in tampered_carry_errors))

    def test_feedback_rule_promotion_needs_attribution_scope_and_behavior_evidence(self) -> None:
        source = self.root / "evolution-source"
        write_jsonl(
            source / "evolution.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "evolution-session", "cwd": "/work/evolution"}},
                visible("user", "For this artifact, remove unsupported completion claims."),
                visible("user", "I explicitly approve this as a global rule for future work."),
                visible("assistant", "Applied the approved rule in the next deliverable."),
                visible("user", "The next deliverable avoided the unsupported completion claim."),
            ],
        )
        output = self.root / "evolution-kb"
        self.build(source, output)
        review_path = output / "review" / "evolution.json"
        review, events = self.prepare_review(output, review_path)
        project = review["projects"][0]
        user_events = [event for event in events if event["role"] == "user"]
        feedback_event, approval_event, validation_event = user_events
        review["base_claims"] = [
            {
                "claim_id": "claim-no-unsupported-completion",
                "knowledge_type": "collaboration",
                "subject": "primary_user",
                "statement": "Do not report completion beyond the strongest observed evidence.",
                "status": "confirmed",
                "confidence": "high",
                "evidence_event_ids": [approval_event["event_id"]],
                "observed_at": "2026-02-01",
                "applies_to": "future tasks unless explicitly overridden",
                "rule_scope": "global",
                "derivation": "feedback-promotion",
                "conflicts": [],
                "supersedes": [],
            }
        ]
        review["feedback_signals"] = [
            {
                "feedback_id": "feedback-completion-claim",
                "project_key": project["project_key"],
                "kind": "negative",
                "object": "completion",
                "scope": "global",
                "statement": "Unsupported completion language was rejected.",
                "status": "observed",
                "evidence_event_ids": [feedback_event["event_id"]],
                "applies_to": "completion reporting",
            }
        ]
        review["rule_evolutions"] = [
            {
                "evolution_id": "evolution-completion-v1",
                "status": "approved",
                "scope": "global",
                "rule_version": 1,
                "feedback_ids": ["feedback-completion-claim"],
                "before_rule": "",
                "proposed_rule": "Do not report completion beyond the strongest observed evidence.",
                "rationale": "A scoped correction was followed by an explicit global approval.",
                "expected_behavior_change": "Later delivery reports stop at the observed completion layer.",
                "promoted_claim_id": "claim-no-unsupported-completion",
                "approval_event_ids": [approval_event["event_id"]],
                "explicit_global_approval": False,
                "baseline_event_ids": [],
                "validation_result": "pending",
                "validation_event_ids": [],
                "observed_behavior_change": "",
            },
            {
                "evolution_id": "evolution-candidate-only",
                "status": "candidate",
                "scope": "task-type",
                "rule_version": 1,
                "feedback_ids": ["feedback-completion-claim"],
                "before_rule": "",
                "proposed_rule": "Candidate text must remain out of active knowledge.",
                "rationale": "This has not been approved.",
                "expected_behavior_change": "None until approval.",
                "promoted_claim_id": "",
                "approval_event_ids": [],
                "explicit_global_approval": False,
                "baseline_event_ids": [],
                "validation_result": "pending",
                "validation_event_ids": [],
                "observed_behavior_change": "",
            },
        ]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertTrue(any("explicit_global_approval" in error for error in errors))

        review["rule_evolutions"][0]["explicit_global_approval"] = True
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertEqual(errors, [])

        review["rule_evolutions"][0].update(
            {
                "status": "validated",
                "validation_result": "passed",
                "baseline_event_ids": [feedback_event["event_id"]],
                "validation_event_ids": [validation_event["event_id"]],
                "observed_behavior_change": "The later user feedback confirmed the unsupported claim was absent.",
            }
        )
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertEqual(errors, [])
        distilled = distill_review(output, review_path)
        self.assertEqual(distilled["status"], "needs_retrieval_verification")
        collaboration = (output / "knowledge" / "02-collaboration-and-expression.md").read_text(encoding="utf-8")
        self.assertIn("evolution-completion-v1", collaboration)
        self.assertNotIn("Candidate text must remain", collaboration)
        self.assertEqual(json.loads((output / "audit" / "feedback-signals.json").read_text(encoding="utf-8"))[0]["scope"], "global")
        self.assertTrue((output / "audit" / "rule-evolutions.json").is_file())

    def test_completion_ladder_rejects_agent_only_publication_claim(self) -> None:
        source = self.root / "completion-source"
        write_jsonl(
            source / "completion.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "completion-session", "cwd": "/work/completion"}},
                visible("user", "Prepare a release candidate."),
                visible("assistant", "It is now publicly reachable everywhere."),
            ],
        )
        output = self.root / "completion-kb"
        self.build(source, output)
        review_path = output / "review" / "completion.json"
        review, events = self.prepare_review(output, review_path)
        assistant_event = next(event for event in events if event["role"] == "assistant")
        project = review["projects"][0]
        project["completion"] = {
            "level": "publicly-reachable",
            "status": "observed",
            "evidence_event_ids": [assistant_event["event_id"]],
            "rationale": "This deliberately tests that an Agent sentence cannot prove remote public availability.",
        }
        project["history"]["delivery_and_state"] = [
            {
                "text": "The Agent said the result was publicly reachable.",
                "status": "observed",
                "evidence_event_ids": [assistant_event["event_id"]],
            }
        ]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertTrue(any("observed publicly-reachable" in error for error in errors))
        self.assertTrue(any("Agent-only grade-C" in error for error in errors))

        project["completion"]["status"] = "agent-reported"
        project["history"]["delivery_and_state"][0]["status"] = "agent-reported"
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        _, _, errors = validate_review(output, review_path)
        self.assertEqual(errors, [])

    def test_incremental_no_change_preserves_verified_publication(self) -> None:
        source = self.root / "stable-source"
        write_jsonl(
            source / "stable.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "stable-session", "cwd": "/work/stable"}},
                visible("user", "Build the stable publication project."),
                visible("assistant", "Created the stable publication draft."),
            ],
        )
        output = self.root / "stable-kb"
        self.build(source, output)
        review_path = output / "review" / "stable.json"
        review, _ = self.prepare_review(output, review_path)
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        distill_review(output, review_path)
        project_key = review["projects"][0]["project_key"]
        verification = verify_retrieval(output, "stable publication project", "unrelated marine geology index", project_key)
        self.assertEqual(verification["status"], "passed")
        index_before = (output / "knowledge" / "knowledge-index.json").read_bytes()
        completion_before = json.loads((output / "audit" / "completion-report.json").read_text(encoding="utf-8"))

        result = self.build(source, output, incremental=True)
        completion_after = json.loads((output / "audit" / "completion-report.json").read_text(encoding="utf-8"))

        self.assertTrue(result["publication_preserved"])
        self.assertEqual(result["impact"]["review_required"], False)
        self.assertEqual((output / "knowledge" / "knowledge-index.json").read_bytes(), index_before)
        self.assertEqual(completion_after["run_id"], completion_before["run_id"])
        self.assertEqual(completion_after["status"], "complete")

    def test_old_memory_tool_result_stays_quarantined_across_incremental_tail(self) -> None:
        source = self.root / "old-memory-source"
        path = source / "old-memory.jsonl"
        write_jsonl(
            path,
            [
                {"type": "session_meta", "payload": {"id": "old-memory-session", "cwd": "/work/current"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "read_file",
                        "arguments": {"path": "/memory/facts.md"},
                        "call_id": "memory-call",
                    },
                },
            ],
        )
        output = self.root / "old-memory-kb"
        self.build(source, output)
        state = json.loads((output / "audit" / "state.json").read_text(encoding="utf-8"))
        source_state = next(iter(state["sources"].values()))
        self.assertEqual(len(source_state["isolated_call_hashes"]), 1)
        self.assertNotIn("memory-call", json.dumps(source_state))

        append_jsonl(
            path,
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "output": "PRIVATE OLD MEMORY FACT",
                        "call_id": "memory-call",
                    },
                }
            ],
        )
        self.build(source, output, incremental=True)

        self.assertNotIn("PRIVATE OLD MEMORY FACT", (output / "audit" / "events.jsonl").read_text(encoding="utf-8"))
        excluded = read_jsonl(output / "audit" / "excluded.jsonl")
        self.assertTrue(any(item.get("reason") == "old_memory_read_result" for item in excluded))

    def test_boundary_failure_preserves_state_then_recovery_replaces_old_events(self) -> None:
        source = self.root / "boundary-source"
        path = source / "boundary.jsonl"
        write_jsonl(
            path,
            [
                {"type": "session_meta", "payload": {"id": "boundary-session", "cwd": "/work/boundary"}},
                visible("user", "ORIGINAL FACT"),
            ],
        )
        output = self.root / "boundary-kb"
        self.build(source, output)

        stale_discovery = discover(self.registry, roots=[source], output_dir=output)
        stale_snapshot = freeze_sources(stale_discovery.sources)
        stale_snapshot["discovery"] = stale_discovery.to_dict()
        write_jsonl(
            path,
            [
                {"type": "session_meta", "payload": {"id": "boundary-session", "cwd": "/work/boundary"}},
                visible("user", "REPLACED FACT"),
            ],
        )
        failed = build_knowledge_base(
            self.registry,
            stale_discovery.sources,
            output,
            discovery=stale_discovery,
            snapshot=stale_snapshot,
            incremental=True,
        )
        self.assertGreater(failed["stats"].get("stale_source_states_preserved", 0), 0)
        failed_state = json.loads((output / "audit" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(failed_state["sources"]), 1)

        recovered = self.build(source, output, incremental=True)
        contents = [event["content"] for event in read_jsonl(output / "audit" / "events.jsonl")]
        self.assertNotIn("ORIGINAL FACT", contents)
        self.assertIn("REPLACED FACT", contents)
        self.assertGreater(recovered["impact"]["removed_event_count"], 0)

    def test_renamed_project_document_is_archived_not_left_as_current_knowledge(self) -> None:
        source = self.root / "rename-source"
        write_jsonl(
            source / "rename.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "rename-session", "cwd": "/work/rename"}},
                visible("user", "Create a dossier whose reviewed title may later be corrected."),
            ],
        )
        output = self.root / "rename-kb"
        self.build(source, output)
        first_path = output / "review" / "first.json"
        first, _ = self.prepare_review(output, first_path)
        first["projects"][0]["title"] = "Original dossier title"
        first_path.write_text(json.dumps(first, ensure_ascii=False, indent=2), encoding="utf-8")
        distill_review(output, first_path)
        first_manifest = json.loads((output / "audit" / "published-files.json").read_text(encoding="utf-8"))
        old_relative = first_manifest["project_paths"][0]
        self.assertTrue((output / "knowledge" / old_relative).is_file())

        second_path = output / "review" / "second.json"
        second = json.loads(json.dumps(first))
        second["projects"][0]["title"] = "Corrected dossier title"
        second_path.write_text(json.dumps(second, ensure_ascii=False, indent=2), encoding="utf-8")
        result = distill_review(output, second_path)
        stale = json.loads((output / "audit" / "stale-project-documents.json").read_text(encoding="utf-8"))

        self.assertEqual(result["stale_project_documents_archived"], 1)
        self.assertFalse((output / "knowledge" / old_relative).exists())
        self.assertEqual(stale[0]["status"], "archived")
        self.assertTrue((output / "knowledge" / stale[0]["to"]).is_file())
        current = json.loads((output / "audit" / "published-files.json").read_text(encoding="utf-8"))
        self.assertNotIn(old_relative, current["project_paths"])

    def test_stale_project_archive_rejects_symlink_escape(self) -> None:
        source = self.root / "archive-symlink-source"
        write_jsonl(
            source / "archive.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "archive-session", "cwd": "/work/archive"}},
                visible("user", "Create a dossier and keep private history inside this knowledge base."),
            ],
        )
        output = self.root / "archive-symlink-kb"
        self.build(source, output)
        first_path = output / "review" / "archive-first.json"
        first, _ = self.prepare_review(output, first_path)
        first["projects"][0]["title"] = "First private dossier"
        first_path.write_text(json.dumps(first, ensure_ascii=False, indent=2), encoding="utf-8")
        distill_review(output, first_path)
        prior = json.loads((output / "audit" / "published-files.json").read_text(encoding="utf-8"))
        old_document = output / "knowledge" / prior["project_paths"][0]

        outside = self.root / "outside-archive"
        outside.mkdir()
        os.symlink(outside, output / "knowledge" / "archive")
        second = json.loads(json.dumps(first))
        second["projects"][0]["title"] = "Second private dossier"
        second_path = output / "review" / "archive-second.json"
        second_path.write_text(json.dumps(second, ensure_ascii=False, indent=2), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "archive path contains a symlink"):
            distill_review(output, second_path)
        self.assertTrue(old_document.is_file())
        self.assertEqual(list(outside.iterdir()), [])

    def test_project_metadata_change_invalidates_semantic_reuse_even_when_event_ids_match(self) -> None:
        source = self.root / "metadata-source"
        path = source / "metadata.jsonl"
        write_jsonl(
            path,
            [
                {"type": "session_meta", "payload": {"id": "metadata-session", "cwd": "/work/one"}},
                visible("user", "Keep the visible message and locator unchanged."),
            ],
        )
        output = self.root / "metadata-kb"
        self.build(source, output)
        before = read_jsonl(output / "audit" / "events.jsonl")

        write_jsonl(
            path,
            [
                {"type": "session_meta", "payload": {"id": "metadata-session", "cwd": "/work/two"}},
                visible("user", "Keep the visible message and locator unchanged."),
            ],
        )
        result = self.build(source, output, incremental=True)
        after = read_jsonl(output / "audit" / "events.jsonl")

        self.assertEqual({event["event_id"] for event in before}, {event["event_id"] for event in after})
        self.assertNotEqual({event["project_key"] for event in before}, {event["project_key"] for event in after})
        self.assertGreater(result["impact"]["modified_event_count"], 0)
        self.assertTrue(result["impact"]["review_required"])
        self.assertFalse(result["publication_preserved"])

    def test_changed_published_file_invalidates_no_change_preservation(self) -> None:
        source = self.root / "manifest-source"
        write_jsonl(
            source / "manifest.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "manifest-session", "cwd": "/work/manifest"}},
                visible("user", "Build the manifest integrity project."),
            ],
        )
        output = self.root / "manifest-kb"
        self.build(source, output)
        review_path = output / "review" / "manifest.json"
        review, _ = self.prepare_review(output, review_path)
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        distill_review(output, review_path)
        project_key = review["projects"][0]["project_key"]
        self.assertEqual(
            verify_retrieval(output, "manifest integrity project", "unrelated deep ocean mineral", project_key)["status"],
            "passed",
        )
        index = json.loads((output / "knowledge" / "knowledge-index.json").read_text(encoding="utf-8"))
        project_path = next(item["path"] for item in index["documents"] if item.get("type") == "project")
        document = output / "knowledge" / project_path
        document.write_text(document.read_text(encoding="utf-8") + "\nmanual unverified edit\n", encoding="utf-8")

        result = self.build(source, output, incremental=True)
        completion = json.loads((output / "audit" / "completion-report.json").read_text(encoding="utf-8"))

        self.assertFalse(result["publication_preserved"])
        self.assertFalse(result["impact"]["previous_publication_manifest_valid"])
        self.assertEqual(completion["status"], "needs_semantic_review")
        self.assertFalse(completion["gates"]["published_knowledge"])

    def test_incremental_context_keeps_private_home_project_identity_stable(self) -> None:
        source = self.root / "context-source"
        path = source / "context.jsonl"
        private_cwd = str(Path.home() / "private-context-project")
        write_jsonl(
            path,
            [
                {"type": "session_meta", "payload": {"id": "context-session", "cwd": private_cwd}},
                visible("user", "FIRST CONTEXT EVENT"),
            ],
        )
        output = self.root / "context-kb"
        self.build(source, output)
        first_event = read_jsonl(output / "audit" / "events.jsonl")[0]

        append_jsonl(path, [visible("assistant", "SECOND CONTEXT EVENT")])
        self.build(source, output, incremental=True)
        events = read_jsonl(output / "audit" / "events.jsonl")
        state_text = (output / "audit" / "state.json").read_text(encoding="utf-8")

        self.assertEqual({event["project_key"] for event in events}, {first_event["project_key"]})
        self.assertEqual({event["working_dir"] for event in events}, {"~/private-context-project"})
        self.assertNotIn(str(Path.home()), state_text)

    def test_full_frozen_prefix_detects_middle_rewrite_before_append_reuse(self) -> None:
        source = self.root / "full-prefix-source"
        path = source / "large.jsonl"
        records = [{"type": "session_meta", "payload": {"id": "large-session", "cwd": "/work/large"}}]
        records.extend(visible("user", f"event-{index:04d}-" + "x" * 900) for index in range(360))
        write_jsonl(path, records)
        output = self.root / "full-prefix-kb"
        self.build(source, output)

        changed = json.loads(json.dumps(records))
        changed[180] = visible("user", "event-0179-" + "y" * 900)
        changed.append(visible("assistant", "new tail after middle rewrite"))
        write_jsonl(path, changed)
        result = self.build(source, output, incremental=True)
        contents = [event["content"] for event in read_jsonl(output / "audit" / "events.jsonl")]

        self.assertEqual(result["stats"].get("append_files_verified", 0), 0)
        self.assertGreater(result["stats"].get("reparsed_files", 0), 0)
        self.assertIn("event-0179-" + "y" * 900, contents)
        self.assertNotIn("event-0179-" + "x" * 900, contents)

    def test_transport_accounting_failure_persists_across_unchanged_incremental_run(self) -> None:
        source = self.root / "transport-source"
        write_jsonl(
            source / "transport.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "transport-session", "cwd": "/work/transport"}},
                visible("user", "kept transport event"),
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": []}},
            ],
        )
        output = self.root / "transport-kb"
        first = self.build(source, output)
        second = self.build(source, output, incremental=True)

        self.assertGreater(first["stats"]["transport_records_unaccounted"], 0)
        self.assertFalse(first["completion"]["gates"]["transport_accounted"])
        self.assertGreater(second["stats"]["transport_records_unaccounted"], 0)
        self.assertFalse(second["completion"]["gates"]["transport_accounted"])

    def test_incomplete_frozen_snapshot_is_not_consumable(self) -> None:
        path = self.root / "vanishing.jsonl"
        write_jsonl(path, [{"type": "session_meta", "payload": {"id": "vanishing", "cwd": "/work/vanishing"}}])
        stat = path.stat()
        source = SourceFile(path, path.parent, "codex-jsonl", "Codex", stat.st_size, stat.st_mtime_ns, stat.st_ino)
        path.unlink()
        snapshot = freeze_sources([source])
        snapshot_path = self.root / "incomplete-snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["requested_source_count"], 1)
        self.assertEqual(snapshot["source_count"], 0)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            load_snapshot(snapshot_path)

    def test_source_change_during_parse_is_discarded_not_accepted_as_new_state(self) -> None:
        source = self.root / "during-read-source"
        path = source / "during-read.jsonl"
        original_records = [
            {"type": "session_meta", "payload": {"id": "during-read", "cwd": "/work/during-read"}},
            visible("user", "ALPHA FACT"),
        ]
        changed_records = [
            {"type": "session_meta", "payload": {"id": "during-read", "cwd": "/work/during-read"}},
            visible("user", "BRAVO FACT"),
        ]
        write_jsonl(path, original_records)
        discovery = discover(self.registry, roots=[source])
        snapshot = freeze_sources(discovery.sources)
        snapshot["discovery"] = discovery.to_dict()
        original_parser = pipeline_module._parse_frozen_source
        changed = False

        def mutate_then_parse(adapter, source_file, start_offset, context):
            nonlocal changed
            if not changed:
                changed = True
                write_jsonl(path, changed_records)
            return original_parser(adapter, source_file, start_offset, context)

        output = self.root / "during-read-kb"
        with patch.object(pipeline_module, "_parse_frozen_source", side_effect=mutate_then_parse):
            result = build_knowledge_base(
                self.registry,
                discovery.sources,
                output,
                discovery=discovery,
                snapshot=snapshot,
            )

        event_text = (output / "audit" / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("BRAVO FACT", event_text)
        self.assertTrue(any(item.get("error") == "source_boundary_changed_during_read" for item in result["errors"]))
        self.assertFalse(result["completion"]["gates"]["parse_clean"])
        state = json.loads((output / "audit" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["sources"], {})


if __name__ == "__main__":
    unittest.main()
