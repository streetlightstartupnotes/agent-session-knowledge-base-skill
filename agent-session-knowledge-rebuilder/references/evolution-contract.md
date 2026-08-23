# Feedback-governed evolution contract

Use this reference when semantic review encounters corrections, praise, rejection, missing behavior, observed outcomes, or a proposal to change future behavior. The goal is controlled learning, not automatic personality rewriting.

## State machine

```text
evidence event
  -> feedback_signal
  -> rule_evolution:candidate
  -> approval or bounded repeated-context claim
  -> versioned active rule
  -> later behavior check
  -> rule_evolution:validated
```

Only `validated` is an evolved rule. `approved` means active but awaiting proof. `candidate` and `rejected` remain non-active records and never guide the Reader.

## 1. Attribute the speaker first

Before using human feedback, approval, or first-person statements, resolve the native user lane through `actor_attributions`.

- Use `project-user-lane` only when one attribution safely covers that project's serialized user lane.
- Use `event` when speakers or roles vary inside the project.
- Cite the events that establish the actor and explain the basis.
- Leave unresolved speakers as `unknown_user`; their text cannot approve a rule or establish the primary user's preference.

## 2. Record feedback at its actual scope

Each `feedback_signals` item needs:

- stable `feedback-*` id and reviewed `project_key`;
- kind: `positive`, `negative`, `gap`, or `outcome`;
- object: the fact, judgment, method, structure, expression, risk, format, completion, or other target;
- scope: `artifact`, `project`, `task-type`, or `global`;
- bounded statement, `applies_to`, status, and evidence event ids.

Positive, negative, and gap feedback require semantically attributed primary-user evidence. Outcome feedback requires primary-user evidence or an observable grade-B event. A nearby approval applies only to the question it actually answers.

## 3. Create a candidate, not a silent rule

A `rule_evolutions` candidate records a stable `evolution-*` id, positive integer `rule_version`, cited feedback ids, prior rule if any, proposed bounded rule, rationale, and an observable expected behavior change. Approval or validation needs at least one cited feedback signal whose status is `observed`.

A candidate or rejected evolution must not contain an active `promoted_claim_id`. Its detailed record remains in the private audit and may have non-active graph metadata, but it must not appear as current Reader guidance.

## 4. Pass the promotion gate

There are two evidence routes, and they must not be conflated:

1. **Explicit evolution approval.** `approved` or `validated` needs semantically attributed primary-user approval evidence and an existing confirmed evidence/collaboration/expression claim. For collaboration/expression, the claim scope must equal the evolution scope. A global evolution additionally needs observed feedback from two project contexts or `explicit_global_approval: true`.
2. **Repeated-context rule.** A confirmed collaboration/expression claim with `derivation: repeated-context` needs supporting primary-user evidence from at least two project chains. It may become a bounded active confirmed rule, but it is not an approved evolution unless the explicit evolution-approval gate also passes.

Set the promoted claim's `rule_scope` and `applies_to` no wider than the evidence. Use `derivation: feedback-promotion` for a collaboration/expression claim activated by an approved evolution. Preserve conflicts and superseded versions instead of rewriting history.

## 5. Version and validate behavior

Use a new positive `rule_version` when the rule's behavior or scope changes. Preserve the earlier rule in `before_rule`, state the expected observable difference, and link any superseded claim.

To move from `approved` to `validated`:

- cite the before-state in `baseline_event_ids`;
- set `validation_result` to `passed`;
- cite later primary-user or observable grade-B behavior evidence in `validation_event_ids`;
- describe the observed change in `observed_behavior_change`;
- keep the scope unchanged unless a separate promotion gate supports expansion.

Baseline and after-evidence must be distinct and belong to reviewed knowledge projects. Failed or mixed behavior evidence cannot be labeled `validated`. Record the result, keep or reject the candidate as the evidence supports, and do not describe the system as evolved.

## 6. Incremental carry-forward

`review-init --from-review` may carry a rule record only when its feedback, promoted claim, approval evidence, validation evidence, supporting full semantic project hashes, and reading receipts remain valid. Changed projects are invalidated.

After any carry-forward, complete the machine-checked `cross_project_recheck` before publication. Recheck relationships, repeated-context rules, conflicts, supersession, global scope, and behavior evidence against both carried and changed projects. Hash identity saves rereading an unchanged chain; it does not prove that cross-project meaning stayed unchanged.

## 7. Publication and reading

`distill` writes `audit/feedback-signals.json`, `audit/rule-evolutions.json`, graph nodes/edges, and the evolution summary in `knowledge-index.json`.

- Candidate and rejected changes remain non-active audit records and are not rendered as current behavior rules.
- Approved and validated changes may appear as active scoped rules.
- The Reader applies only confirmed claims whose scope covers the current task and active evolutions linked to those claims.
- Only validated changes count toward an “evolved” status or claim.

Run `validate-review` before `distill`, then `verify-retrieval`. Passing evolution validation does not replace the positive and negative retrieval gates.
