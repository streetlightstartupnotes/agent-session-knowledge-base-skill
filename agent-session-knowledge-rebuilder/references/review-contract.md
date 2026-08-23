# Semantic review and publication contract

Use this reference after deterministic rebuild and before `distill`. Transport coverage cannot decide speaker identity, durable preference, real-project membership, completion, relationship meaning, or whether feedback changed future behavior. Those decisions remain hash-bound semantic review.

## Initialize the exact evidence run

`review-init` creates review version 4. It records the run id and raw event count, hashes complete canonical unified-event records globally and per effective project in `event_set_sha256`, and separately fixes each project's event order in `event_ids_sha256`. It also records the proposed and effective project partitions. Do not edit generated hashes or membership audit fields. A stable event id alone cannot preserve a review when another semantic event field changes.

## Correct project membership before review attestation

The deterministic partition is a proposal. When semantic reading finds a false merge, false split, moved project, or cross-Agent continuation, create a private membership input:

```json
{
  "project_membership": {
    "override_version": 1,
    "basis_event_set_sha256": "the current raw review event_set_sha256",
    "assignments": [
      {
        "override_id": "membership-stable-id",
        "operation": "merge|split|reassign",
        "target_project_key": "project:stable-key",
        "rationale": "Why these exact events form this project",
        "evidence_basis": ["explicit-user-intent"],
        "evidence_event_ids": ["evt-inside-this-assignment"],
        "evidence_event_set_sha256": "semantic hash of those evidence events",
        "sources": [
          {
            "proposed_project_key": "project:proposal",
            "proposed_project_event_set_sha256": "full proposed-project hash",
            "event_ids": ["evt-in-proposed-order"],
            "event_ids_sha256": "ordered id hash",
            "event_set_sha256": "semantic hash of the selected events"
          }
        ]
      }
    ]
  }
}
```

Permitted evidence bases are `explicit-user-intent`, `continuation-lineage`, `objective-boundary`, `shared-artifact`, `observed-handoff`, `correction`, and `role-context-review`. At least one evidence event must be user-lane or observable grade A/B evidence inside the assigned subset.

`merge` needs at least two proposed source projects. `split` needs a strict subset of a proposed project; use enough assignments to give each intended target its events. `reassign` needs one proposed source and a different target key. Unlisted events keep their proposal. The runtime rejects duplicate event assignment, order changes, foreign events, stale global/source/selection hashes, and any loss or duplication.

Run `review-init --membership-plan PLAN.json`, then use `review-packet --review REVIEW.json`. The generated `project_membership` object becomes canonical: it contains proposal/result denominators, assignment hashes, event-level diff, and derived merge/split/reassign operations. Do not hand-edit that output. Any membership change requires `cross_project_recheck`, even without prior semantic carry-forward.

For an incremental run, use:

```text
<python> scripts/session_kb.py review-init \
  --kb /approved/private/kb \
  --from-review /approved/private/kb/review/PRIOR_REVIEW.json
```

A full-semantic-hash-identical v4 reviewed project, its valid reading receipts, and still-valid evidence records may carry forward. Changed projects remain `unreviewed`, but exact semantic chunks may be reused as described below. v2/v3 reviews remain directly inspectable but their older receipts are not silently upgraded to v4 carry-forward evidence. The carry summary lists invalidated/partially reused project keys and requires this machine-checked record after reviewing changes:

```json
{
  "cross_project_recheck": {
    "required": true,
    "completed": true,
    "reviewed_at": "ISO-8601 timestamp",
    "rationale": "What was compared across carried and changed chains, and the result"
  }
}
```

Recheck project membership, relationships, repeated-context claims, conflicts, supersession, global rules, approvals, and validation evidence that cross project boundaries. Bind `checked_state_sha256` to the final current review state. Validation rejects reuse when completion, date, rationale, or the checked-state hash is missing or stale. Evolution mutation commands reopen this attestation; they do not silently re-sign it.

## Read complete chains, including long ones

For a small chain:

```text
<python> scripts/session_kb.py review-packet --kb /approved/private/kb --project-key PROJECT_KEY
```

For a long chain:

```text
<python> scripts/session_kb.py review-packet \
  --kb /approved/private/kb \
  --project-key PROJECT_KEY \
  --start-event 0 \
  --max-events 200
```

Every v4 `review-packet` returns a receipt with project key, full project semantic hash, range bounds, slice event-id and semantic hashes, a hash that includes one adjacent event on each available side, boundary ids, and reading mode. It also returns a stable `semantic_chunk` for the exact range. Copy the receipt unchanged into the matching project's `reading_receipts`, then resume from `range.next_start_event`.

Receipts must cover indexes `0..event_count` contiguously without overlap. Verify previous/next event ids, cumulative `returned_event_count`, total `event_count`, ordered `event_ids_sha256`, full semantic `event_set_sha256`, slice semantic hash, and adjacent-boundary hash. Validation recomputes every receipt and rejects a missing range, overlap, wrong project hash, slice mismatch, boundary mismatch, or incomplete final coverage. A search result, summary, high-signal subset, or truncated response never satisfies reviewer attestation.

### Build reusable semantic chunks during the first full read

Copy the packet's `semantic_chunk`, then add:

- one or more bounded notes whose evidence ids stay inside that exact chunk;
- each note's `source_note_sha256`, computed over the note without that hash field;
- `reviewed_at` and an attestation that this exact range was read.

Every chunk binds project key, start/end, selected event ids, full selected event semantics, first/last ids, previous/next ids, and the adjacent-boundary hash. Chunks may not overlap. A free-text note without in-chunk evidence and its note hash is not reusable.

Use the runtime helpers `session_kb.review.semantic_note_sha256(note)`, `project_synthesis_sha256(project)`, and `cross_project_state_sha256(review)` after the corresponding objects are otherwise final. Do not hash only the prose or copy a digest from an older object.

On an incremental v4 review, a changed project may carry only chunks whose exact range and adjacent boundary still match. A new tail typically invalidates the old last chunk's next-boundary context while allowing earlier exact chunks to survive. The template writes the uncovered or affected complements into `project_synthesis.reopened_ranges`; read all of them fresh and add fresh receipts.

Any partial chunk reuse requires `project_synthesis.status: completed`, `mode: fresh`, the current project event hash, current `reused_chunk_ids`, a date, attestation, rationale, and `synthesis_sha256` over every publishable current conclusion. Old history/free-text synthesis is not copied from a changed project. Reused notes reduce repeated reading but never replace a fresh full-project synthesis.

Each project ends in one semantic state:

- `reviewed`: the full chain was read and at least one evidence-bound history item remains;
- `reviewed-no-knowledge`: the full chain was read and is noise, test-only, greeting-only, duplicated transport, or otherwise unsuitable; include the reason;
- `unreviewed`: publication is blocked.

The default semantic disposition covers every event unless an exact `event_exceptions` entry overrides it. Publication expands the decision into `audit/semantic-dispositions.jsonl`.

## Attribute actors before first-person promotion

The deterministic `role: user` and `actor_kind: native_user` fields describe a storage lane, not the primary person. Use `actor_attributions` before those events support identity, direction, collaboration, expression, approval, or human feedback.

Each attribution needs:

- a unique `actor-*` id;
- scope `project-user-lane` or `event`;
- project key and, for event scope, exact event id;
- actor kind: `primary_user`, `unknown_user`, `customer`, `third_party`, `test_actor`, `orchestrator`, or `subagent`;
- one or more permitted semantic bases, evidence event ids, and a bounded rationale.

Project-lane attribution applies only to native/unknown user events in that project. Exact-event attribution overrides it. Leave unresolved material as `unknown_user`; never convert uncertainty into grade-A evidence.

## Reconcile real project membership

Deterministic project keys are routing proposals. Before setting `reviewed`, inspect explicit user intent, native session/parent/continuation ids, stable project ids, concrete artifacts, dependencies, corrections, and handoffs. Working directory, title, file proximity, broad type, and keyword overlap are hints only.

Changing a title or alias does not move events. If a false merge or split would make the history misleading and the schema cannot express the correction, keep the affected project unreviewed and block publication. A graph relationship cannot repair wrong membership.

## Write histories and the completion ladder

Use every applicable history section:

1. `objective`;
2. `changes_and_corrections`;
3. `actions_and_artifacts`;
4. `validation_and_observations`;
5. `failures_and_fallbacks`;
6. `delivery_and_state`;
7. `remaining_work`.

Every item needs a bounded statement, chain-local event ids, and an honest status such as `observed`, `agent-reported`, `inferred`, `disputed`, `stale`, `retracted`, or `unverified`. Optional `before`, `after`, and `applies_to` fields preserve a correction without generalizing it.

Each project also records one highest completion level:

```text
requested -> designed -> implemented -> artifact-created -> installed
-> enabled -> invoked -> automated-tests-passed -> real-interaction-observed
-> user-accepted -> submitted -> merged -> remotely-published
-> publicly-reachable
```

Use `unknown` when no safe level exists and `not-published` for reviewed-no-knowledge projects. Store status, evidence ids, and a rationale explaining both the supported layer and what it does not prove. Observable higher levels need matching primary-user or grade-B evidence; Agent-only prose stays `agent-reported`.

## Publish base claims conservatively

A base claim uses a stable `claim-*` id, knowledge type, primary subject where applicable, statement, status, confidence, evidence ids, observation date/range, applicability, conflicts, and superseded claims.

- Confirmed identity and direction claims require semantically attributed primary-user evidence.
- Confirmed collaboration and expression rules require primary-user evidence, a bounded `rule_scope`, and `derivation` of `direct-explicit-rule`, `repeated-context`, or `feedback-promotion`.
- `repeated-context` requires supporting evidence from at least two project chains.
- A correction strongly proves the rejected interpretation; it does not prove every replacement.
- Keep disputed, stale, and retracted claims visible rather than smoothing them away.
- Dated session evidence does not make changing external facts current.

## Govern feedback and rule evolution

Use `feedback_signals` to preserve the actual positive, negative, gap, or outcome evidence, its object, scope, applicability, and project. Human feedback requires semantically attributed primary-user evidence; outcomes may also use observable grade-B evidence.

Use `rule_evolutions` to record candidate, approved, validated, or rejected versioned changes. A candidate cannot activate a claim. Approved changes require at least one observed feedback signal, a confirmed claim with matching scope, and primary-user approval evidence; a global evolution also needs observed feedback from two project contexts or explicit global approval. Validated changes additionally need distinct baseline and later evidence, a passed behavior check, validation event ids, and the observed behavior change. Only `validated` may be called evolved. Read [evolution-contract.md](evolution-contract.md) for the executable state machine.

## Validate relationships from both endpoints

Every reviewed project uses `link_analysis.status` of `linked` or `intentional-isolate`; reviewed-no-knowledge uses `not-published`. An isolate needs an evidence-bounded reason.

A relationship records a stable id, two different project keys, precise relation, direction, status, confidence, rationale, permitted evidence basis, and event ids from both endpoints. Permitted bases are explicit user intent, continuation lineage, shared artifact, dependency, correction, contradiction, and observed handoff.

Even uncertain and rejected candidates need evidence from both chains so the audit explains the comparison. Confirmed edges may connect only published projects. Lexical similarity, filename, title, broad type, and directory proximity are never evidence bases.

Only confirmed relationships become reciprocal Markdown navigation. Directed semantics stay directed in the graph; a backlink does not reverse them. Uncertain and rejected candidates remain in the audit.

## Attest, validate, distill, and verify retrieval

Reviewer id, timestamp, and attestation are required. The attestation means every ordered event in every full-semantic-hash-bound project chain was read and every range receipt was recorded, and unsupported candidates were inspected and acknowledged. Acknowledgement never changes support status.

```text
<python> scripts/session_kb.py validate-review --kb /approved/private/kb --review /approved/private/kb/review/REVIEW.json
<python> scripts/session_kb.py distill --kb /approved/private/kb --review /approved/private/kb/review/REVIEW.json
<python> scripts/session_kb.py verify-retrieval \
  --kb /approved/private/kb \
  --eval-set /approved/private/retrieval-eval.json
```

`distill` ends at `needs_retrieval_verification` and sets retrieval contract 2. The private eval set must contain at least two distinct `related` cases and two distinct `hard_negative` cases after privacy cleanup. It has exactly one shared `retrieval_profile` containing `min_score`, `max_projects`, and `max_related`; a case that tries to override any of them is invalid. Related cases may require project keys, document types, and `min_project_matches`. Hard negatives must declare no expected match and return `no_match`. The suite hash binds the shared profile and case expectations, including `min_project_matches`; weakening either changes suite identity. The audit stores hashes and summaries, not task text.

The legacy `--related-task`/`--unrelated-task` pair remains diagnostic. It cannot complete a v0.5 retrieval-contract-2 publication even when both cases pass. Do not register or use the Reader while status is draft, semantically incomplete, lifecycle-pending, merely distilled, retrieval-suite-unverified, or run-id mismatched.
