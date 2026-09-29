# Selective reading and authorized task-end maintenance

## Keep two independent decisions

Read decision: does the current answer require a specific personal fact, prior decision,
or project state absent from the current conversation and supplied artifacts?
If not, do not activate the Reader or open private knowledge. A task can use zero
personal memory and still produce useful new knowledge.

Write decision: did this task produce a durable, evidenced change, and did the user
authorize maintenance of this exact private destination? A registered path or a
successful read is not write permission. Save standing permission only after the
user requests automatic updating; retain its scope, destination and revocation.
Never assume all users who install this pack consent to automatic writes.

## Small host instruction template

Adapt only the approved destination and scope, without copying a user's history
into a public Skill. Place this in the host's always-loaded instructions only with
user approval:

> Use current context first. Retrieve private history only for a named missing fact;
> generic writing, research, coding, company names and "continue" do not suffice.
> At a substantive task's handoff, check for new durable facts, explicit corrections
> or observed project results. With my standing maintenance permission, maintain
> only the approved private knowledge base. No meaningful delta means no write and
> no scan. Preserve date, source, scope, old state and uncertainty. Do not promote
> guesses, tool noise or artifact-specific feedback into identity or global rules.
> Report "updated" only after the actual write and required verification. If blocked,
> state the pending change and reason; never claim an inbox item is published.
> If I identify a source/session directly, use it without loading unrelated
> biography. Keep corrections attached to the target version; distinguish my
> requirements from Agent suggestions and preserve unchanged requirements.

The user may disable automatic maintenance or narrow it to specific projects.
Their latest request overrides this template, including "do not remember this".
Do not demand a new permission for each low-risk write already covered by standing
authorization. External publishing, deletion, credential use and new data sources
still need their own authority.

## What to retain

Apply [collaboration-learning.md](collaboration-learning.md) when distilling
interaction patterns. Reusing those review rules does not itself require a KB read.

- A project entry leads with user intent, what was made, observed result, latest
  correction and remaining work. Test totals and command traces support the result;
  they do not replace the project narrative.
- Separate an intended action, an attempted action, a tool-observed result, and
  user acceptance. A passed build is not proof that the product was accepted.
- Resolve project boundaries by explicit continuation, artifacts and stable
  identities. Agent workers and individual sessions are not automatically projects.
- Store an explicit preference at its narrowest supported scope: artifact,
  project, task-type, then global. Repetition in injected prompts is not repeated
  human endorsement. Scope expansion uses the existing evolution approval gate.
- Identity changes, ambiguous referents, conflicts and inferred preferences need
  source review. Do not silently overwrite biography or invent an update.
- Sensitive non-credential events can stay in an approved private subfolder.
  Passwords, tokens, cookies, private keys and raw login material never belong in it.

## Execute the update, not just its intention

1. Check for a meaningful change from this task before reading disk. Skip routine
   questions, duplicate facts, failed retries with no new durable implication, and
   the maintenance operation's own telemetry.
2. Locate only the affected project/base records. Establish the actual primary
   human or observed artifact behind each change. Retain event IDs or exact native
   session/turn references. Tool wrappers, quotes and Agent summaries are not
   primary-user evidence.
3. Choose the destination's actual maintenance mode. Human-maintained pages need
   a sourced delta, not a session inventory, freeze or full reconstruction. Use
   existing source authorization; a triggered update never expands it to all disks
   or accounts. If no meaningful delta remains after checking the target, stop.
4. For machine-generated knowledge, read [reconstruction.md](reconstruction.md),
   freeze once at the task's evidence boundary, save the prior review outside generated output,
   run `rebuild --incremental` with a fresh immutable snapshot, then
   `review-init --from-review`. Reuse only hash-valid reviews/chunks. Read reopened
   ranges, synthesize affected project chains and complete the cross-project recheck.
   Run validate-review, distill and the retrieval suite. Use the existing CLI help
   and contracts for arguments; none of these steps is replaced by this template.
5. The existing runtime invalidates publication during rebuilding. It does NOT
   provide atomic candidate-to-active switching. Do not simultaneously promise
   uninterrupted Reader service, and do not bypass invalidation by reading old or
   draft files. Schedule this work at a safe handoff and report failures honestly.
6. For a separately user-maintained Markdown knowledge base, use its own reviewed
   source and writing contract: read the target, add a dated sourced delta, retain
   history, deduplicate, check the diff and verify it was saved. Never hand-edit
   generated published files or fake their manifest/gates to simulate this mode.
   For authorized append-only deltas that need crash recovery, use
   [manual-maintenance.md](manual-maintenance.md). Configure exact target pages,
   stage the sourced delta, apply it and verify the receipt. Do not install or
   configure this queue for an unconsenting user; it is not a scheduler.
7. In the final handoff, one short line states what changed, or why a meaningful
   change remains pending. Do not list maintenance internals when nothing changed.

Do not turn a pending draft or queue item into "updated". If the task's useful
artifact is ready but maintenance is blocked, deliver the artifact and identify
the pending delta without claiming the knowledge is current. Recovery resumes
that delta and its missing gate; it does not restart the original user task.

Task-end execution is instruction-driven, not a guaranteed hook on every message.
If the host stops early, the update can be missed. A durable background job requires
an actual scheduler and separately approved source/output scope; do not install one
silently. This pack does not ship a daemon or cross-host session-end hook.

## Behavioral acceptance cases

Skip private reads: generic CSS style debugging; voice-tool installation; a supplied
article rewrite; a cover using supplied copy; a shell-command explanation; "continue"
when the full working state is already in this chat.

Read selectively: "What did we decide about the earlier research assistant?"
when the decision is absent; "Use my established writing voice" when its rules are
absent; an explicit request for a personal retrospective.

Write independently of reading: an observed deployment result or direct project
correction under standing permission. Skip duplicates and no-delta tasks. Preserve
an unfinished deployment as unfinished. Keep "this cover should be square" local to
that artifact. Never infer global visual preference from it.

Evaluate these as routing/maintenance behavior in the host as well as retrieval
unit tests. Passing unit tests does not prove the host selected or obeyed a Skill.
