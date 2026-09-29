# Generated-knowledge reconstruction

The pipeline and its completion states below apply to generated-knowledge work,
not automatically to a scoped Skill review or a human-page maintenance task.

Turn supported session archives into a private, maintainable knowledge base. Continue through semantic review and the v0.5 retrieval suite; deterministic extraction or an empty review template is not completion.

Python performs deterministic discovery, parsing, sanitization, hashing, accounting, lifecycle planning, rendering, and validation. The Agent performs semantic work: read the sanitized evidence, correct project membership, resolve actors, reconstruct intent and corrections, write evidence-bound histories, validate both endpoints of links, and govern lasting rules. Never describe one layer as having done the other's work.

## Load only the applicable reconstruction contracts

Read [references/execution-contract.md](execution-contract.md) before inventory and final reporting.

- Read [references/adapter-contract.md](adapter-contract.md) before adding, judging, or golden-testing a format.
- Read [references/distillation-method.md](distillation-method.md) and [references/review-contract.md](review-contract.md) before project regrouping or semantic review.
- Read [references/evolution-contract.md](evolution-contract.md) when evidence contains feedback or a proposed lasting behavior change.
- Read [references/lifecycle-contract.md](lifecycle-contract.md) before retracting, forgetting, or scheduling revalidation.
- Read [references/output-contract.md](output-contract.md) when consuming or changing generated artifacts.
- Read [references/public-release.md](public-release.md) before packaging or publishing the Skill pack.

## Choose a launcher

Use an available Python 3.9+ launcher appropriate to the environment. Replace `<python>` below with it. Only the standard library is required. If it is unavailable, explain the prerequisite and stop; do not install or upgrade software without permission.

## 1. Inventory without writing

```text
<python> scripts/session_kb.py inventory --json
```

Report separately:

1. installed or configured Agent candidates;
2. supported files grouped by exact adapter;
3. unknown or ambiguous structural fingerprints;
4. unreadable roots, filters, errors, and coverage gaps.

For an export or nonstandard location, repeat `--root PATH`. For a trusted exact source, use `--source ADAPTER=PATH`. Never turn a bounded scan into “every session everywhere was found.”

## 2. Ask where private knowledge should be stored

If the user supplied no path, run:

```text
<python> scripts/session_kb.py guide-output --name portable-name
```

Show the choices and wait for the path decision. Do not choose the Skill repository, a public repository, a source-session root, the filesystem root, or the entire user directory. Explain sync uploads, shared access, backup retention, device/disk encryption, and file-permission boundaries.

Path approval covers only the private output and snapshot. It does not authorize registration, upload, public release, source modification, software installation, or deletion elsewhere.

## 3. Freeze, dry-run, and rebuild

```text
<python> scripts/session_kb.py freeze --snapshot /approved/private/snapshot.json
<python> scripts/session_kb.py rebuild --snapshot /approved/private/snapshot.json --output /approved/private/kb --dry-run
<python> scripts/session_kb.py rebuild --snapshot /approved/private/snapshot.json --output /approved/private/kb --summary
```

Snapshot version 2 binds every requested source's frozen bytes with `frozen_sha256`. Any stat/hash error, incomplete denominator, duplicate source identity, or missing digest blocks consumption. Snapshot creation is exclusive and never overwrites an old denominator.

Inspect completion, statistics, dispositions, impact, unsupported formats, gaps, errors, exclusions, and compatibility output. The expected state is `needs_semantic_review`; extraction is not a completed knowledge base.

Use `--summary` for normal written rebuilds to avoid flooding model context with
every exclusion. It reports exact counts and private audit paths; inspect those
files as required. Omit it for full JSON compatibility. A dry-run summary writes
no details and cannot stand in for the full dry-run review.

Mutating commands use a fail-closed single-writer lock and atomic replacement. Inspect lock ownership before treating one as stale; never delete an active or ambiguous lock.

For later runs, freeze again and add `--incremental`. Only a verified append tail reaches the adapter parser, but the old prefix is still read for integrity and the actual-stream digest. Report parsed bytes separately from integrity bytes. A boundary or digest failure preserves prior verified source state. Recovery under a new consistent snapshot fully reparses and replaces that source's retained events and source-scoped audit rows.

## 4. Reconcile project membership before attesting histories

Create the proposal review first:

```text
<python> scripts/session_kb.py review-init --kb /approved/private/kb
```

Inspect proposed chains for false merges, false splits, renamed/moved work, and cross-Agent continuations. Evidence strength descends from explicit user intent, native continuation lineage, concrete shared artifacts/dependencies, observed handoffs/corrections, then path/title hints.

When correction is needed, prepare a private hash-bound membership plan and rerun:

```text
<python> scripts/session_kb.py review-init \
  --kb /approved/private/kb \
  --membership-plan /approved/private/project-membership-plan.json
```

The plan may `merge`, `split`, or `reassign` exact event subsets. It must cite evidence events inside the assignment and bind the global evidence set, each proposed source project, ordered event ids, selected semantic events, and rationale. The runtime rejects stale plans, duplicate assignment, foreign events, event loss, and malformed operations. Unassigned events keep their deterministic proposal; never guess membership for a new tail.

Use `review-packet --review REVIEW.json` after regrouping. Distillation publishes the canonical before/after partition and event-level diff to `audit/project-membership.json`. Follow the full schema and gates in [references/review-contract.md](review-contract.md).

## 5. Read every chain, with evidence-block reuse only after a full first pass

For a small chain, omit paging. For a long chain, request contiguous ranges:

```text
<python> scripts/session_kb.py review-packet \
  --kb /approved/private/kb \
  --review /approved/private/kb/review/review.json \
  --project-key PROJECT_KEY \
  --start-event 0 \
  --max-events 200
```

Copy each packet `receipt` into `reading_receipts`. Copy its `semantic_chunk`, add evidence-bound notes whose event ids stay inside that exact range, add the note hash, date, and attestation, then resume at `range.next_start_event`. Ranges must cover `0..event_count` without gaps or overlap. Search hits, summaries, high-signal lists, and truncated output do not count as reading.

Review version 4 binds complete canonical events, ordered ids, each chunk, and one-event adjacent boundaries. On `review-init --from-review PRIOR_REVIEW.json`:

- a fully identical reviewed project may carry as a whole;
- a changed project may reuse only v4 semantic chunks whose content, order, notes, and adjacent boundary hashes remain exact;
- changed, uncovered, and boundary-affected ranges reopen;
- free-text project conclusions from the old changed project do not carry;
- any chunk reuse requires a fresh `project_synthesis` over the complete current chain and a fresh cross-project recheck.

This saves repeated semantic reading, not full physical I/O, and never lets cached notes replace the current full-project synthesis.

## 6. Resolve actors, write histories, links, and feedback

Before native/unknown user events support first-person claims, add a bounded project-lane or exact-event `actor_attributions` record. Leave unresolved identity as `unknown_user`.

For every real project, fill objective, changes/corrections, actions/artifacts, validation/observations, failures/fallbacks, delivery/state, and remaining work. Every item needs chain-local event ids and an honest status. Fully read noise/test chains may be `reviewed-no-knowledge` with a reason.

Record evidence-supported completion per artifact and environment. `requested`, `designed`, `implemented`, `artifact-created`, `installed`, `enabled`, `invoked`, `automated-tests-passed`, `real-interaction-observed`, `user-accepted`, `submitted`, `merged`, `remotely-published`, and `publicly-reachable` are different states. The schema's summary level does not imply all other states; preserve mixed outcomes in history and rationale.

Validate relationships only after histories stabilize. Reopen both complete endpoint packets and cite both sides. Only confirmed edges become reciprocal Markdown navigation; the graph keeps true direction. Every published project must be `linked` or an explained `intentional-isolate`.

Record feedback at its actual object and scope. Use executable evolution operations when a lasting rule is proposed:

```text
<python> scripts/session_kb.py evolution-clusters --review REVIEW.json
<python> scripts/session_kb.py evolution-propose --kb /approved/private/kb --review REVIEW.json --proposal PROPOSAL.json
<python> scripts/session_kb.py evolution-queue --review REVIEW.json
<python> scripts/session_kb.py evolution-decide --kb /approved/private/kb --review REVIEW.json --evolution-id ID --decision approve --approval-event-id EVENT --promoted-claim-id CLAIM
<python> scripts/session_kb.py evolution-evaluate --kb /approved/private/kb --review REVIEW.json --evolution-id ID --result passed --baseline-event-id BEFORE --validation-event-id AFTER --observed-change "Observed bounded change"
```

Exact clustering does not perform semantic merging. A CLI flag does not manufacture user approval. Candidates and rejected changes stay inactive; only a validated change may be called evolved. When a required `cross_project_recheck` exists, an evolution mutation reopens it; review the changed current state and record a fresh checked-state hash instead of silently re-signing the old attestation. Read [references/evolution-contract.md](evolution-contract.md).

Validate before publication:

```text
<python> scripts/session_kb.py validate-review --kb /approved/private/kb --review REVIEW.json
```

Fix errors or preserve uncertainty. Never bypass the gate.

## 7. Distill and pass the v0.5 retrieval suite

```text
<python> scripts/session_kb.py distill --kb /approved/private/kb --review REVIEW.json
<python> scripts/session_kb.py verify-retrieval --kb /approved/private/kb --eval-set /approved/private/retrieval-eval.json
```

`distill` ends at `needs_retrieval_verification`. The private eval set needs at least two distinct related paraphrases and two distinct hard negatives after privacy cleanup. It has one suite-level `retrieval_profile` (`min_score`, `max_projects`, `max_related`) shared by every case; case-level overrides are invalid. Related cases may declare expected project keys/document types and `min_project_matches`. Every case must pass, and the suite/completion hashes bind the shared profile and expectations.

The verifier independently requires all prerequisite publication gates: `frozen_snapshot`, `transport_accounted`, `parse_clean`, `discovery_coverage_complete`, `semantic_review_complete`, `knowledge_graph_complete`, and `published_knowledge`. Unsupported candidates must additionally be clear or explicitly acknowledged. Reader and registration repeat the same prerequisite checks; no earlier successful caller substitutes for them.

The verification audit stores task hashes, redaction counts, bounded selection summaries, and a manifest of published knowledge, not task text or document bodies. Any later edit, replacement, deletion, retract, or forget invalidates the publication manifest and retrieval gates.

The legacy `--related-task` plus `--unrelated-task` pair remains a diagnostic interface. Current publications use retrieval contract 3, extending the v0.5 contract 2 with shared selection options and retrieval-engine binding. The pair may report `legacy_pair_passed_needs_suite` but cannot reach a final complete state under either contract. Compact-view suites must also demonstrate actual returned evidence units; matching a document alone is insufficient.

Only `complete` or `complete_with_unsupported_formats` is a normal final state. The latter preserves acknowledged unsupported inputs and does not upgrade them to compatible. Report denominators, exact formats, unknowns, host/system boundaries, privacy results, actor ambiguities, regrouping, chunk reuse, links, evolution state, lifecycle state, retrieval cases, errors/fallbacks, and remaining work.

## 8. Handle lifecycle requests as a separate, explicit workflow

When the user asks to retract, forget, set a retention/recheck date, or inspect due items, read [references/lifecycle-contract.md](lifecycle-contract.md). Plan first:

```text
<python> scripts/session_kb.py lifecycle-plan --kb /approved/private/kb --action forget --event-id EVENT --plan /approved/private/plan.json
```

Inspect matched/unmatched selectors, impact, and backup warning. `lifecycle-apply` without `--commit` remains dry-run. Apply only the exact unchanged plan after explicit confirmation:

```text
<python> scripts/session_kb.py lifecycle-apply --kb /approved/private/kb --plan /approved/private/plan.json --commit
```

`retract` keeps provenance but deactivates current conclusions. `forget` removes selected material from generated knowledge and writes content-free hashed tombstones so incremental reconstruction does not reintroduce it. `schedule` records dates; `lifecycle-due` reports due checks without claim bodies and never auto-deletes. None edits source sessions or guarantees removal from backups, sync history, caches, exports, or other devices.

A committed lifecycle change uses a recoverable transaction. Before any knowledge body changes, completion becomes fail-closed `lifecycle-applying`; Reader, registration, and rebuild/incremental entrypoints refuse while its journal exists. Stage only the final generated bytes, reject selected forgotten literals from forget staging, commit the content-free tombstone before body writes/deletes, and write final completion last. An interruption may resume only with the exact same plan; refuse a different plan, and remove the private journal/staging only after success. Never delete an ambiguous transaction manually to bypass recovery.

## 9. Offer the separate Reader

After all gates pass, ask whether to register the approved location:

```text
<python> scripts/session_kb.py register-kb --name portable-name --kb /approved/private/kb --default
```

Registration is a separate local write and needs confirmation. The Reader may instead use an explicit path. It rejects drafts, lifecycle-pending outputs, retrieval-suite failures, run mismatches, changed publication files, graph/index allowlist mismatches, and archive paths.

Every Reader query returns a non-persisted `usage_receipt` with run id, task hash, match status, selected project keys/document paths, query parameters, `verified_profile_used`, and publication-manifest hash. With no explicit threshold flags, Reader uses the verified suite profile. An override is reported as outside that verified profile and must not be presented as equivalent retrieval behavior. The receipt records what context was selected; it does not itself learn, approve, or mutate a rule.

## 10. Run golden and public-source gates

This section applies only when the task also changes or packages the public Skill
source. Ordinary reconstruction ends at its private publication/retrieval gates;
do not inspect or package a public repository merely because a private library
was updated. Installed Skills may not include the development fixtures below.

The repository's public inline synthetic manifest exercises all seven declared adapters without real data:

```text
<python> scripts/session_kb.py golden-check \
  --fixture-root tests/fixtures/golden/v1 \
  --manifest tests/fixtures/golden/v1/manifest.json
```

Synthetic success is regression evidence, not a new compatibility claim. Real samples and their manifests stay in a user-held private root, with isolated output outside the repository. A new compatibility claim still needs exact-format implementation, contract tests, and real-sample smoke evidence.

Before packaging or publishing:

```text
<python> agent-session-knowledge-rebuilder/scripts/session_kb.py release-check --root /path/to/skill-pack
```

Keep private deny terms in newline-delimited files outside the release root and pass them with `--deny-term-file`. Pair the automated gate with staging-set inspection and human review. The repository is source-visible under a noncommercial limited license; a privacy scan is never permission to publish private knowledge or sessions.
