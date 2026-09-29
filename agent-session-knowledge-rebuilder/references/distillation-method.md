# Efficient, low-hallucination distillation

Use this reference after deterministic parsing and before claiming that the knowledge base is rebuilt. It generalizes lessons from a large, failure-preserving session reconstruction without carrying any person's facts into the Skill.

## Why the pipeline has two layers and staged gates

The deterministic layer is good at coverage, format handling, binary removal, redaction, deduplication, continuation merging, event ids, and incremental state. It cannot reliably decide that a first-person test prompt is a durable identity fact, that an Agent completion statement is true, or that two similarly named efforts are one project.

The semantic layer reads the sanitized evidence, restores context and causality, and writes maintainable knowledge. Skipping either layer causes a predictable failure. Pure manual reading wastes time and loses coverage; pure automated summarization produces confident, smooth hallucinations.

Keep four denominators separate throughout the run:

1. frozen source bytes and files;
2. transport records with an explicit deterministic disposition;
3. sanitized retained events with an explicit semantic disposition;
4. published claims and project assertions with valid evidence links.

The corresponding evidence gates are freeze, ingest, review, and publish. A v0.5 publication is followed by a retrieval eval set with at least two distinct related paraphrases and two distinct hard negatives. A later gate cannot repair an unaccounted earlier denominator. `audit/completion-report.json` is the machine-readable authority for which gates passed.

## Distill in four passes

### 1. Freeze and account for coverage

Start from a complete immutable snapshot version 2. Every requested source needs a full frozen-byte digest and matching source/discovery denominator; any freeze error writes no snapshot, and incomplete manifests are unusable. Snapshot creation is exclusive and never overwrites an old denominator. Resolve parser errors and unsupported candidates before interpreting content. Record the actual denominator, changed files, excluded classes, fallback reparses, and unreadable material. “Discovered,” “opened,” “integrity-read,” “parsed,” and “semantically reviewed” are separate states.

Do not chase files created after the snapshot during the same run. They belong to the next incremental snapshot. This prevents the reconstruction process from repeatedly consuming its own new transcript.

For an append-only source, parser work may be tail-only while physical I/O is not: the old prefix is read for full-prefix integrity and actual-stream digest binding. Report parsed bytes separately from integrity bytes. If a sampled boundary or the actual-read full digest differs from the snapshot, preserve the previous verified state and fail that source rather than advancing an untrusted cursor. This catches middle changes and ABA-style matching metadata/boundaries when content differs. On a later consistent snapshot, full-reparse recovery must replace the source's retained events and audit rows.

Preserve old-memory isolation across append-only runs with irreversible call-id hashes only, so a delayed result cannot re-enter current evidence. Accumulate the transport denominator per source so an old unaccounted record survives an unchanged run. Keep project routing stable with an irreversible source/session identity map whose persisted label/path context is sanitized; never persist the raw private working directory for that purpose.

### 2. Rebuild each real project chain

Use explicit session ids, parent/continuation links, stable project ids, working directories, and repeated concrete artifacts to route sessions. Titles and keyword similarity may nominate a link but cannot prove one. When the link is uncertain, keep separate chains and record the question.

Treat a generated `project_key` as a routing proposal, not semantic truth. Before writing histories, compare proposed chains for false merges and false splits. A shared working directory can contain unrelated efforts; one real project can move across directories, Agents, or session identifiers. Explicit user intent, native continuation lineage, and concrete shared artifacts outrank path and title hints. Renaming a review entry or adding an alias does not change its event membership.

Review version 4 can apply a hash-bound membership plan that merges proposed chains, splits exact event subsets, or reassigns them to a stable project key. The plan must bind the raw global event set, every proposed source project, selected ordered event ids, selected semantic events, and evidence inside that exact assignment. Unassigned events remain in their proposal. Read and write histories against the effective partition, and publish the canonical before/after/event-diff audit. If evidence still cannot support a safe assignment, leave it unreviewed rather than publishing a misleading dossier.

Read every retained event in the chosen chain in source order. While reading, maintain six fields:

1. original user objective and context;
2. later additions, withdrawals, and corrections;
3. actions and artifacts actually produced;
4. observed tests, browser/device checks, and their exact coverage;
5. failures, fallbacks, interruptions, and superseded versions;
6. observable end state and remaining work.

Write a narrative project history from those fields. It should be detailed enough that another Agent can continue without reopening every transcript, yet each important claim should retain event ids. Do not replace the history with a source index, feature list, or one-paragraph success summary.

Start with `review-init`. Review version 4 hashes the complete canonical unified-event records for the full retained set and each effective project, while a separate hash fixes ordered event ids. A review remains valid only for those exact count/hash pairs and its canonical membership partition. Mark every chain `reviewed` or `reviewed-no-knowledge`; silence is not review.

On an incremental run, use `review-init --from-review PRIOR_REVIEW.json`. A fully identical v4 project can carry as a whole. A changed project can carry only hash-bound semantic chunks whose selected events, evidence notes, note hashes, and one-event adjacent boundaries remain identical. Reopen every uncovered, changed, or boundary-affected range. Old free-text histories from the changed project do not carry.

After partial reuse, combine the carried evidence notes with every freshly reopened range and write a new full-project synthesis bound to all current publishable conclusions. After any whole-project or chunk carry-forward, complete `cross_project_recheck`: revisit membership, links, conflicts, repeated-context claims, global scopes, approvals, and evolved rules across unchanged and changed projects. Hash identity prevents unnecessary rereading; it does not prove current whole-project or cross-project meaning.

### 3. Derive cross-project knowledge

Only after project histories are stable should you update evidence rules, identity/current direction, and collaboration/expression rules. Cross-project knowledge must be supported by dated direct statements or repeated contextual choices. A single short instruction, imported prompt, role-play, or third-party article is not a stable preference or identity.

Before treating any serialized user-lane event as first-person evidence, resolve it through `actor_attributions`. `native_user` records the provider lane, not the primary person's identity. Use a project-lane or exact-event attribution with cited evidence; keep unresolved lanes as `unknown_user`.

Represent each promoted assertion as a claim record: claim id, subject, statement, status, confidence, observed date, applicability, evidence event ids, conflicts, and superseded claims. Keep disputed, retracted, and stale claims in the audit instead of rewriting history into one smooth story.

Record concrete feedback separately in `feedback_signals`. A correction, praise, gap, or outcome keeps its object and actual scope. If it motivates a future rule, create a versioned `rule_evolutions` candidate. Explicit approval may activate a bounded confirmed claim; repeated primary-user evidence from at least two project chains may establish a `repeated-context` claim without pretending it was explicitly approved. Only a later passed behavior check with primary-user or observable grade-B evidence can move an evolution to `validated`; only then call it evolved. Read [evolution-contract.md](evolution-contract.md).

Run `validate-review` before `distill`; publication must fail closed when a claim, attribution, feedback record, evolution, completion state, or relationship points to missing, wrong-project, or insufficient evidence.

### 4. Relink by intent and evidence

Use the compact reviewed histories to nominate possible continuations, dependencies, shared artifacts, handoffs, corrections, and contradictions. A nomination is not a link. Reopen the complete sanitized evidence packet for both endpoint projects and cite at least one event from each side. Confirm only relationships supported by explicit user intent, continuation lineage, a concrete artifact, dependency, correction, contradiction, or observed handoff.

Project titles, filenames, directory proximity, broad categories, and keyword overlap can help locate material but can never be the recorded evidence basis. Keep an unsupported candidate uncertain or rejected. Every published project must either participate in a confirmed project/base-claim link or carry an intentional-isolate rationale.

Publishing materializes one graph edge and reciprocal Markdown navigation for each confirmed document relationship. Direction remains in the graph even though navigation works both ways. The reverse link is discoverability, not proof that the directed causal or dependency claim reverses.

## Reading efficiently without pretending to read less

- Remove binary and known runtime noise before semantic reading, but retain a hash/length or exclusion audit.
- Deduplicate exact and semantic replays before counting patterns. Approval transcripts and imported history must not amplify a statement.
- Route by project first. Read one complete chain at a time instead of loading the whole corpus into one context.
- For every chain, copy each `review-packet` `receipt` into the project's `reading_receipts`. If a chain exceeds one context or tool response, call `review-packet --start-event N --max-events K` for consecutive non-overlapping ranges. Resume from `range.next_start_event`; verify the boundary event ids, accumulate `returned_event_count`, and compare both ordered-id and full semantic event-set hashes. Receipts must cover `0..event_count` without gaps or overlap. A truncated response never satisfies complete-chain attestation.
- During the first complete read, preserve each exact range as an attested `semantic_chunk` with in-range evidence notes and note hashes. On a later run, reuse only chunks whose range and adjacent-boundary hashes still match; reread the reported complements and synthesize the complete current project again.
- Prioritize corrections, errors, state transitions, patches, tests, browser/device observations, and deliveries when constructing the project state sheet. Still read the surrounding messages so the priority signal does not become a context-free conclusion.
- For concrete feedback, preserve the rejected form and accepted replacement as `before` and `after`, plus the scope where the lesson applies. This is more reusable and less hallucinatory than turning one correction into a universal personality rule.
- Store the detailed project history once. Later tasks first check whether current
  context suffices; retrieve nothing if it does. Otherwise retrieve the missing
  fact's evidence rules and relevant record, not a fixed quota of base pages or projects.
- Follow only a small bounded number of confirmed graph edges during retrieval. A deep graph should improve discovery without flooding every task with the whole knowledge base.

## Claim-promotion gates

### Identity and ownership

Promote an identity fact only when the speaker has a valid semantic attribution to the primary user and the statement is about that person rather than a client, subagent, test role, quoted source, or requested persona. Preserve date and context. A native user lane and project participation do not automatically prove job title, authorship, or ownership.

### Preference and collaboration rule

Separate task-specific scope, risk-based confirmation gates, content taste, and durable collaboration preferences. A nearby “yes” confirms only the immediately preceding question. A correction is stronger evidence of the rejected interpretation than of every possible replacement.

### Causality and chronology

An earlier command proves action order, not the user's original motivation. A missing tool record proves only that the action was not recorded. Do not turn either absence into a first-person story. When timestamps, versions, or claims conflict, preserve the conflict and prefer the latest explicit correction.

### Completion

Track declared capability, implementation, installation, invocation, tests, real
interaction, acceptance and publication separately per deliverable and environment.
The schema's completion summary is not proof of all earlier states: something
publicly reachable may remain unaccepted or defective. Preserve these limits in
history and completion rationale. An Agent sentence such as “done” is grade C;
only report the specific state actually supported by evidence.

### External facts

Session evidence proves what was said or observed then. Prices, laws, product versions, follower counts, remote repositories, and public states may have changed. Keep the historical date and re-verify when the current task depends on them.

## Final anti-hallucination audit

Before handoff, sample every promoted identity fact and collaboration rule back to its event id. For each project, compare the final history with the last user correction, the strongest observable validation, every retained error, and the final delivery/status event. Search the final knowledge tree for credentials, private contacts, raw Base64, unsupported compatibility claims, identity claims sourced from non-primary actors, and unqualified words such as “all,” “finished,” or “published.”

If evidence is missing, write the uncertainty. An explicit unresolved item is a successful reconstruction outcome; a plausible invented bridge is not.

After the audit passes, publish with `distill`. Then run `verify-retrieval --eval-set` with at least two distinct related paraphrases and two distinct hard negatives after privacy cleanup. Every case uses one shared suite-level retrieval profile; case-level threshold overrides are invalid. Every related expectation and every hard-negative `no_match` must pass. Query and verification must load the graph selected by the published index, verify all prerequisite publication gates, graph status/run id, and exact document allowlist equality, and reject every archive path. The legacy one-related/one-unrelated pair is diagnostic only for a v0.5 publication. Do not register or invoke the companion Reader as maintained knowledge until the suite passes. Retrieval precision and graph containment are part of the completion contract, not optional conveniences.

During a later distillation, compare current project paths with `audit/published-files.json`. Archive prior generated project documents absent from the current reviewed set under `knowledge/archive/<run-id>/`, record the move in `audit/stale-project-documents.json`, and exclude the archive from current indexed retrieval. This preserves history without leaving renamed or removed dossiers looking current.
