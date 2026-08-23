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

The deterministic evidence layer may use `native_user` for a provider's ordinary user lane. That remains grade D until semantic review supplies a valid `actor_attributions` record. The event row is not rewritten; the review and `audit/actor-attributions.json` preserve the semantic attribution separately.

`audit/excluded.jsonl` records locator and reason only. It must not copy compacted summaries, hidden reasoning, runtime injection, or raw secrets. `audit/errors.jsonl` keeps parsing and fallback details without source content.

Sanitization covers padded/unpadded standard and URL-safe Base64, MIME-wrapped blocks with short terminal lines, parameterized or bare Base64 data URLs, binary mapping fields, provider and generic/compound credential assignments, authorization and Cookie headers, Cookie assignments/jars, URI userinfo, encrypted or ordinary private keys, contacts, non-global addresses, and current/foreign Windows, macOS, or Linux home-account paths. Generic documented home placeholders remain intact. Public release scanning independently blocks the corresponding raw patterns.

## Snapshot and incremental state

`audit/snapshots/<run-id>.json` and `audit/snapshot.json` use snapshot version 2. Each requested source row includes path/adapter identity, frozen size/metadata, sampled head/tail boundaries, and `frozen_sha256` over every frozen byte. `requested_source_count`, `source_count`, discovery supported count, source identities, `complete`, and `errors` form one denominator. Any error makes the snapshot incomplete: the `freeze` CLI writes nothing, and loaders/rebuild reject an incomplete or legacy manifest. An explicit destination is created once and never overwritten.

`audit/state.json` version 2 records schema/sanitizer policy versions and, per source, the last verified complete-byte offset, full/sample boundary hashes, sanitized parser context, `isolated_call_hashes`, `project_identities`, and cumulative `transport_records_seen/accounted/unaccounted`. Old-memory hashes irreversibly bind an excluded call to a tool result that may arrive in a later append-only tail; raw call ids and old-memory content are not retained. Project identities map an irreversible source/session marker to the prior project key and sanitized label so tail events remain stable without persisting a raw private working directory. Global transport totals are summed from source state, preserving old gaps across unchanged runs.

For growing append-only JSONL, only the new tail is parsed. The implementation still physically reads the prior prefix for append verification and an actual-stream digest, so `bytes_parsed` and `integrity_bytes_read`—plus their incremental variants—are separate statistics. Rewritten or truncated files fall back to a full parse and record that fact. Missing prior files do not cause old evidence to be deleted automatically.

When the frozen head/tail boundary fails before reading, the run records `source_boundary_changed_before_read`, preserves the prior verified source state, and does not advance that source. While reading, the digest of every byte in the actual frozen stream must equal snapshot `frozen_sha256`; otherwise `source_boundary_changed_during_read` discards the batch and restores the prior state/evidence. Full digest binding rejects middle changes or ABA-style restored metadata/sampled boundaries when bytes differ. A later run with a consistent fresh snapshot performs a full reparse, removes the source's retained old events plus source-scoped exclusions/errors/dispositions, and writes the replacement representation.

Rebuild, review initialization, distillation, and retrieval verification use a fail-closed single-writer lock for the knowledge base. Generated JSON, JSONL, Markdown, completion, and verification files use same-directory temporary files followed by atomic replacement. A crash may leave a lock that requires ownership inspection; do not delete an active or ambiguous lock.

New review version 3 files use `event_set_sha256` for a canonical hash of complete unified-event records at both the global and project level. Project `event_ids_sha256` independently fixes event order. Each `review-packet` returns a receipt containing the project semantic hash, range bounds, slice event-id hash, and boundary ids. The project's `reading_receipts` must cover every event index contiguously without overlap.

`review-init --from-review` can seed a new review from a prior review. It copies only full-semantic-hash-identical reviewed projects, their valid reading receipts, and still-valid evidence records. `carry_forward.invalidated_project_keys` names reopened chains. Any carry-forward requires the machine-checked `cross_project_recheck` before publication; unchanged chain hashes do not validate cross-project relationships or rules by themselves.

## Knowledge layer

The generated contract is:

```text
knowledge/
  00-evidence-rules.md
  01-identity-and-current-direction.md
  02-collaboration-and-expression.md
  projects/*.md
  archive/<run-id>/*.md
  knowledge-graph.json
  knowledge-index.json
```

Before semantic review, the identity, collaboration, and project files are explicitly drafts and `knowledge-index.json` has `semantic_status: draft`. The query entrypoint rejects them by default. After a matching review passes, `distill` writes reviewed claims and detailed project histories and changes the index to `semantic_status: published`.

`knowledge-index.json` powers task-scoped retrieval and records the feedback-governed evolution summary. The graph path declared in `index.graph.path` is authoritative when present; only the fixed default is used when the field is absent. Query, Reader, and publication-manifest validation use the same graph loader. It requires graph `semantic_status: published`, the same run id as the index, safe unique indexed document paths, exact equality between graph document nodes and the index document allowlist, indexed `document_path` references, and existing edge endpoints. Any path rooted at `archive/` is rejected rather than traversed.

The validated graph contains document, base-claim, granular project-assertion, feedback-signal, and rule-evolution nodes plus confirmed, uncertain, or rejected edges with direction, rationale, evidence basis, and evidence ids. A project-relationship edge records assertion anchors when its evidence ids support published history items at both endpoints. Project and base documents receive reciprocal `Related knowledge` navigation only for confirmed document edges. The query command returns evidence rules, matching base/project documents, and at most the requested number of one-hop confirmed related documents.

`archive/<run-id>/` contains prior generated project documents absent from the current reviewed publication, including documents made stale by title/path changes. Archive creation rejects symlinked stale sources, archive parents, run directories, destinations, or resolved containment escapes. Archives are not listed as current index documents and the normal Reader rejects their paths rather than retrieving them. They remain private historical material and retain the same privacy handling as current knowledge.

The collaboration document may contain confirmed scoped claims and active (`approved` or `validated`) feedback-governed rules. Candidate and rejected changes remain non-active records: their detailed audit and graph metadata may be retained, but they are not rendered as current behavior rules. `approved` means active but awaiting behavior evidence; only `validated` may be described as evolved. A Reader must still check `rule_scope` and `applies_to` against the current task.

## Audit layer

The output also includes `audit/stats.json`, `audit/completion-report.json`, `audit/impact-report.json`, `audit/compatibility-matrix.md`, `audit/unsupported-formats.json`, `audit/coverage-gaps.json`, `audit/errors.jsonl`, `audit/excluded.jsonl`, `audit/actor-attributions.json`, `audit/claims.json`, `audit/feedback-signals.json`, `audit/rule-evolutions.json`, `audit/relationships.json`, `audit/relationship-candidates.json`, `audit/link-audit.json`, `audit/published-files.json`, `audit/stale-project-documents.json`, and—after the final check—`audit/retrieval-verification.json`.

Counts distinguish discovered, frozen and parsed files, retained events, duplicates, excluded records, redactions, binary replacements, parser bytes (`bytes_parsed`), integrity I/O (`integrity_bytes_read`), their incremental variants, reparsed files, cumulative source transport denominators, and errors. A zero parser error count does not imply semantic review is complete.

Likewise, zero reported coverage gaps means zero gaps inside the declared discovery contract. It does not prove that arbitrary file types, inaccessible accounts, vendor clouds, unmounted disks, or locations excluded by discovery heuristics were searched.

`audit/coverage-gaps.json` records roots that were not fully probed because a bounded `--max-files` run stopped discovery. A coverage gap is not mislabeled as an unsupported format, and it blocks semantic publication until a complete discovery run is frozen.

`audit/completion-report.json` is the only machine-readable completion authority. `audit/impact-report.json` lists added/removed events and affected project keys after an incremental run, invalidating old semantic assumptions without rereading unaffected projects blindly.

`distill` sets the completion status to `needs_retrieval_verification`; semantic publication alone is not final. `verify-retrieval` must prove that one relevant task selects reviewed project knowledge and one unrelated task returns `no_match`. Its audit stores task hashes, redaction counts, selection summaries, pass/fail states, and a size/hash manifest of indexed knowledge files, not the task text or selected document contents. The completion report stores the manifest hash. Only after both gates pass may completion become `complete` or `complete_with_unsupported_formats`.

The Reader and registry recompute that manifest. Any post-verification edit, replacement, or deletion fails closed instead of letting the old retrieval result attest to changed knowledge. Re-distill or otherwise restore a reviewed publication, then rerun retrieval verification.

When a prior review contributes carried records, the completion gates also require `cross_project_recheck`. This covers links, repeated-context claims, conflicts, supersession, global scopes, and feedback-governed rules that may depend on more than one project.

`audit/relationships.json` contains every graph edge. `audit/relationship-candidates.json` preserves uncertain/rejected or non-publishable project relationship records. `audit/link-audit.json` accounts for every published document's confirmed-link count and each intentional isolate rationale. The `knowledge_graph_complete` gate must pass before publication is complete.

`audit/published-files.json` is the current generated-path ledger used to detect stale project documents on the next distillation; it is distinct from the size/hash publication manifest created by retrieval verification. `audit/stale-project-documents.json` records each old path, archive destination, and `archived` or `already-missing` status. The completion report counts archived stale project documents.

## Private local location registry

The optional local registry maps a user-chosen name to a verified knowledge-base root. It lives in the user's configuration area and is never part of this output tree or the public Skill source pack. Registration is an explicit post-verification action. Registry mutation uses its own standalone sibling lock and atomic file replacement, separate from the knowledge-base lock. The companion Reader refuses draft, merely distilled, retrieval-unverified, run-id-mismatched, or otherwise incomplete knowledge bases and can use either a registry name or an explicit path.
