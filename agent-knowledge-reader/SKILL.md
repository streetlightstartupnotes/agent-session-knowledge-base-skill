---
name: agent-knowledge-reader
description: Retrieve verified personal history only when an explicit recall request or a missing past decision, personal fact, or established preference is necessary to answer. Skip when the current conversation or supplied files suffice. Generic writing, coding, research, project names, and continuing the current task alone do not trigger this skill. Read-only; never rebuild or update.
---

# Agent Knowledge Reader

Retrieve the smallest evidence-backed context set needed for the current task. This Skill is read-only: it does not rebuild sessions, edit knowledge, apply lifecycle changes, promote feedback, approve rules, register paths, or invent a bridge when retrieval returns no match.

## Decide necessity before any private I/O

First use the current request, conversation and supplied files. Internally name the exact missing fact and how it changes the answer. If none is missing, do not open the registry, base documents or histories. A mention of a company, product, "my computer", writing, CSS style, voice software or "continue" is not a personal-memory request.

Explicitly recalling an earlier decision, applying a previously established personal voice, or continuing a project whose relevant state is absent can justify retrieval. Query the missing fact and concrete project name, not the entire prompt. Current explicit instructions win over past preferences.

An explicitly supplied session, repository, draft or asset is a direct source, not
a reason to preload a personal profile. If the user asks to read that source, read
it within scope; use the KB only if a separate necessary gap remains. Stop unrelated
history retrieval when the user says it is unnecessary. Retrieval scores rank
documents after this decision; a named project match cannot prove necessity.

If an orchestration layer has already dispatched the Reader although context suffices, use the no-I/O escape:

```text
<python> scripts/read_knowledge.py query --task "current task" --context-sufficient
```

It returns `skipped` with zero documents without resolving a registry or knowledge base. It makes no publication claim or usage receipt. Prefer not invoking the Reader at all.

## Choose the needed context, then query

Only after necessity is established, read
[references/retrieval-contract.md](references/retrieval-contract.md) for resolving
an approved library, querying it and enforcing publication gates. Refusal is not
permission to open drafts or rebuild the library inside this read-only Skill.

Use `--base-context none` for a missing project decision; use `identity`,
`collaboration` or `both` only when those facts are actually needed. This selection
is semantic and language-independent: a background worker is not personal
background. It also prevents graph expansion from adding excluded base documents.
Evidence rules still accompany a real match. `none` limits the returned context,
not the integrity checks required to validate the publication.

Omitted options inherit the verified suite selection; older publications default
to `auto`. That mode uses limited Chinese/English phrase inference, not a general
intent classifier. Overrides appear in the usage receipt and are marked outside
the suite's selection. Never use `both` as a convenience default. Unicode
lexical matching supports more scripts, but does not translate or establish
semantic relevance; the Agent still decides necessity and query wording.

For a narrow fact or project continuation, read
[references/context-views.md](references/context-views.md) and prefer `--view facts`
or `--view current` when that publication supports it. Expand to full documents
when omitted evidence, conflict or the user's requested scope requires it. Never
silently treat missing current-state fields as facts or claim a compact view is
a complete retrospective.

## Apply only active, confirmed, in-scope rules

For a matched query, read the returned evidence rules first. Add identity or collaboration units/documents only when the missing fact needs them, then read the selected project units or history needed to close that gap. Do not expand a sufficient compact answer into full history by ritual, or load all three base documents by default. Reuse already-read context within the task while the publication and question remain unchanged. For an explicitly complete project retrospective, expand to every relevant version chain and read it completely; bounded retrieval is not proof of exhaustive coverage.

When a collaboration or expression document is selected:

- Apply a base rule only when it is `confirmed` and its `rule_scope` plus `applies_to` cover the current task.
- Apply a feedback-governed rule only when its evolution is active (`approved` or `validated`), its promoted claim is confirmed, and its scope covers the task.
- Treat `approved` as active but awaiting behavioral proof. Only `validated` may be called evolved.
- Never apply `candidate`, `rejected`, `disputed`, `retracted`, `stale`, forgotten, or out-of-scope material as instructions.
- Never widen an artifact-, project-, or task-type rule merely because the current task looks similar.
- The user's latest explicit instruction overrides older knowledge. Preserve and report a dated conflict instead of smoothing it away.
- Bind corrections to the actual draft/version and recipient. Keep the original
  purpose, audience and exclusions when a later message only adds a requirement.
- Preserve result scope: configured, tested, real-use observed and user-accepted
  are different; old successes or Agent summaries cannot overrule a later failure.
- For writing, match a preference's actual author, genre, language and channel.
  The requester, narrator, reviewer and reference author may differ. A style sample
  supplies no biography; an old local edit is not a universal writing rule. Current
  supplied prose and explicit transformation take priority over a retrieved style.

Reading and maintenance are independent. A task may need no personal history yet produce a durable project result. Under an explicit standing maintenance authorization, the host's task-end check may invoke the Rebuilder afterwards; this Reader itself remains read-only. See the Rebuilder's host-integration contract.

`no_match` means this published index did not find sufficiently relevant maintained knowledge for that wording. It does not prove that no raw session ever mentioned the subject. Retry with a more precise task only when useful; never silently load the whole knowledge base or invent missing context.
