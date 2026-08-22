# Semantic review and publication contract

Use this reference after deterministic rebuild and before `distill`.

## Why review is a separate gate

Transport coverage answers whether bytes and records were handled. It cannot decide whether first-person text belongs to the primary user, whether an Agent completion claim is true, whether two sessions are one project, or whether repeated feedback is a durable rule. `rebuild` therefore emits evidence and a draft index; `distill` accepts only a matching, fully reviewed project set.

## Project hashes

`review-init` records the run id, complete event-set hash, and each project's ordered event count/hash. Do not edit those fields. Any incremental change makes the old review stale and forces a new review template for affected evidence.

Every project uses one semantic status:

- `reviewed`: the complete chain was read and at least one evidence-bound history item is retained;
- `reviewed-no-knowledge`: the complete chain was read and is greeting-only, test, noise, duplicated transport, or otherwise unsuitable for maintained knowledge; include a reason;
- `unreviewed`: publication is blocked.

The project default disposition applies to every event. Use exact event exceptions when an event instead supports a claim, is ambiguous, is non-knowledge, or should remain quarantined. Publication expands this into `audit/semantic-dispositions.jsonl`, so every retained event has one exact destination.

Use `review-packet` to load one full sanitized chain at a time. Its project event count and hash must match the review template. Reading a search result or a truncated excerpt does not satisfy the reviewer attestation.

## Project-chain identity gate

The template's project groups come from deterministic transport metadata. Before changing a project to `reviewed`, confirm that its membership represents one real project or a deliberately retained standalone session. Inspect explicit user intent, session/parent lineage, stable project ids, concrete artifacts, dependencies, corrections, and handoffs. Working directories, titles, file proximity, and keywords are supporting hints only.

Changing `title` or `aliases` does not merge, split, or move events. If false grouping would make the history misleading and the current schema cannot express the correction, keep the affected project `unreviewed` and block publication. A relationship edge improves navigation; it is not a substitute for correct project membership.

## Project history fields

For each real project, use all applicable fields:

1. `objective` — the primary request and its context;
2. `changes_and_corrections` — additions, withdrawals, rejected versions, and changed intent;
3. `actions_and_artifacts` — work actually performed and artifacts actually created;
4. `validation_and_observations` — tests, browser/device observations, and their exact scope;
5. `failures_and_fallbacks` — errors, interruptions, failed attempts, and fallback effects;
6. `delivery_and_state` — the highest observed completion layer, not the smoothest Agent claim;
7. `remaining_work` — open questions, unverified claims, and stopped work.

Each item needs a bounded statement, one or more event ids, and a status such as `observed`, `agent-reported`, `inferred`, `disputed`, or `stale`. Optional `before`, `after`, and `applies_to` fields preserve exact human feedback without turning one edit into a permanent personality rule.

## Base claims

Claims use a stable `claim_id`, `knowledge_type`, `subject`, statement, status, confidence, evidence ids, observation date/range, applicability, conflicts, and superseded claims.

- Confirmed identity or direction claims require grade-A primary-user evidence.
- Confirmed collaboration or expression claims also require grade-A primary-user evidence and a bounded context.
- A correction is strong evidence for what was rejected; it does not prove every possible replacement.
- Agent text may support project context but cannot by itself confirm the user's identity.
- External facts stay dated and must be rechecked when a current task depends on them.
- Keep conflicting claims side by side as `disputed`, `stale`, or `retracted`; do not harmonize them for narrative smoothness.

## Project relationships and link analysis

Every `reviewed` project sets `link_analysis.status` to `linked` or `intentional-isolate`; every `reviewed-no-knowledge` project uses `not-published`. An intentional isolate includes the evidence-bounded reason that no trustworthy relationship was found.

A project relationship records a stable id, two different project keys, precise relation label, direction, status, confidence, rationale, evidence basis, and evidence event ids from both endpoints. Allowed bases are `explicit-user-intent`, `continuation-lineage`, `shared-artifact`, `dependency`, `correction`, `contradiction`, and `observed-handoff`.

Every relationship status, including uncertain and rejected candidates, needs evidence from both chains so the audit explains what was compared. `confirmed` can connect only published projects. Filename, title, broad type, directory proximity, and keyword overlap are discovery hints and are never valid evidence bases.

Only confirmed relationships become reciprocal document navigation. Directed semantics remain directed in `knowledge-graph.json`; the target's backlink is labeled as the reverse of the relation. Uncertain and rejected candidates remain in `audit/relationship-candidates.json`.

## Completion ladder

Record these separately: requested, designed, implemented, artifact created, local install, enabled, invoked, automated tests passed, real interaction observed, user accepted, submitted, merged, remotely published, publicly reachable. Publish only the highest layer with direct evidence.

## Reviewer attestation

The reviewer id, timestamp, and attestation are required. The attestation means every ordered event in every hashed project chain was read, not merely searched, summarized, or viewed through truncated output. For segmented reading, the final count and hash must match the template and every range must be contiguous and non-overlapping. Unsupported formats must be inspected and explicitly acknowledged; acknowledgement never changes them into supported formats.
