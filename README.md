# Agent Session Knowledge Base Skill Pack

Privacy-first Codex Skills for rebuilding a maintainable, evidence-bound knowledge base from supported Agent session archives, then loading only the published knowledge relevant to a task.

Current release: **v0.3.2 (Beta)**

## What is included

- `agent-session-knowledge-rebuilder`: discovers or accepts explicit session roots, normalizes supported formats, removes private/binary material, merges continuation chains, produces an auditable review workspace, and publishes reviewed personal/project/collaboration knowledge with evidence-bound bidirectional links.
- `agent-knowledge-reader`: retrieves a bounded set of published knowledge for the current task and returns `no_match` instead of loading everything or inventing context.

The Python layer handles deterministic extraction, sanitization, audit records, validation, incremental state, and retrieval. The invoking Agent performs the semantic review; publication is blocked until that review satisfies the evidence contract.

## Verified input formats

The following exact formats have implemented adapters and test evidence:

- Codex rollout JSONL
- Clacky session JSON and chunk Markdown
- Claude Code project JSONL
- WorkBuddy project JSONL
- Neo Claude-compatible project JSONL
- Cursor `agent-transcripts` JSONL

Unknown formats are reported as unsupported. An extension contract or a discovered application directory is not presented as verified compatibility. See [the compatibility matrix](agent-session-knowledge-rebuilder/references/compatibility.md) for the evidence level and known candidates.

## Requirements and installation

- Codex is the only natively packaged and verified Skill host in this release.
- Python 3.9 or newer is required. The CLI uses only the standard library.
- The source archives are read-only by default. The tool never modifies or deletes original sessions.

Copy both Skill directories into your Codex Skills directory, keeping their independent names:

```text
agent-session-knowledge-rebuilder/
agent-knowledge-reader/
```

Then invoke `$agent-session-knowledge-rebuilder`. It inventories accessible sources and asks where the private knowledge base should be written before creating it. After a reviewed knowledge base is published, invoke `$agent-knowledge-reader` to load task-scoped context.

For direct CLI help:

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py --help
python3 agent-knowledge-reader/scripts/read_knowledge.py --help
```

## Safety and auditability

The rebuild pipeline separates user, Agent, subagent, test, customer, third-party, and runtime roles. It quarantines compacted summaries, injected runtime instructions, old-memory transcripts, delegated transcripts, and nested approval transcripts. It also deduplicates parallel representations, strips binary/Base64 bodies, redacts common credentials and personal contact data, freezes source metadata, and records fallback/error/coverage reports.

Run the release gate before publishing a fork:

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py release-check --root .
```

## Current boundaries

- Real-format smoke evidence was collected on macOS. Windows and Linux discovery/path behavior has automated coverage, but those operating systems have not had release-environment end-to-end validation.
- Semantic correction of wrongly merged or split project chains is not automated; publication must stop and report the grouping blocker.
- Large review packets use a documented Agent-side checkpoint protocol rather than native CLI pagination.
- Auto-discovery is heuristic and bounded to reported roots. Explicit exports remain the reliable fallback.
- Reader matching is lexical over reviewed metadata plus confirmed one-hop links; `no_match` does not prove the raw archive never mentioned a subject.

See each Skill's `SKILL.md` and reference contracts for the exact workflow and completion gates.

## Tests

The repository includes synthetic unit/contract tests for format recognition, unified events, deduplication, continuation merging, summary exclusion, identity isolation, privacy cleaning, binary removal, incremental freezing, review/link validation, retrieval, and the output contract.

## License

MIT. See [LICENSE](LICENSE).
