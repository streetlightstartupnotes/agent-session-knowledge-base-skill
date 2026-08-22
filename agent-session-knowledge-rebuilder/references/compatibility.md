# Compatibility matrix

Compatibility is format-specific and evidence-bound. The runtime build writes the current environment-specific matrix to `audit/compatibility-matrix.md`.

Keep three claims separate: input-format compatibility, native execution-host compatibility, and real operating-system validation. The table below covers input formats only. This pack is natively packaged as a Codex Skill and also exposes a Python 3.9+ standard-library CLI. That CLI may be invoked from other environments, but no other Agent host may be described as Skill-compatible until its discovery, invocation, semantic-review, and handoff path has been exercised end to end.

| Agent / format | Adapter | Implementation | Verification status |
| --- | --- | --- | --- |
| Codex rollout JSONL | `codex-jsonl` | Implemented | Verified with unit/contract tests, real-sample ingest, and a reviewed-no-knowledge publish/query smoke |
| Clacky session JSON | `clacky-json` | Implemented | Verified with unit/contract tests and real-sample ingest/review-template hashing |
| Clacky chunk Markdown | `clacky-chunk` | Implemented | Verified with unit/contract tests, real-sample ingest, and a reviewed-no-knowledge publish/query smoke |
| Claude Code project JSONL | `claude-code-jsonl` | Implemented | Verified with unit/contract tests and real-sample ingest/review-template hashing |
| WorkBuddy project JSONL | `workbuddy-jsonl` | Implemented | Verified with unit/contract tests and real-sample ingest/review-template hashing |
| Neo Claude-compatible project JSONL | `neo-claude-jsonl` | Implemented | Verified with unit/contract tests and an isolated real-sample ingest/review-template smoke |
| Cursor `agent-transcripts` JSONL | `cursor-agent-jsonl` | Implemented | Verified with unit/contract tests and an isolated real-sample ingest/review-template smoke |
| Grok session updates JSONL | none | A candidate root is probed, but no readable transcript sample was present | Unsupported / sample missing |
| OpenCodex local state | none | No transcript sample found in the validation environment | Unsupported / sample missing |
| Any other Agent | extension contract only | Not implemented | Unsupported until a real sample and adapter test exist |

An adapter framework is not evidence of compatibility. Update this file only after the named format has passed the gate in [adapter-contract.md](adapter-contract.md).

Discovery and registry path construction have automated Windows/macOS/Linux tests. Current real-format smoke evidence was collected on macOS; Windows and Linux still require release-environment end-to-end runs before claiming operating-system-specific validation. The implementation uses only the Python standard library and accepts explicit exports on every platform, but Python 3.9+ must already be available or be installed with separate permission.
