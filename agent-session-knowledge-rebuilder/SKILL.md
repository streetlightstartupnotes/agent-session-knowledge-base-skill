---
name: agent-session-knowledge-rebuilder
description: Rebuild and incrementally maintain an auditable personal, project, and collaboration knowledge base from supported Agent session archives. Use to inventory accessible Windows, macOS, or Linux environments, normalize verified formats, redact private material, semantically review complete project chains, publish evidence-bound links, or configure task-scoped retrieval. Report unknown formats, scan gaps, execution-host limits, and unverified operating systems instead of claiming universal compatibility.
---

# Agent Session Knowledge Rebuilder

Turn supported Agent transcripts into a private, maintainable knowledge base. Execute the workflow for the user from inventory through verified retrieval; do not stop at extraction or hand them an empty review form. Deterministic code prepares and audits evidence. The Agent using this Skill performs the semantic work: it reads every sanitized chain, reconstructs user intent, verifies project grouping, writes the review, checks relationships against both endpoints, and publishes only after every applicable gate passes.

## Execution contract

Read [references/execution-contract.md](references/execution-contract.md) before starting a rebuild and again before the final report.

- Separate source-format support, execution-host support, and operating-system validation. Success in one dimension proves nothing about the others.
- Treat automatic discovery and deterministic project keys as bounded proposals, not proof of exhaustive coverage or correct real-project grouping.
- Continue autonomously through ordinary in-scope work. Pause only for the output-location decision, unavailable evidence, a material scope choice, or an action that needs new authority.
- Never attest to a project chain from search hits, summaries, or truncated tool output. Use exact event counts, hashes, and contiguous checkpoints for long chains.
- A run is not complete at `inventory`, `rebuild`, or `review-init`. Complete semantic review, relationship validation, publication, and related/unrelated retrieval checks unless a named blocker prevents them.

## Non-negotiable boundaries

- Keep source sessions read-only. Never delete, rewrite, move, repair, or upload them.
- Inventory the accessible environment before claiming coverage. Presence of an app, directory, or adapter framework is not compatibility.
- Claim support only for an exact format listed as verified in [references/compatibility.md](references/compatibility.md). Report every unknown or ambiguous candidate and every bounded scan gap.
- Freeze source size and boundary hashes before parsing. Never chase files created after the snapshot or re-ingest the current rebuild's own logs.
- Preserve visible user/Agent messages, tool calls/results, patches, browser/device events, errors, fallbacks, and observed delivery. Quarantine summaries, runtime injection, old-memory reads, nested approval/imported transcripts, hidden reasoning, and delegated wrappers.
- Treat serialized `user` as provenance, not identity. Keep primary users, unknown users, customers, quoted sources, test actors, orchestrators, and subagents separate.
- Strip binary bodies and redact credentials, cookies, contacts, private network addresses, and home paths from every generated artifact.
- Never infer durable identity, preference, causality, ownership, or completion from keywords or Agent claims. Every published assertion needs valid event ids.
- Generated knowledge, local registry files, validation outputs, and real sessions are private data. Never put them in the open-source Skill tree.

## Choose the Python launcher

Use `py -3` on Windows when available and `python3` on macOS/Linux. In commands below, replace `<python>` with that launcher. Before the first operation, run `<python> --version`. Python 3.9 or newer is required, but no third-party package is required. If no launcher works or the version is older, explain that prerequisite plainly and stop; do not install or upgrade a runtime without permission.

## 1. Inventory first, without writing

Run the broad read-only inventory before asking the user where to store knowledge:

```text
<python> scripts/session_kb.py inventory --json
```

This probes known roots, platform application-data locations, environment overrides, Agent-like home directories, and installed command names. It reads candidate headers for format recognition. Unknown fingerprints contain keys and value types only, never record values. For an export or nonstandard location, repeat `--root PATH`; for a trusted exact file/root, use `--source ADAPTER=PATH`.

The built-in search is intentionally heuristic. “Complete inventory” means complete within the reported roots and filters, not every byte on every mounted disk. When the user asks for all or niche Agents, report the roots actually inspected, use accessible platform/application evidence and explicit exports to expand coverage, and name unavoidable blind spots.

Report four things separately: installed Agent candidates, supported session files by exact adapter, unknown/ambiguous candidates, and discovery errors/coverage gaps. Read [references/adapter-contract.md](references/adapter-contract.md) for the discovery boundary and before adding an adapter. Do not install applications merely to search for sessions.

## 2. Ask where the knowledge base should live

If the user did not already provide an explicit output path, run:

```text
<python> scripts/session_kb.py guide-output --name portable-name
```

Show the recommended personal location, private project-local option, and custom-path option, then ask the displayed question and wait. This location choice is a required user decision because it changes where private data is written. Never choose the Skill source tree, a public repository, a source-session root, the filesystem root, or the user's home directory itself.

After the user confirms a path, continue. `rebuild` independently rejects unsafe source/output nesting.

## 3. Freeze, dry-run, and rebuild

Store the snapshot beside the private knowledge base or in another user-approved private location:

```text
<python> scripts/session_kb.py freeze --snapshot /chosen/private/snapshot.json
<python> scripts/session_kb.py rebuild --snapshot /chosen/private/snapshot.json --output /chosen/private/kb --dry-run
<python> scripts/session_kb.py rebuild --snapshot /chosen/private/snapshot.json --output /chosen/private/kb
```

Inspect `audit/completion-report.json`, `stats.json`, `dispositions.jsonl`, `impact-report.json`, `coverage-gaps.json`, `errors.jsonl`, `unsupported-formats.json`, and `compatibility-matrix.md`. The initial state must be `needs_semantic_review`; deterministic extraction is not a finished knowledge base.

For later runs, repeat inventory/freeze and use `--incremental`. Append-only streams read only verified tails; changed streams fall back to an audited full reparse. The new evidence run invalidates affected semantic conclusions. `review-init` preserves an existing review by creating a run-specific review file.

Read [references/distillation-method.md](references/distillation-method.md) before semantic work.

## 4. Perform the Agent-run semantic review

Initialize a hash-bound review:

```text
<python> scripts/session_kb.py review-init --kb /chosen/private/kb
```

The command returns the exact review path. For each project key, retrieve one complete sanitized chain:

```text
<python> scripts/session_kb.py review-packet --kb /chosen/private/kb --project-key PROJECT_KEY
```

As the executing Agent, read every ordered event in that packet and fill the returned review file yourself. Do not ask the user to transcribe it. “Automatic” here means the invoked Agent continues the semantic work; the Python runtime does not invent claims.

Before attesting, verify that deterministic grouping matches the real project chain. A shared working directory, similar title, or common keyword is not enough; check explicit project/session lineage, user intent, concrete artifacts, corrections, and handoffs. If a chain is wrongly merged or split and the current review structure cannot represent the correction, leave it unreviewed and report the grouping blocker. Do not hide it with a relationship link.

For a packet that cannot fit without truncation, follow the contiguous checkpoint protocol in [references/distillation-method.md](references/distillation-method.md). Record the objective, later corrections, actions/artifacts, observed validation, failures/fallbacks, delivery level, and remaining work. Every history item cites events from its own chain. Mark noise-only chains `reviewed-no-knowledge` with a reason.

Derive the three base knowledge classes only from appropriate evidence: evidence/reading rules, identity/current direction, and collaboration/expression rules. Preserve dates, scope, disputes, retractions, and staleness. Follow [references/review-contract.md](references/review-contract.md).

## 5. Analyze relationships and create evidence-bound bidirectional links

After all project histories are stable, make a second semantic pass across their objectives, corrections, artifacts, dependencies, handoffs, and contradictions.

1. Nominate possible relationships from meaning and user intent, not filenames, broad project types, or keyword overlap.
2. Reopen the complete `review-packet` for both endpoints.
3. Record a precise direction, relation label, evidence basis, confidence, rationale, and event ids from both chains.
4. Use `confirmed` only when both sides support the link. Keep plausible but insufficient candidates `uncertain`; record disproved candidates `rejected`.
5. Set every published project to `linked` or `intentional-isolate`. An isolate needs a reason; absence of a fabricated link is a valid result.

Allowed evidence bases are explicit user intent, continuation lineage, a concrete shared artifact, dependency, correction, contradiction, or observed handoff. The validator rejects lexical-only bases and relationships without evidence from both endpoints.

```text
<python> scripts/session_kb.py validate-review --kb /chosen/private/kb --review /chosen/private/kb/review/REVIEW_FILE.json
```

Fix failures or preserve uncertainty. Never bypass the gate.

## 6. Publish and verify retrieval

```text
<python> scripts/session_kb.py distill --kb /chosen/private/kb --review /chosen/private/kb/review/REVIEW_FILE.json
<python> scripts/session_kb.py query --kb /chosen/private/kb --task "current task" --max-projects 3 --max-related 2
```

Publishing writes the three base documents, detailed project histories, `knowledge-graph.json`, reciprocal Markdown links, and relationship/link audits. Only confirmed document edges become navigation; uncertain and rejected candidates stay in audit. Verify one related task and one unrelated task; the latter should return `no_match` rather than broad context.

At handoff, report the frozen and semantic denominators, exact verified input formats present, unknown candidates and scan boundaries, execution host, operating systems actually exercised, privacy/redaction results, relationship coverage, errors/fallbacks, and remaining blockers. Do not collapse these into “all compatible” or “finished.”

## 7. Offer the separate reader Skill

The companion `$agent-knowledge-reader` reads only task-relevant published documents. After successful publication, ask whether the user wants to register the chosen private location. Registration is a separate local write:

```text
<python> scripts/session_kb.py register-kb --name portable-name --kb /chosen/private/kb --default
```

Do not register without confirmation. The reader can also use an explicit `--kb` path without registry state.

## 8. Open-source release gate

Before packaging or committing this Skill pack, run:

```text
<python> agent-session-knowledge-rebuilder/scripts/session_kb.py release-check --root /path/to/skill-pack
```

Add private names or identifiers with repeated `--deny-term` arguments when needed; matches are never echoed. A pass forbids secrets, contacts, private home paths, raw binary/Base64, real session/state files, generated knowledge/audit trees, symlinks, and validation output. Read [references/open-source-release.md](references/open-source-release.md).

For generated schemas and completion gates, read [references/output-contract.md](references/output-contract.md).
