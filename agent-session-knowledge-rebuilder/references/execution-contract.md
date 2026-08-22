# Execution and truth-reporting contract

Use this reference before inventory and again before reporting completion. It governs how an Agent executes the Skill; format schemas remain in the other references.

## What automation means

The Python runtime performs deterministic discovery, parsing, sanitization, hashing, accounting, validation, rendering, and retrieval. The executing Agent performs semantic reading and judgment. It must continue through that work itself instead of returning a review template to the user.

Do not describe the Python command alone as an intelligent reconstruction. Do not describe an Agent-written review as deterministic. The combination is the product.

## Keep three compatibility axes separate

1. **Source-format compatibility** means an exact session format has a verified adapter and real-sample smoke evidence.
2. **Execution-host compatibility** means a particular Agent product can discover, load, and carry out this Skill's semantic workflow.
3. **Operating-system validation** means the complete workflow has run in a real environment on that operating system.

The portable Python CLI does not prove native Skill discovery in every Agent. Automated Windows, macOS, and Linux path tests do not prove real end-to-end operation on all three systems. State each axis separately in inventory and final reports.

## Decisions and authority

Continue autonomously once the source scope and output path are known. Do not ask the user to choose parsing details, evidence grades, filenames, link labels, or ordinary review tactics.

Pause only when:

- the private output location has not been confirmed;
- a missing root, export, credential, or inaccessible account prevents further read-only evidence collection;
- two interpretations would materially change the knowledge base and the evidence cannot resolve them;
- installation, publication, deletion, source modification, or another action requires new authority.

An output-path confirmation authorizes writes only to that private output and its optional snapshot location. It does not authorize source changes, registry writes, software installation, uploads, or public release.

## Coverage ledger

Inventory is an evidence-building pass, not a filesystem oracle. Record:

- every root requested or automatically probed;
- whether it existed and was readable;
- whether it was a known, generic, environment-supplied, or explicit root;
- candidate, scanned, supported, unknown, ambiguous, and unreadable counts;
- any file, depth, suffix, directory, permission, account, mount, or `--max-files` boundary that constrained discovery.

“All accessible sessions” means all supported candidates inside this declared denominator. It never means encrypted stores, another account, unmounted disks, vendor cloud history, or formats hidden outside the reported search contract.

When the user asks for niche Agents, inspect accessible installed-command evidence and platform application/configuration roots, then accept explicit roots or exports. Do not install an Agent, bypass access controls, scrape another account, or upload private samples merely to improve coverage.

## Project-chain reconciliation

Deterministic `project_key` values are routing proposals. Before semantic attestation, compare the proposed chains using, in descending strength:

1. explicit user statements that one effort continues, replaces, or belongs to another;
2. native session, parent, continuation, or stable project identifiers;
3. a concrete shared artifact, repository, output, dependency, correction, or observed handoff;
4. working directory and titles only as supporting hints.

Never merge solely by directory, title, broad type, or keyword overlap. Never split a continuation solely because its path or Agent changed. If the current evidence/review structure cannot faithfully represent a required merge or split, do not attest or publish that project set. Report the grouping blocker instead of using a graph link to disguise it.

## Long-chain reading protocol

A packet is complete only when its event count and ordered-event hash match the review template. If a tool or context window truncates it:

1. keep the complete packet or evidence file inside the approved private knowledge-base area;
2. read consecutive, non-overlapping ranges in source order;
3. checkpoint the next event index, previous and next event ids, cumulative count, and packet hash;
4. resume from the exact checkpoint after interruption;
5. mark the project reviewed only after the final range and count/hash comparison pass.

Search, keyword ranking, high-signal event lists, and summaries may route attention. They never replace the surrounding ordered events or satisfy the attestation.

## Progress and stopping conditions

For a long run, report concise evidence-based progress: current gate, frozen denominator, projects and events semantically reviewed, exact checkpoint for the active chain, errors/fallbacks, and the next boundary. Do not give an unsupported completion percentage or time estimate.

Stop publication when deterministic coverage, parse accounting, project grouping, complete-chain reading, claim provenance, privacy, or graph validation is unresolved. An explicit unsupported or unresolved result is valid output; a plausible invented bridge is not.

## Definition of done

A normal successful run has:

- a frozen, read-only source denominator;
- accounted transport records and explicit exclusions/errors;
- complete semantic dispositions for every retained event;
- evidence-bound project histories and base claims;
- confirmed, uncertain, rejected, or intentional-isolate relationship outcomes;
- published knowledge that passes validation;
- one relevant retrieval check and one unrelated `no_match` check;
- a final report that preserves unsupported formats, operating-system and host limits, privacy results, fallbacks, and remaining work.

`complete_with_unsupported_formats` means supported evidence was published after the unsupported candidates were inspected and acknowledged. It never upgrades those candidates to compatibility.
