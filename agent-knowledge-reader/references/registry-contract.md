# Local knowledge-base registry

The Reader and Rebuilder share registry version 1:

```json
{
  "registry_version": 1,
  "default": "personal",
  "knowledge_bases": {
    "personal": {
      "path": "/user-approved/private/location",
      "registered_at": "ISO-8601 timestamp",
      "run_id": "verified run id"
    }
  }
}
```

The runtime chooses the current environment's user-configuration directory. Current defaults are `%APPDATA%/agent-session-knowledge-base/locations.json` for the Windows path family, `~/Library/Application Support/agent-session-knowledge-base/locations.json` for the macOS path family, and `${XDG_CONFIG_HOME:-~/.config}/agent-session-knowledge-base/locations.json` for the Linux path family. `AGENT_KB_REGISTRY` provides an explicit override. This path strategy does not by itself prove that the full Skill workflow was validated on every operating system.

## Registration gate

Registration is allowed only when:

- `knowledge-index.json` is `published`;
- its run id matches `audit/completion-report.json`;
- the prerequisite gates `frozen_snapshot`, `transport_accounted`, `parse_clean`, `discovery_coverage_complete`, `semantic_review_complete`, `knowledge_graph_complete`, and `published_knowledge` all passed;
- unsupported candidates are clear or explicitly acknowledged without being treated as compatible;
- for a v0.5 retrieval-contract-2 publication, both `retrieval_related_suite` and `retrieval_hard_negative_suite` passed, with at least two distinct cases in each class;
- the passed retrieval-suite report belongs to the same run and matches `retrieval_verification_sha256` in completion;
- the current indexed knowledge files match the manifest hashed during retrieval verification;
- the graph selected by the index is published, belongs to the same run, and its document nodes exactly match the safe non-archive index allowlist;
- completion is `complete` or `complete_with_unsupported_formats`.

The Reader repeats those checks on every query. Reader and registration also refuse `audit/lifecycle-transaction.json` before trusting completion: a transaction may be interrupted while live files are changing, even if an older registry entry exists. Resume it with the exact original plan; do not remove the journal to force registration. The legacy one-related/one-unrelated pair is diagnostic only for a v0.5 publication and cannot replace the suite. A stale registry entry cannot turn a draft, an older run, a merely distilled, `lifecycle-applying`, or lifecycle-pending knowledge base, a failed retrieval suite, or post-verification file edits into maintained knowledge.

`register-kb` updates the registry under a standalone sibling file lock and uses atomic replacement. This lock is independent of any individual knowledge-base lock because one registry can name multiple knowledge bases. A second registry writer fails closed. Inspect the lock's operation, process id, target kind, and creation time; verify that no writer remains before treating it as stale.

## Scope and authority

The registry is private local state. Never include it, generated knowledge, raw sessions, review files, or audit events in the public Skill source pack.

Registration proves only where a verified knowledge base was located at registration time. It does not:

- grant permission to edit or upload the knowledge base;
- prove that the path still exists or remains current;
- make a synchronized directory local-only;
- make a shared directory private;
- activate candidate, rejected, disputed, stale, or out-of-scope rules.

Before registering a cloud-synchronized or shared path, confirm the intended upload, access, retained-version, and sharing boundary. The Reader remains read-only and applies only active confirmed rules whose scope covers the current task.

## Per-query usage receipt

Every successful Reader query returns a `usage_receipt` with the verified run id, task SHA-256, match status, selected project keys, selected document paths, and publication-manifest SHA-256. The receipt is returned in command output and is not automatically persisted in the registry or knowledge base. It can show what context was selected, but it cannot prove that guidance was followed, approve an evolution, or validate behavior by itself. The surrounding query result still contains the task text; only the receipt hashes it.

Completion also stores the suite-level verified retrieval profile. When no threshold flags are supplied, Reader uses that profile. The receipt includes the actual `query_parameters` and `verified_profile_used`. An explicit override is allowed for a deliberate alternate query but is marked false and must not be described as the retrieval behavior that passed the suite.
