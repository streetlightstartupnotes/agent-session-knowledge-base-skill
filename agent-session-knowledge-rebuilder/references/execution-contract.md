# Execution and truth-reporting contract

Use this reference before inventory and again before reporting completion. It governs how an Agent executes the Skill; format schemas remain in the other references.

## What automation means

The Python runtime performs deterministic discovery, parsing, sanitization, hashing, accounting, validation, rendering, and retrieval. The executing Agent performs semantic reading and judgment. It must continue through that work itself instead of returning a review template to the user.

Do not describe the Python command alone as an intelligent reconstruction. Do not describe an Agent-written review as deterministic. The combination is the product.

## Keep three compatibility axes separate

1. **Source-format compatibility** means an exact session format has a verified adapter and real-sample smoke evidence.
2. **Execution-host compatibility** means a particular Agent product can discover, load, and carry out this Skill's semantic workflow.
3. **Operating-system validation** means the complete workflow has run in a real environment on that operating system.

The portable Python CLI does not prove native Skill discovery in every Agent. Automated Windows, macOS, and Linux path tests do not prove real end-to-end operation on all three systems. State each axis separately in inventory and final reports.

## Decisions and authority

Continue autonomously once the source scope and output path are known. Do not ask the user to choose parsing details, evidence grades, filenames, link labels, or ordinary review tactics.

Pause only when:

- the private output location has not been confirmed;
- a missing root, export, credential, or inaccessible account prevents further read-only evidence collection;
- two interpretations would materially change the knowledge base and the evidence cannot resolve them;
- installation, publication, deletion, source modification, or another action requires new authority.

An output-path confirmation authorizes writes only to that private output and its optional snapshot location. It does not authorize source changes, registry writes, software installation, uploads, or public release.

Before accepting a synchronized or shared output path, explain the material boundary: synchronization may upload private knowledge and retain remote versions; sharing may expose it to workspace members or link holders. Confirm that the user intends that storage and visibility. Never infer consent from the mere presence of a sync client or shared folder.

## Single-writer boundary

CLI commands that mutate a knowledge base acquire a sibling lock and replace files atomically. Registration uses a separate sibling lock for the standalone registry file, so concurrent registration cannot overwrite another entry update. A concurrent writer must fail closed. When a lock is present, inspect its operation, process id, target kind, and creation time as applicable; verify the owning process is gone before removing a stale lock. Never treat a busy or ambiguous lock as permission to continue in parallel. Manual review-file editing remains an external write and must be coordinated separately.

`freeze` emits only complete snapshot version 2 manifests. Every requested source needs a full frozen-byte `frozen_sha256`; source counts and discovery denominator must match. If any stat/hash fails, the CLI writes no snapshot. The loader and rebuild reject legacy, incomplete, errored, digest-missing, duplicate-identity, or denominator-mismatched snapshots. Snapshot creation is exclusive and never overwrites an existing file.

## Coverage ledger

Inventory is an evidence-building pass, not a filesystem oracle. Record:

- every root requested or automatically probed;
- whether it existed and was readable;
- whether it was a known, generic, environment-supplied, or explicit root;
- candidate, scanned, supported, unknown, ambiguous, and unreadable counts;
- any file, depth, suffix, directory, permission, account, mount, or `--max-files` boundary that constrained discovery.

“All accessible sessions” means all supported candidates inside this declared denominator. It never means encrypted stores, another account, unmounted disks, vendor cloud history, or formats hidden outside the reported search contract.

When the user asks for niche Agents, inspect accessible installed-command evidence and platform application/configuration roots, then accept explicit roots or exports. Do not install an Agent, bypass access controls, scrape another account, or upload private samples merely to improve coverage.

## Project-chain reconciliation

Deterministic `project_key` values are routing proposals. Before semantic attestation, compare the proposed chains using, in descending strength:

1. explicit user statements that one effort continues, replaces, or belongs to another;
2. native session, parent, continuation, or stable project identifiers;
3. a concrete shared artifact, repository, output, dependency, correction, or observed handoff;
4. working directory and titles only as supporting hints.

Never merge solely by directory, title, broad type, or keyword overlap. Never split a continuation solely because its path or Agent changed. If the current evidence/review structure cannot faithfully represent a required merge or split, do not attest or publish that project set. Report the grouping blocker instead of using a graph link to disguise it.

## Actor reconciliation

Serialized roles describe transport lanes, not human identity. `native_user` means the provider's ordinary user lane and still requires semantic attribution before it can support primary-user identity, preference, approval, or feedback. Use `actor_attributions` at the project-user-lane or exact-event scope, cite the evidence for the attribution, and keep unresolved speakers as `unknown_user`. Imported material, customers, third parties, test actors, orchestrators, and subagents must not inherit the primary user's identity.

## Long-chain reading protocol

A new review binds both the ordered event-id hash and a full semantic hash of each canonical unified-event record. A packet is complete only when its count and both hashes match the review template. If a tool or context window truncates it:

1. keep the complete packet or evidence file inside the approved private knowledge-base area;
2. call `review-packet --start-event N --max-events K` for consecutive, non-overlapping ranges in source order;
3. copy each returned `receipt` into the project's `reading_receipts`;
4. checkpoint `next_start_event`, previous and next event ids, cumulative returned count, ordered-id hash, and full event-set hash;
5. resume from the exact checkpoint after interruption;
6. mark the project reviewed only after receipts cover `0..event_count` without gaps/overlap and the final count/hash comparison passes.

Search, keyword ranking, high-signal event lists, and summaries may route attention. They never replace the surrounding ordered events or satisfy the attestation.

## Incremental semantic reuse

Use `review-init --from-review PRIOR_REVIEW.json` only after the new frozen evidence run exists. The command may carry full-semantic-hash-identical reviewed projects, their valid reading receipts, and evidence records; changed chains remain unreviewed. After any carry-forward, complete the machine-checked `cross_project_recheck` across relationships, repeated-context claims, conflicts, supersession, global scope, and feedback-governed rules. An unchanged project hash proves only that chain's events stayed unchanged.

## Incremental transport recovery

For append-only reuse, pass only the new tail to the adapter parser, but read the old prefix for integrity verification and for a digest of the actual frozen stream. Keep `bytes_parsed` distinct from `integrity_bytes_read`, including their incremental counters. Do not report parser savings as equivalent physical I/O savings.

Bind the actual-read digest to snapshot `frozen_sha256`. A different middle, a change during reading, or an ABA-style return to matching metadata/sampled boundaries must fail when the complete digest differs. Persist only the last verified source boundary and parser context. When verification fails, record the error and preserve the prior source state; do not advance the cursor to an unverified boundary. Retry from a fresh snapshot. On the recovery run, a full reparse must remove the retained events and source-scoped audit rows for that source before adding its replacement representation.

For old-memory tool reads, state may retain only irreversible call-id hashes. Use them to quarantine a matching tool result that arrives in a later append-only tail. Never persist the raw call id or old-memory content merely to maintain that linkage.

Accumulate transport seen/accounted/unaccounted counts per source in state and derive run totals from the full source-state set. An unchanged or missing-source incremental pass must not reset an earlier accounting gap.

Keep project grouping stable across a sanitized incremental parser context through a `project_identities` map keyed by an irreversible source/session marker. Its values are the already derived project key and sanitized label. The raw working directory may participate in the initial in-memory hash but must not be persisted; distinct private accounts must not collapse merely because their display paths redact to the same placeholder.

## Current versus archived project documents

Before a reviewed publication replaces the document set, compare it with `audit/published-files.json`. Move prior generated project paths absent from the new publication into `knowledge/archive/<run-id>/` and record every archived or already-missing path in `audit/stale-project-documents.json`. Reject symlinks at the stale source, archive root, run directory, or destination, and require the resolved destination to remain under the knowledge root. Archived documents remain private historical artifacts and are excluded from the current index and normal Reader retrieval.

## Progress and stopping conditions

For a long run, report concise evidence-based progress: current gate, frozen denominator, projects and events semantically reviewed, exact checkpoint for the active chain, errors/fallbacks, and the next boundary. Do not give an unsupported completion percentage or time estimate.

Stop publication when deterministic coverage, parse accounting, project grouping, complete-chain reading, claim provenance, privacy, or graph validation is unresolved. An explicit unsupported or unresolved result is valid output; a plausible invented bridge is not.

## Definition of done

A normal successful run has:

- a complete snapshot-v2, full-digest-bound, read-only source denominator;
- cumulative per-source transport records with explicit dispositions, exclusions, and errors;
- complete semantic dispositions for every retained event;
- semantic attribution for every native/unknown user lane used as primary-user evidence;
- evidence-bound project histories and base claims;
- confirmed, uncertain, rejected, or intentional-isolate relationship outcomes;
- evidence-bound feedback/rule records, with only behavior-validated changes described as evolved;
- a completed cross-project recheck after semantic carry-forward;
- published knowledge whose indexed graph/status/run/document allowlist passes validation and excludes archives;
- a machine-verified relevant retrieval match and unrelated `no_match`;
- a final report that preserves unsupported formats, operating-system and host limits, privacy results, fallbacks, and remaining work.

`complete_with_unsupported_formats` means supported evidence was published after the unsupported candidates were inspected and acknowledged. It never upgrades those candidates to compatibility.
