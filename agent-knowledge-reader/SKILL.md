---
name: agent-knowledge-reader
description: Load only the reviewed personal, project, and collaboration knowledge relevant to the current task from a verified Agent-session knowledge base. Use after agent-session-knowledge-rebuilder has completed publication and positive/negative retrieval gates. Refuse drafts, incomplete gates, and invented context.
---

# Agent Knowledge Reader

Retrieve the smallest evidence-backed context set needed for the current task. This Skill is read-only: it does not rebuild sessions, edit knowledge, promote feedback, change rules, register locations, or turn missing evidence into a plausible answer.

## Choose the launcher

Use an available Python 3.9+ launcher appropriate to the current environment. Replace `<python>` below with it. Only the standard library is required. Do not install or upgrade a runtime without permission.

## Resolve a user-approved knowledge base

When the user did not supply a name or path, list local registrations:

```text
<python> scripts/read_knowledge.py list
```

Use a confirmed registration name, or ask for the published knowledge-base path. Never scan the entire user directory or guess a location from project names. Read [references/registry-contract.md](references/registry-contract.md) when resolving or troubleshooting the registry.

Registration is only a private path mapping. It does not prove that the knowledge is current, grant write permission, or make a cloud/shared location private.

## Query for the current task

```text
<python> scripts/read_knowledge.py query \
  --name default \
  --task "the current task" \
  --max-projects 3 \
  --max-related 2
```

Use `--kb /user-approved/private/kb` for a one-off path. Add `--emit-content` only when the selected documents should enter the current context.

The command must fail closed unless all of these are true:

- `knowledge-index.json` is semantically `published`;
- the completion report belongs to the same run id;
- the knowledge publication gate passed;
- the relevant-task retrieval gate passed;
- the unrelated-task `no_match` gate passed;
- the current indexed knowledge files match the manifest hashed during retrieval verification;
- the graph selected by `knowledge-index.json` is published, belongs to the same run, and exposes exactly the safe indexed document allowlist;
- completion is `complete` or `complete_with_unsupported_formats`.

Do not work around a refusal by opening draft files manually as maintained knowledge. A missing or changed manifest means the publication changed after verification. Ask the user to run `$agent-session-knowledge-rebuilder` through review, distillation, and `verify-retrieval` as appropriate.

## Apply only active, confirmed, in-scope rules

Always read the evidence rules first. Add identity or collaboration documents only when the task actually needs them, then select a small number of matching project histories and at most the requested one-hop confirmed links.

When a collaboration or expression document is selected:

- Apply a base rule only when its status is `confirmed` and its `rule_scope` and `applies_to` cover the current task.
- Apply a feedback-governed rule only when its evolution state is active (`approved` or `validated`), its promoted claim is confirmed, and its scope covers the current task.
- Treat `approved` as active but awaiting behavioral proof. Only `validated` may be described as evolved.
- Do not apply `candidate`, `rejected`, `disputed`, `retracted`, or `stale` material as instructions. Preserve such records as caveats when they are selected and relevant.
- A task-, project-, or artifact-scoped rule never becomes global merely because the current task looks similar.
- The user's latest explicit instruction in the current task overrides an older knowledge rule; report the dated conflict instead of silently harmonizing it.

## Respect retrieval boundaries

- When `index.graph.path` is present, it is authoritative; never substitute another graph found on disk. Only the fixed default graph path is allowed for an index that omits the field.
- Require graph `semantic_status: published` and the same run id as the index. Every graph document node must correspond exactly to one safe, unique indexed document; node `document_path` values stay in that allowlist and every edge endpoint must exist.
- Follow only graph edges whose status is `confirmed`, and never more than the requested bound. Query and publication-manifest checks must use the same validated graph loader.
- Never traverse or search any path rooted at `archive/`, even when a graph node or edge points there. Archived project documents are historical stale outputs absent from the current published index.
- A backlink helps navigation; it does not reverse a directed dependency, causality, correction, or handoff.
- A linked document keeps its own scope. Do not transfer every statement across the relationship.
- Preserve disputed, stale, retracted, incomplete, unsupported, and completion-level boundaries in the response.
- When no indexed project or task-specific base document meets the threshold, return `no_match`.

Retrieval uses reviewed titles, aliases, keywords, and bounded confirmed one-hop links. `no_match` means the published index did not find a sufficiently relevant document for that wording; it does not prove that no raw session ever mentioned the subject. Retry with a more precise task only when useful. Never silently load the whole knowledge base or invent the missing bridge.
