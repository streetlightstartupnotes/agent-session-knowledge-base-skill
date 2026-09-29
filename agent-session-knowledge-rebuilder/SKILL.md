---
name: agent-session-knowledge-rebuilder
description: Rebuild or maintain private session knowledge, or improve this Skill from explicitly requested history review. Task-end maintenance requires standing permission and new durable evidence. Skip ordinary writing, coding and research; missing personal recall belongs to the Reader.
---

# Agent Session Knowledge Rebuilder

Turn session evidence into useful private knowledge, or improve this Skill when
asked. Choose the requested operation before opening sources or running commands.
This is not a general task orchestrator and must not take over ordinary work.

## Route once, then load only that workflow

| Requested result | Read and do | Do not start implicitly |
|---|---|---|
| Inventory, first reconstruction, or generated-library update | [reconstruction.md](references/reconstruction.md); for inventory-only, stop after inventory | Registration, host configuration, public upload |
| Improve this Skill from specified collaboration history | [collaboration-learning.md](references/collaboration-learning.md); implement bounded portable changes and verify them | Rebuilding or invalidating the active private library |
| Save an evidenced task outcome under explicit/standing permission | [host-integration.md](references/host-integration.md); choose human-page or generated-library maintenance | Full-machine discovery or either other write mode |
| Retract, forget, or set retention/recheck dates | [lifecycle-contract.md](references/lifecycle-contract.md); plan before a confirmed mutation | A new rebuild or source-session deletion |
| Prepare or publish the public Skill source | [public-release.md](references/public-release.md); scan the exact candidate tree | Publishing private knowledge, installing, committing or pushing without authority |

A generic task with no maintenance request or authorized durable delta needs none
of these workflows. Missing historical context belongs to the separate Reader,
when available; do not reconstruct a library to answer one recall question.
Read and write decisions are independent. Explicit invocation still respects the
user's requested operation: asking for status does not authorize a new rebuild.

For mixed requests, complete only the applicable branches in dependency order.
One mode's completion gate does not substitute for another's. Do not ask the user
to choose technical details already settled by the request or existing evidence.
Ask only when a missing choice changes scope, privacy, authority or the outcome.

## Preserve the current task, not just old preferences

In review/maintenance, recover purpose, target/version, unchanged requirements,
latest correction, action authority and observable end state. Keep these inside
existing review notes or project history; do not create a separate planning system.
Current instructions override older preferences. A narrow correction changes its
target, not every related project. Detailed semantics and counterexamples live in
[collaboration-learning.md](references/collaboration-learning.md); load it when
extracting interaction patterns. Load [writing-history.md](references/writing-history.md)
only for writing/editing evidence, not ordinary composition.

Reuse validated reviewed ranges within the agreed cutoff; reopen changed or
uncovered ranges and re-synthesize affected conclusions. A request for all history
requires honest coverage, not repeated scans or sampling presented as exhaustive.

## Non-negotiable boundaries

- Keep source sessions read-only. Never delete, rewrite, move, repair, or upload them.
- Inventory the accessible environment before claiming coverage. Installed software, a supported source format, an execution host, and an operating system are separate compatibility facts.
- Claim input support only for exact formats in [references/compatibility.md](references/compatibility.md). Preserve unknown candidates, ambiguous probes, unreadable roots, and scan gaps.
- Freeze source sizes and full frozen-byte hashes before parsing. Do not chase files created after the freeze or re-ingest the rebuild's own logs.
- Preserve visible messages, tool calls/results, patches, browser/device events, failures, fallbacks, and observed delivery. Quarantine summaries, runtime injection, old-memory reads, imported/nested transcripts, hidden reasoning, and delegated wrappers.
- A serialized `user` lane is transport provenance, not proof of the primary human. Resolve it through evidence-bound `actor_attributions`; keep customers, third parties, test actors, orchestrators, and subagents separate.
- Strip binary bodies and redact credentials, authorization/Cookie material, URI userinfo, private keys, contacts, non-global addresses, private home paths, and standard/Base64URL/MIME/data-URL payloads from generated artifacts.
- Treat deterministic project keys as proposals. Never use a graph link to hide a false merge or split.
- Do not infer identity, preference, causality, ownership, relationship, completion, or evolution from keywords or Agent claims. Published assertions need valid event evidence and the applicable semantic gate.
- Keep generated knowledge, reviews, audits, registries, real sessions, private eval sets, lifecycle plans, and real-sample outputs outside the public Skill source tree.

## Handoff at the chosen mode's actual finish line

Lead with the useful result, remaining limitation and verified state. Inventory
is not reconstruction; a private note is not published generated knowledge; a
Skill patch is not a deployed update; a local release candidate is not a remote
release. Do not spend the delivery on test counts instead of the outcome.

If interrupted, preserve the chosen mode, cutoff/checkpoint, changed files, next
required step and pending gate in the existing private task record. Resume there
instead of restarting unrelated discovery. Never claim background work completed
while the host was not running.
