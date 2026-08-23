# Compatibility matrix

Compatibility is evidence-bound and has three independent axes. The runtime also writes an environment-specific matrix to `audit/compatibility-matrix.md`.

| Axis | A passing claim proves | It does not prove |
| --- | --- | --- |
| Input format | One exact session representation passed recognition, parsing, sanitization, accounting, and real-sample ingest gates | Other formats, vendor versions, or every product using a similar filename |
| Execution host | One Agent host discovered/invoked the Skill and completed semantic review, handoff, and retrieval verification | That every Agent host can discover Skills or execute the full workflow |
| Operating system | The declared workflow was exercised in a real environment on that system | Other systems merely because path construction is portable or unit-tested |

## Verified input formats

The table below covers the input-format axis only.

Row order is not an input, host, or operating-system priority.

| Agent / exact format | Adapter | Implementation | Verification status |
| --- | --- | --- | --- |
| Codex rollout JSONL | `codex-jsonl` | Implemented | Verified with unit/contract tests, real-sample ingest, and a reviewed-no-knowledge publish/query smoke |
| Clacky session JSON | `clacky-json` | Implemented | Verified with unit/contract tests and real-sample ingest/review-template hashing |
| Clacky chunk Markdown | `clacky-chunk` | Implemented | Verified with unit/contract tests, real-sample ingest, and a reviewed-no-knowledge publish/query smoke |
| Claude Code project JSONL | `claude-code-jsonl` | Implemented | Verified with unit/contract tests and real-sample ingest/review-template hashing |
| WorkBuddy project JSONL | `workbuddy-jsonl` | Implemented | Verified with unit/contract tests and real-sample ingest/review-template hashing |
| Neo Claude-compatible project JSONL | `neo-claude-jsonl` | Implemented | Verified with unit/contract tests and an isolated real-sample ingest/review-template smoke |
| Cursor `agent-transcripts` JSONL | `cursor-agent-jsonl` | Implemented | Verified with unit/contract tests and an isolated real-sample ingest/review-template smoke |

## Explicitly unsupported or unverified

| Candidate | Current evidence | Accurate status |
| --- | --- | --- |
| Grok session updates JSONL | A candidate root can be probed; no readable transcript sample was available in the validation environment | Unsupported / sample missing |
| OpenCodex local state | No transcript sample was found in the validation environment | Unsupported / sample missing |
| Any other Agent or session representation | Extension contract only; no exact adapter and evidence gate | Unsupported until a real sample, implementation, tests, and exact-format smoke exist |

An adapter registry is extensibility, not universal compatibility. A familiar JSONL shape, installed command, application directory, or provider name never upgrades an unknown candidate.

## Host and system boundaries

This pack uses a `SKILL.md` workflow plus a Python 3.9+ standard-library CLI. An execution host may be listed as compatible only after its Skill discovery/invocation, Agent-run semantic review, retrieval verification, and handoff have been exercised together. Being able to open `SKILL.md` or run the CLI is insufficient.

Registry and discovery path construction have automated tests for Windows, macOS, and Linux path families. Current real-format end-to-end sample evidence was collected on macOS. Windows and Linux still require real-environment end-to-end runs before an operating-system validation claim is allowed. These are validation boundaries, not product or system preferences.

Update this file only after the named exact format, host, or system passes its corresponding gate in [adapter-contract.md](adapter-contract.md).
