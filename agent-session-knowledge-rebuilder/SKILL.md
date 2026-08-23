---
name: agent-session-knowledge-rebuilder
description: Rebuild and incrementally maintain an auditable personal, project, and collaboration knowledge base from supported Agent session archives. Use for read-only session inventory, normalization, privacy cleanup, complete-chain semantic review, evidence-bound relinking, feedback-governed rule evolution, publication, or retrieval verification. Report unknown formats and validation boundaries instead of claiming universal compatibility.
---

# Agent Session Knowledge Rebuilder

Turn supported session archives into a private, maintainable knowledge base. Execute the workflow through machine-verified retrieval; do not stop after extraction or hand an empty review file to the user.

The Python runtime performs deterministic discovery, parsing, sanitization, hashing, accounting, rendering, and validation. The Agent using this Skill performs semantic work: read every sanitized project chain, reconstruct intent and corrections, resolve actor identity, write evidence-bound histories, validate both endpoints of each relationship, and govern rule changes. Neither layer may impersonate the other.

## Load the applicable contracts

Read [references/execution-contract.md](references/execution-contract.md) before inventory and final reporting.

- Read [references/adapter-contract.md](references/adapter-contract.md) before adding or judging a format.
- Read [references/distillation-method.md](references/distillation-method.md) and [references/review-contract.md](references/review-contract.md) before semantic review.
- Read [references/evolution-contract.md](references/evolution-contract.md) when the evidence contains corrections, positive/negative feedback, gaps, outcomes, or proposed lasting rules.
- Read [references/output-contract.md](references/output-contract.md) when consuming or changing generated artifacts.
- Read [references/public-release.md](references/public-release.md) before packaging or publishing this Skill pack.

## Non-negotiable boundaries

- Keep source sessions read-only. Never delete, rewrite, move, repair, or upload them.
- Inventory the accessible environment before claiming coverage. An installed app, adapter framework, readable session format, execution host, and operating-system validation are different facts.
- Claim input support only for exact formats listed in [references/compatibility.md](references/compatibility.md). Report unknown candidates, ambiguous probes, unreadable locations, and bounded scan gaps.
- Freeze source sizes and boundary hashes before parsing. Do not chase later files or re-ingest the rebuild's own logs.
- Keep old-memory reads isolated across incremental boundaries. Never turn a delayed result from a prior memory-read call into current evidence.
- Preserve visible user/Agent messages, tool calls/results, patches, browser/device events, errors, fallbacks, and observed delivery. Quarantine compacted summaries, runtime injection, old-memory reads, imported or nested transcripts, hidden reasoning, and delegated wrappers.
- A native serialized `user` lane is provenance, not proof of the primary human. Resolve it semantically through `actor_attributions`; keep unknown users, customers, third parties, test actors, orchestrators, and subagents separate.
- Strip binary bodies and redact standard/Base64URL payloads, MIME-wrapped blocks including short terminal lines, parameterized data URLs, compound or generic credential assignments, authorization headers, Cookie headers/assignments/jars, URI userinfo, private keys, contacts, non-global network addresses, and current or foreign-machine home paths from every generated artifact.
- Do not infer identity, preference, causality, ownership, relationship, completion, or evolution from keywords or Agent claims. Published assertions require valid event ids and the applicable semantic gate.
- Keep generated knowledge, audits, reviews, registry data, real sessions, and real-sample outputs outside the public Skill source tree.

## Choose the launcher

Use an available Python 3.9+ launcher appropriate to the current environment. In the commands below, replace `<python>` with that launcher. Only the standard library is required. If no suitable runtime exists, explain the prerequisite and stop; do not install or upgrade software without permission.

## 1. Inventory without writing

```text
<python> scripts/session_kb.py inventory --json
```

The scan is heuristic and bounded. Report separately:

1. installed or configured Agent candidates;
2. supported session files by exact adapter;
3. unknown or ambiguous structural fingerprints;
4. errors, unreadable roots, filters, and coverage gaps.

For an export or nonstandard location, repeat `--root PATH`. For a trusted exact file/root, use `--source ADAPTER=PATH`. Never turn “all candidates inside the reported search boundary were checked” into “every session everywhere was found.”

## 2. Ask where private knowledge should be stored

If the user did not supply an output path, run:

```text
<python> scripts/session_kb.py guide-output --name portable-name
```

Show the choices and wait for the path decision. Do not choose the Skill repository, a public repository, a source-session root, the filesystem root, or the entire user directory. Explain that a cloud-synchronized location may upload private knowledge and preserve remote versions, while a shared location may expose it to other people or link holders. Confirm sync, sharing, backup retention, encryption, and file-access boundaries before writing there.

Path approval authorizes only the private knowledge output and snapshot. It does not authorize registry writes, uploads, publication, source changes, or software installation.

## 3. Freeze, dry-run, and rebuild

```text
<python> scripts/session_kb.py freeze --snapshot /approved/private/snapshot.json
<python> scripts/session_kb.py rebuild --snapshot /approved/private/snapshot.json --output /approved/private/kb --dry-run
<python> scripts/session_kb.py rebuild --snapshot /approved/private/snapshot.json --output /approved/private/kb
```

`freeze` creates snapshot version 2 with an exact `frozen_sha256` over every source's frozen bytes, plus the sampled boundaries and source denominator. If any source stat/hash fails, `freeze` writes no snapshot. A snapshot with errors, incomplete status, missing full digest, duplicate/mismatched source ids, or denominator mismatch is not consumable. Snapshot creation is exclusive and refuses an existing destination; use a new path instead of overwriting an old denominator.

Inspect the completion report, statistics, deterministic dispositions, impact report, unsupported formats, coverage gaps, errors, exclusions, and compatibility matrix. The expected first state is `needs_semantic_review`; deterministic extraction is not a completed knowledge base.

CLI knowledge-base mutation commands use a fail-closed single-writer lock and atomic file replacement. If a lock already exists, do not start a second writer or delete it reflexively. Inspect the recorded operation, process, and time; verify that no writer remains before treating it as stale. Coordinate any manual review-file editing separately.

For a later run, freeze again and add `--incremental`. For a verified append-only source, only bytes after the last complete offset are passed to the adapter parser, but the old prefix is still physically read for append integrity and the actual frozen-stream digest. Report `bytes_parsed`/`incremental_bytes_parsed` separately from `integrity_bytes_read`/`incremental_integrity_bytes_read`; never say the command physically reads only the tail.

The digest computed over bytes actually read must equal snapshot v2 `frozen_sha256`. Reject middle rewrites, change-during-read, and ABA-style attempts that merely restore size, mtime, inode, or sampled head/tail appearance. If a frozen boundary or stream digest fails, report a parse error and retain the previous verified source state instead of advancing it. Retry with a fresh consistent snapshot. Recovery full-reparse removes that source's retained old events and source-scoped audit rows before adding the newly parsed representation, so old and replacement versions are not blended.

State keeps irreversible `isolated_call_hashes` for quarantined old-memory tool calls. This lets a later append-only tail quarantine the matching tool result without storing the raw call id or memory content.

State also accumulates `transport_records_seen`, `transport_records_accounted`, and `transport_records_unaccounted` per source; run totals are recomputed from those source denominators so an unchanged incremental run cannot erase an old accounting gap. Its `project_identities` map binds an irreversible source/session marker to the prior sanitized project key/label, keeping later tails in the same project without storing the raw private working directory. If there is no event, unsupported-format, source-denominator, policy, or coverage change—and the prior publication manifest still matches—a previously complete publication may be preserved. Otherwise affected semantic conclusions reopen.

## 4. Initialize or continue semantic review

For a first review:

```text
<python> scripts/session_kb.py review-init --kb /approved/private/kb
```

For an incremental review, explicitly seed it from the prior review:

```text
<python> scripts/session_kb.py review-init \
  --kb /approved/private/kb \
  --from-review /approved/private/kb/review/PRIOR_REVIEW.json
```

New reviews use review version 3. Their global and per-project `event_set_sha256` values hash each complete canonical unified-event record; `event_ids_sha256` separately proves ordering. `--from-review` carries only projects whose full semantic event hash/count matches and whose evidence remains valid. Changed projects remain unreviewed. When any project is carried, `cross_project_recheck.required` is true. After comparing carried and changed chains, set `completed: true`, `reviewed_at`, and a concrete `rationale`; validation rejects a carried review without them. An unchanged endpoint does not make a relationship valid when the other endpoint changed.

### Read every chain with native checkpoints

For a small chain, omit paging options. For a long chain, use bounded contiguous ranges:

```text
<python> scripts/session_kb.py review-packet \
  --kb /approved/private/kb \
  --project-key PROJECT_KEY \
  --start-event 0 \
  --max-events 200
```

Copy the returned `receipt` object into that project's `reading_receipts`, then resume from `range.next_start_event`. For every range, verify the previous/next event ids, cumulative count, slice boundary, project event count, `event_ids_sha256`, and full semantic `event_set_sha256`. Receipts must cover event indexes `0..event_count` contiguously without overlap. Validation checks each receipt's project hash, slice hash, first/last event ids, and final coverage. Search hits, summaries, high-signal lists, and truncated output do not satisfy complete-chain reading.

### Reconcile actors before promoting first-person claims

Inspect every project user lane. When the deterministic event has `native_user` or `unknown_user`, add an `actor_attributions` entry for the project lane or exact event before using it as primary-user evidence. Attribution needs a bounded actor kind, permitted semantic basis, evidence event ids, and rationale. If identity is unresolved, keep `unknown_user`; do not publish identity, preference, approval, or feedback as belonging to the primary user.

### Write granular histories and completion states

For every real project, fill all applicable history sections: objective, changes/corrections, actions/artifacts, validation/observations, failures/fallbacks, delivery/state, and remaining work. Every item needs chain-local evidence ids and an honest status. Mark fully read noise/test-only chains `reviewed-no-knowledge` with a reason.

Record the highest supported completion level separately: requested, designed, implemented, artifact-created, installed, enabled, invoked, automated-tests-passed, real-interaction-observed, user-accepted, submitted, merged, remotely-published, publicly-reachable, or unknown. A higher label needs matching evidence; Agent prose alone is `agent-reported`, not an observation.

### Record feedback without inventing a permanent rule

Use `feedback_signals` for primary-user positive/negative/gap feedback or primary-user/observable outcome evidence. Record its project, object, scope, statement, evidence, status, and exact applicability.

Use `rule_evolutions` only when an observed feedback record motivates a versioned rule candidate. Candidates and rejected changes never activate a rule. Approved changes need an existing confirmed claim with the same scope plus semantically attributed approval evidence. Cross-project repeated context can establish a bounded confirmed rule, but it does not become an approved evolution without the approval gate. A global change needs two observed-feedback project contexts or explicit global approval. Only `validated`, with distinct baseline and later evidence, a passed behavior check, and observable or primary-user after-evidence, may be described as evolved. Follow [references/evolution-contract.md](references/evolution-contract.md).

## 5. Validate evidence-bound relationships

After project histories stabilize, compare objectives, corrections, artifacts, dependencies, handoffs, and contradictions.

1. Nominate candidates from meaning and user intent, not lexical overlap.
2. Reopen the complete packet for both endpoints.
3. Record direction, precise relation, evidence basis, confidence, rationale, and event ids from both chains.
4. Use `confirmed` only when both endpoints support it. Preserve insufficient candidates as `uncertain` and disproved candidates as `rejected`.
5. Set every published project to `linked` or `intentional-isolate`; an isolate needs a reason.

Only confirmed edges become reciprocal Markdown navigation. The graph keeps the true direction; the backlink does not reverse causality.

```text
<python> scripts/session_kb.py validate-review --kb /approved/private/kb --review /approved/private/kb/review/REVIEW.json
```

Fix validation failures or preserve the uncertainty. Never bypass the gate.

## 6. Distill, then pass both retrieval gates

```text
<python> scripts/session_kb.py distill --kb /approved/private/kb --review /approved/private/kb/review/REVIEW.json
<python> scripts/session_kb.py verify-retrieval \
  --kb /approved/private/kb \
  --related-task "a task that must retrieve reviewed project knowledge" \
  --unrelated-task "a task that must return no_match" \
  --expected-project-key PROJECT_KEY
```

`distill` ends at `needs_retrieval_verification`. `verify-retrieval` writes a content-free audit: it stores task hashes, selection summaries, and a manifest hash of the published knowledge files, not the task or document contents. The relevant task must select reviewed project knowledge, and the unrelated task must return `no_match`. Failure keeps the completion gates closed. Editing, replacing, or deleting a published knowledge file after verification invalidates the manifest and requires distillation/reverification as appropriate.

During distillation, `audit/published-files.json` records the current generated document paths. A project document present in the prior ledger but absent from the new reviewed publication—such as after a title/path change—is moved to `knowledge/archive/<run-id>/` and recorded in `audit/stale-project-documents.json`. Reject a symlink at the stale source, archive root, run directory, destination, or any destination containment check. Archived files are private history, not current indexed knowledge; do not use or link them as current Reader context.

Only `complete` or `complete_with_unsupported_formats` is a normal final state. The latter preserves acknowledged unsupported inputs; it never upgrades them to compatible. At handoff, report the frozen and semantic denominators, exact input formats present, unknown candidates, scan/host/system boundaries, privacy results, actor ambiguities, carry-forward scope, relationships, evolution state, retrieval checks, errors/fallbacks, and remaining work.

## 7. Offer the separate Reader

After all gates pass, ask whether to register the approved location for `$agent-knowledge-reader`. Registration is a separate local write:

```text
<python> scripts/session_kb.py register-kb --name portable-name --kb /approved/private/kb --default
```

Do not register without confirmation. The Reader can use an explicit path without registration and must reject incomplete retrieval gates.

`register-kb` uses a standalone sibling lock for the registry file and an atomic replacement, separate from the knowledge-base lock. If that lock exists, inspect ownership before treating it as stale; do not run concurrent registry writers.

The Reader resolves the graph from the published index's `graph.path` when supplied; it never substitutes another on-disk graph. It verifies graph `semantic_status`, graph/index run id, safe unique indexed document paths, exact equality between graph document nodes and the index document allowlist, safe `document_path` references, and existing edge endpoints. Archive paths are rejected at path resolution and never traversed. The retrieval manifest uses the same validated graph loader.

## 8. Public source gate

Before packaging or publishing the Skill pack:

```text
<python> agent-session-knowledge-rebuilder/scripts/session_kb.py release-check --root /path/to/skill-pack
```

For private identifiers, prefer a newline-delimited file outside the release root with repeated `--deny-term-file /private/check-terms.txt`; it avoids placing the terms in committed files and reduces command-line history exposure. `--deny-term` remains available for controlled use. A pass must still be paired with staging-set inspection and human review. This repository is source-visible under a noncommercial limited license; never call that privacy scan permission to publish private knowledge or real sessions.
