# Adapter contract

Use this reference when implementing or reviewing a session adapter.

## Registration

Built-in adapters are registered in `scripts/session_kb/adapters.py`. An explicitly supplied `--adapter-dir` may contain Python modules with:

```python
def register(registry):
    registry.register(MyAdapter())
```

Loading an adapter executes that module. Never auto-load adapter code from a discovered session root.

Environment inventory is broader than adapter registration. It probes platform configuration/data roots, known Agent roots, Agent-like home/application directories, installed command names, and explicit roots. An unknown candidate receives a structural fingerprint made only of container type, field names, and value types. Never include record values in an inventory report. Discovery is not permission to install an Agent, unlock another account, bypass operating-system access controls, or upload a sample.

## Discovery coverage contract

Automatic discovery is intentionally bounded by the implementation's current root hints, filename/container candidates, depth limits, ignored directories, permissions, and file-count limits. A scan marked complete proves that every candidate inside those declared bounds was examined; it does not prove that every file on the machine or every vendor cloud session was examined.

Inventory reports must preserve the roots and their candidate/scanned counts. If a likely Agent store falls outside the automatic contract, add it through an explicit `--root`, `--source`, export, or `AGENT_SESSION_ROOTS` value. If it remains inaccessible or filtered, report that as a coverage boundary rather than returning zero gaps and implying exhaustive discovery.

An installed command, application bundle, configuration directory, and readable transcript are four different observations. List them separately. Do not infer a session format from an application name, and do not claim compatibility when only installation evidence exists.

An adapter has a unique lowercase `name`, a human-readable `agent_name`, `append_only` semantics, a `probe(path, head)` method, and a `parse(data, source, context)` method. `probe` returns a score from 0 to 100 and a short reason. A score of zero means unsupported. The highest unambiguous score wins; ties are reported instead of guessed.

## Parse result

`parse` returns normalized raw events, safe continuation context, parse errors, excluded-record audit entries without excluded content, and the last complete byte offset.

Every raw event must identify the source record locator, logical session identity, role, event type, visible content, and any call/parent linkage. Select metadata fields explicitly; do not dump provider objects that may contain credentials or injected context.

Set `metadata.transport_lane` when a format serializes the same visible event in more than one lane. Mark the less authoritative lane `possible_duplicate`. The shared deduplicator pairs known mirrors by occurrence and preserves genuine repeated messages.

## Unified roles and event types

Roles are `user`, `assistant`, `tool`, `system`, `observer`, or `unknown`. The pipeline refines them into actor kinds such as `primary_user`, `primary_agent`, `subagent`, `orchestrator`, `third_party`, `test_actor`, `runtime`, and `unknown_user`.

Prefer `message`, `tool_call`, `tool_result`, `patch`, `browser`, `device`, `attachment`, `status`, `delivery`, and `error`. Add a new type only when it changes downstream evidence handling.

## Exclusion and isolation requirements

Adapters must identify format-native compacted summaries, hidden reasoning, system/developer context, duplicated history records, and sidechains. The shared pipeline additionally detects runtime wrappers, external imports, delegated prompts, test roles, nested approvals, old-memory reads, sensitive data, and binary bodies.

A serialized `user` role is not automatically the primary human. Sidechain user messages, tool results encoded as user blocks, client prompts, embedded transcripts, and imported sessions must carry a non-primary actor hint or risk flag. When provenance is ambiguous, use `unknown_user`; never guess.

## Incremental requirements

Declare `append_only=True` only for a format whose files append complete records. The pipeline verifies the old head and tail boundaries before starting at the saved complete-byte offset. If verification fails, it reparses the file and records the fallback. Whole JSON documents and mutable chunks must declare `append_only=False` and rely on event deduplication.

Append-only adapters are invoked on newline-aligned bounded chunks. Their continuation context must be sufficient to continue sequence, session, parent, title, and working-directory state without loading the whole file. A partial final line is excluded as an incomplete tail and is retried on the next run.

## Verification gate

An adapter is compatible only after recognition and malformed-input tests, required unified-event fields, visible message/call/result/delivery handling, summary/runtime/reasoning exclusion, identity isolation, privacy and binary sanitization, incremental or safe-reparse behavior, and an end-to-end smoke test on a real sample of that exact format.

For this format-specific gate, end to end means discovery or explicit selection through freeze, parse, sanitization, deterministic disposition, draft output, and review-template hashing. Semantic claim writing is deliberately human-reviewed and format-independent; its validate, publish, and query path must also have its own end-to-end tests, but a format sample must never be given invented claims merely to exercise that path.

If any step is absent, report `implemented-unverified`, `sample-missing`, or `unsupported` instead of `verified`.

Cross-platform code paths need automated path/root tests for Windows, macOS, and Linux. Real-sample validation on one operating system proves the format adapter, not every vendor version or operating-system integration. Record those boundaries in the compatibility matrix.
