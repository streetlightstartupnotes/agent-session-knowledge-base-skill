# Efficient, low-hallucination distillation

Use this reference after deterministic parsing and before claiming that the knowledge base is rebuilt. It generalizes lessons from a large, failure-preserving session reconstruction without carrying any person's facts into the Skill.

## Why the pipeline has two layers and four gates

The deterministic layer is good at coverage, format handling, binary removal, redaction, deduplication, continuation merging, event ids, and incremental state. It cannot reliably decide that a first-person test prompt is a durable identity fact, that an Agent completion statement is true, or that two similarly named efforts are one project.

The semantic layer reads the sanitized evidence, restores context and causality, and writes maintainable knowledge. Skipping either layer causes a predictable failure. Pure manual reading wastes time and loses coverage; pure automated summarization produces confident, smooth hallucinations.

Keep four denominators separate throughout the run:

1. frozen source bytes and files;
2. transport records with an explicit deterministic disposition;
3. sanitized retained events with an explicit semantic disposition;
4. published claims and project assertions with valid evidence links.

The corresponding gates are freeze, ingest, review, and publish. A later gate cannot repair an unaccounted earlier denominator. `audit/completion-report.json` is the machine-readable authority for which gates passed.

## Distill in four passes

### 1. Freeze and account for coverage

Start from the immutable snapshot and audit statistics. Resolve parser errors and unsupported candidates before interpreting content. Record the actual denominator, changed files, excluded classes, fallback reparses, and unreadable material. “Discovered,” “opened,” “parsed,” and “semantically reviewed” are separate states.

Do not chase files created after the snapshot during the same run. They belong to the next incremental snapshot. This prevents the reconstruction process from repeatedly consuming its own new transcript.

### 2. Rebuild each real project chain

Use explicit session ids, parent/continuation links, stable project ids, working directories, and repeated concrete artifacts to route sessions. Titles and keyword similarity may nominate a link but cannot prove one. When the link is uncertain, keep separate chains and record the question.

Treat a generated `project_key` as a routing proposal, not semantic truth. Before writing histories, compare proposed chains for false merges and false splits. A shared working directory can contain unrelated efforts; one real project can move across directories, Agents, or session identifiers. Explicit user intent, native continuation lineage, and concrete shared artifacts outrank path and title hints. Renaming a review entry or adding an alias does not change its event membership. If the current review schema cannot express the correct grouping, leave the affected project unreviewed and report the blocker instead of publishing a misleading dossier.

Read every retained event in the chosen chain in source order. While reading, maintain six fields:

1. original user objective and context;
2. later additions, withdrawals, and corrections;
3. actions and artifacts actually produced;
4. observed tests, browser/device checks, and their exact coverage;
5. failures, fallbacks, interruptions, and superseded versions;
6. observable end state and remaining work.

Write a narrative project history from those fields. It should be detailed enough that another Agent can continue without reopening every transcript, yet each important claim should retain event ids. Do not replace the history with a source index, feature list, or one-paragraph success summary.

Start with `review-init`. It hashes the full retained event set and each proposed project chain. A review remains valid only for those exact hashes. Mark every chain `reviewed` or `reviewed-no-knowledge`; silence is not review. If an incremental run changes a chain, use `audit/impact-report.json` to reopen that chain instead of silently carrying its old conclusions forward.

### 3. Derive cross-project knowledge

Only after project histories are stable should you update evidence rules, identity/current direction, and collaboration/expression rules. Cross-project knowledge must be supported by dated direct statements or repeated contextual choices. A single short instruction, imported prompt, role-play, or third-party article is not a stable preference or identity.

Represent each promoted assertion as a claim record: claim id, subject, statement, status, confidence, observed date, applicability, evidence event ids, conflicts, and superseded claims. Keep disputed, retracted, and stale claims in the audit instead of rewriting history into one smooth story. Run `validate-review` before `distill`; publication must fail closed when a claim points to missing, wrong-project, or insufficient-grade evidence.

### 4. Relink by intent and evidence

Use the compact reviewed histories to nominate possible continuations, dependencies, shared artifacts, handoffs, corrections, and contradictions. A nomination is not a link. Reopen the complete sanitized evidence packet for both endpoint projects and cite at least one event from each side. Confirm only relationships supported by explicit user intent, continuation lineage, a concrete artifact, dependency, correction, contradiction, or observed handoff.

Project titles, filenames, directory proximity, broad categories, and keyword overlap can help locate material but can never be the recorded evidence basis. Keep an unsupported candidate uncertain or rejected. Every published project must either participate in a confirmed project/base-claim link or carry an intentional-isolate rationale.

Publishing materializes one graph edge and reciprocal Markdown navigation for each confirmed document relationship. Direction remains in the graph even though navigation works both ways. The reverse link is discoverability, not proof that the directed causal or dependency claim reverses.

## Reading efficiently without pretending to read less

- Remove binary and known runtime noise before semantic reading, but retain a hash/length or exclusion audit.
- Deduplicate exact and semantic replays before counting patterns. Approval transcripts and imported history must not amplify a statement.
- Route by project first. Read one complete chain at a time instead of loading the whole corpus into one context.
- If a chain exceeds one context or tool response, read consecutive non-overlapping ranges. Checkpoint the next event index, boundary event ids, cumulative count, and packet hash; resume from that exact boundary rather than resummarizing the beginning. A truncated response never satisfies complete-chain attestation.
- Prioritize corrections, errors, state transitions, patches, tests, browser/device observations, and deliveries when constructing the project state sheet. Still read the surrounding messages so the priority signal does not become a context-free conclusion.
- For concrete feedback, preserve the rejected form and accepted replacement as `before` and `after`, plus the scope where the lesson applies. This is more reusable and less hallucinatory than turning one correction into a universal personality rule.
- Store the detailed project history once. Later tasks retrieve only evidence rules, the relevant base rule set, and one to three matching projects.
- Follow only a small bounded number of confirmed graph edges during retrieval. A deep graph should improve discovery without flooding every task with the whole knowledge base.

## Claim-promotion gates

### Identity and ownership

Promote an identity fact only when the speaker is confidently the primary user and the statement is about the user rather than a client, subagent, test role, quoted source, or requested persona. Preserve date and context. Project participation does not automatically prove job title, authorship, or ownership.

### Preference and collaboration rule

Separate task-specific scope, risk-based confirmation gates, content taste, and durable collaboration preferences. A nearby “yes” confirms only the immediately preceding question. A correction is stronger evidence of the rejected interpretation than of every possible replacement.

### Causality and chronology

An earlier command proves action order, not the user's original motivation. A missing tool record proves only that the action was not recorded. Do not turn either absence into a first-person story. When timestamps, versions, or claims conflict, preserve the conflict and prefer the latest explicit correction.

### Completion

Track declared capability, implementation, local installation, enablement, invocation, artifact generation, automated tests, real interaction, user acceptance, remote publication, and public accessibility separately. Only the highest observed layer may be reported as complete.

The project history should use the same ladder for each deliverable. An Agent sentence such as “done” is a grade-C assertion; a patch, test result, browser observation, delivered file, and user acceptance are different evidence layers and must not be collapsed.

### External facts

Session evidence proves what was said or observed then. Prices, laws, product versions, follower counts, remote repositories, and public states may have changed. Keep the historical date and re-verify when the current task depends on them.

## Final anti-hallucination audit

Before handoff, sample every promoted identity fact and collaboration rule back to its event id. For each project, compare the final history with the last user correction, the strongest observable validation, every retained error, and the final delivery/status event. Search the final knowledge tree for credentials, private contacts, raw Base64, unsupported compatibility claims, identity claims sourced from non-primary actors, and unqualified words such as “all,” “finished,” or “published.”

If evidence is missing, write the uncertainty. An explicit unresolved item is a successful reconstruction outcome; a plausible invented bridge is not.

After the audit passes, publish with `distill`. Then exercise the normal reader with `query` and confirm that an unrelated task returns `no_match`; retrieval precision is part of the knowledge contract, not an optional convenience.
