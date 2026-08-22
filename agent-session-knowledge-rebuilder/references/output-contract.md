# Output contract

Use this reference when consuming, validating, or changing a generated knowledge base.

## Immutable evidence layer

`audit/events.jsonl` contains sanitized unified events. Each event has schema version, stable event id, source agent and adapter, non-secret source identity, raw and merged session identity, source-order locator, timestamp, role, actor kind, event type, evidence grade, flags, call linkage, sanitized visible content and content hash, and project linkage.

The event's deterministic `project_key` is a routing proposal derived from available native ids and path context. It becomes a reviewed project boundary only after semantic reconciliation. Consumers must not treat a shared key as proof that every event belongs to one real project.

`audit/dispositions.jsonl` gives every parsed record/event a deterministic destination: evidence candidate, transport duplicate, excluded runtime/summary, quarantine, or parse error. `audit/semantic-dispositions.jsonl` is created only after review and gives every retained event its maintained-knowledge destination.

Evidence grades mean:

- `A`: direct statement from a confidently isolated primary user;
- `B`: observable tool, patch, browser, device, status, or delivery evidence;
- `C`: visible Agent assertion or diagnosis not independently observed;
- `D`: ambiguous provenance, imported/test material, or unresolved state.

`audit/excluded.jsonl` records locator and reason only. It must not copy compacted summaries, hidden reasoning, runtime injection, or raw secrets. `audit/errors.jsonl` keeps parsing and fallback details without source content.

## Snapshot and incremental state

`audit/snapshots/<run-id>.json` freezes source paths, adapters, sizes, mtimes, and file identities at the start of a run. `audit/snapshot.json` contains the latest equivalent snapshot. `audit/state.json` keeps complete byte offsets and sampled boundary hashes used to verify append-only updates.

Growing JSONL files read only the verified tail. Rewritten or truncated files fall back to a full parse and record that fact. Missing prior files do not cause old evidence to be deleted automatically.

## Knowledge layer

The generated contract is:

```text
knowledge/
  00-evidence-rules.md
  01-identity-and-current-direction.md
  02-collaboration-and-expression.md
  projects/*.md
  knowledge-graph.json
  knowledge-index.json
```

Before semantic review, the identity, collaboration, and project files are explicitly drafts and `knowledge-index.json` has `semantic_status: draft`. The query entrypoint rejects them by default. After a matching review passes, `distill` writes reviewed claims and detailed project histories and changes the index to `semantic_status: published`.

`knowledge-index.json` powers task-scoped retrieval. `knowledge-graph.json` contains document, base-claim, and granular project-assertion nodes plus confirmed, uncertain, or rejected edges with direction, rationale, evidence basis, and evidence ids. A project-relationship edge records assertion anchors when its evidence ids support published history items at both endpoints. Project and base documents receive reciprocal `Related knowledge` navigation only for confirmed document edges. The query command returns evidence rules, matching base/project documents, and at most the requested number of one-hop confirmed related documents.

## Audit layer

The output also includes `audit/stats.json`, `audit/completion-report.json`, `audit/impact-report.json`, `audit/compatibility-matrix.md`, `audit/unsupported-formats.json`, `audit/coverage-gaps.json`, `audit/errors.jsonl`, `audit/excluded.jsonl`, `audit/relationships.json`, `audit/relationship-candidates.json`, and `audit/link-audit.json`.

Counts distinguish discovered, frozen and parsed files, retained events, duplicates, excluded records, redactions, binary replacements, incremental bytes, reparsed files, and errors. A zero parser error count does not imply semantic review is complete.

Likewise, zero reported coverage gaps means zero gaps inside the declared discovery contract. It does not prove that arbitrary file types, inaccessible accounts, vendor clouds, unmounted disks, or locations excluded by discovery heuristics were searched.

`audit/coverage-gaps.json` records roots that were not fully probed because a bounded `--max-files` run stopped discovery. A coverage gap is not mislabeled as an unsupported format, and it blocks semantic publication until a complete discovery run is frozen.

`audit/completion-report.json` is the only machine-readable completion authority. `audit/impact-report.json` lists added/removed events and affected project keys after an incremental run, invalidating old semantic assumptions without rereading unaffected projects blindly.

`audit/relationships.json` contains every graph edge. `audit/relationship-candidates.json` preserves uncertain/rejected or non-publishable project relationship records. `audit/link-audit.json` accounts for every published document's confirmed-link count and each intentional isolate rationale. The `knowledge_graph_complete` gate must pass before publication is complete.

## Private local location registry

The optional cross-platform registry maps a user-chosen name to a published knowledge-base root. It lives in the user's platform configuration directory and is never part of this output tree or the open-source Skill. Registration is an explicit post-publication action. The companion reader refuses draft indexes and can use either a registry name or an explicit path.
