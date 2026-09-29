# Read-only retrieval and publication gates

## Choose a launcher

Use an available Python 3.9+ launcher appropriate to the environment. Replace `<python>` below with it. Only the standard library is required. Do not install or upgrade a runtime without permission.

## Resolve a user-approved knowledge base

When the user supplied no name or path, list local registrations:

```text
<python> scripts/read_knowledge.py list
```

Use a confirmed registration name, or ask for the published knowledge-base path. Never scan the entire user directory or guess a location from project names. Read [references/registry-contract.md](registry-contract.md) when resolving, validating, or troubleshooting the registry.

Registration is a private local path mapping. It does not prove that knowledge is current, grant write permission, erase cloud-sharing risk, or authorize rules outside their scope.

## Query only for the current task

```text
<python> scripts/read_knowledge.py query \
  --name default \
  --task "the specific missing fact" \
  --base-context none
```

Use `--kb /user-approved/private/kb` for a one-off path. Add `--emit-content` only when selected documents should enter the current context. Omitted limits and selection options inherit the verified suite configuration. An override that differs from it marks `verified_profile_used: false`. Contract 3 binds base-context selection, view, unit budgets and engine version; contract 2 remains readable, but without an engine binding it cannot attest to current retrieval behavior. Publication safety gates apply to every view.

The command fails closed unless all applicable publication checks pass:

- the index and graph are `published` and belong to the same run;
- completion belongs to that run and publication passed;
- unsupported candidates are clear or explicitly acknowledged without being upgraded to compatible;
- a v0.5 knowledge base passed retrieval contract 2, including at least two related cases and two hard negatives;
- the exact passed retrieval-suite audit still matches the report hash bound into completion;
- current indexed knowledge files still match the manifest hashed during verification;
- graph document nodes exactly match the safe non-archive index allowlist and every edge endpoint exists;
- completion is `complete` or `complete_with_unsupported_formats`;
- no lifecycle transaction journal exists, completion is not `lifecycle-applying`, and no retract/forget is waiting for redistillation and renewed retrieval verification.

The legacy one-related/one-unrelated pair is diagnostic only for a v0.5 publication. Do not interpret `legacy_pair_passed_needs_suite` as completion. Do not bypass any refusal by opening draft, archived, old-run, `lifecycle-applying`, or lifecycle-pending files as maintained knowledge. A pending transaction must be resumed with its exact original private plan; ask the user to run `$agent-session-knowledge-rebuilder` through the missing review, distillation, lifecycle recovery, or retrieval-suite gate.

## Use the returned usage receipt

Every query returns `usage_receipt` alongside the selected documents. It contains:

- receipt version and knowledge-base run id;
- SHA-256 of the current task;
- `matched` or `no_match`;
- selected project keys and document paths;
- actual `query_parameters`, whether `verified_profile_used` is true, and the explicit `base_context` selection;
- publication-manifest SHA-256.

Use it when the current task needs to report which maintained knowledge was selected, or when auditing whether relevant context was retrieved before a separate behavior evaluation. The receipt is returned, not automatically written back. It does not prove that the Agent followed every selected rule, and it cannot approve, validate, or evolve one. The surrounding query output still contains the task text; only the receipt uses its hash.

## Respect retrieval and relationship boundaries

- `index.graph.path` is authoritative. Never substitute another graph found on disk.
- Require graph/index run equality, published status, a safe exact document allowlist, valid document paths, and existing edge endpoints.
- Follow only confirmed graph edges, never more than the requested bound, and never traverse `archive/`.
- A backlink is navigation, not a reversal of dependency, causality, correction, contradiction, or handoff.
- A related document keeps its own scope. Do not transfer all statements across an edge.
- Preserve disputed, stale, retracted, unsupported, incomplete, and completion-level boundaries in the answer.
- When no indexed project or task-specific base document meets the threshold, return `no_match` with zero documents, including zero evidence-rule documents. Generic tokens alone must not match a project.
