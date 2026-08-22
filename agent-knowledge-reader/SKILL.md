---
name: agent-knowledge-reader
description: Load only the reviewed personal, project, and collaboration knowledge relevant to the current task from a registered Agent-session knowledge base. Use after agent-session-knowledge-rebuilder has published and optionally registered a knowledge base; refuse drafts and return no-match instead of inventing context.
---

# Agent Knowledge Reader

Retrieve a bounded evidence-backed context set. This Skill reads knowledge; it does not rebuild sessions, edit the knowledge base, or turn missing evidence into a plausible answer.

## Choose the Python launcher

Use `py -3` on Windows when available and `python3` on macOS/Linux. Replace `<python>` below with that launcher. Python 3.9 or newer is required; no third-party package is required. Do not install or upgrade it without permission.

## Resolve the knowledge base

Prefer a user-confirmed registry name. List local registrations when the user did not name one:

```bash
<python> scripts/read_knowledge.py list
```

If no registration exists, ask for the published knowledge-base path. Do not scan the whole home directory or guess from project names. Registry paths are local configuration and must never be committed with the public Skill source pack. Read [references/registry-contract.md](references/registry-contract.md) when configuring or troubleshooting locations.

## Read for a task

```bash
<python> scripts/read_knowledge.py query --name default --task "the current task" --max-projects 3 --max-related 2
```

Use `--kb /absolute/path` instead of `--name` for a one-off read. Add `--emit-content` only when the selected documents should enter the current context.

The reader must:

- reject a knowledge index whose semantic status is not `published`;
- always include the evidence rules;
- add identity or collaboration documents only when the task needs them;
- select a small number of matching project histories;
- follow at most the requested number of evidence-backed graph links;
- report `no_match` when nothing relevant exists;
- preserve disputed, stale, retracted, incomplete, and unsupported states.

Do not treat a linked document as proof that every statement transfers across projects. Relationship labels and evidence ids explain why the navigation link exists; the target document keeps its own scope.

Retrieval currently uses the reviewed index's titles, aliases, and keywords plus confirmed one-hop links. A `no_match` result means the indexed query did not find a sufficiently relevant document for that wording; it does not prove that no raw session ever mentioned the subject. Report `no_match` or retry with a more precise task phrase. Never respond by silently loading the whole knowledge base or inventing missing context.
