# Small, evidence-bound context views

After establishing a necessary history gap, choose a view intentionally:

- `--view facts`: rank reviewed assertion units inside selected documents for the
  missing fact. Document-title/alias tokens are removed from the within-document
  query so a repeated project name does not drown out the actual question.
- `--view current`: return only explicit reviewed current-state references. Use
  this for project continuation, not to invent a current state from the last
  paragraph, file modification time or most optimistic completion claim.
- `--view documents`: read the full selected document for surrounding history,
  ambiguity or a requested complete retrospective.

Add `--emit-content` to emit bodies/units. Compact views keep whole statements,
evidence IDs, status, applicability and recorded corrections. Base claims preserve
conflicts, supersession and any governed rule-evolution evidence. Only active,
confirmed, in-scope rules can guide action; the presence of a unit is not approval.

`--max-facts` (default 6) and `--max-chars` (default 6000) bound units per document.
The latter counts serialized unit characters, not model tokens or whole-response
size. Evidence rules are returned whole outside that unit budget. Oversized units
are omitted, not cut mid-statement. Inspect `omitted_count`, `missing_state_fields`
and `unexpanded_claim_ids`; expand the needed history before resolving a conflict.
A compact result is never proof that every historical assertion was considered.

Older publications without units report `unavailable`; they are not silently
summarized. Use the full approved document if needed. Adding compact/current views
to such a library requires normal reviewed distillation and retrieval verification,
not a whole-machine rescan or hand-edit of the published index.

The verified suite can bind `selection_options` containing base context, view and
unit budgets. An omitted Reader option inherits that verified selection. Changing
an option marks the query outside it. Engine version is also bound: old verification
does not attest to a newly changed retrieval algorithm. Usage receipts include
returned-content character counts, not a claim that all selected guidance was used.

In compact-view evaluations, `min_context_units` applies separately to every
declared expected project and document type. A related graph neighbour cannot
make a missing target pass. Without explicit expectations, count only directly
selected context, not graph expansion. This checks availability, not semantic
correctness or whether a later Agent actually applied a correction.

These views reduce emitted context; publication integrity still hashes the indexed
files. Do not promise equivalent reductions in filesystem I/O or semantic search.
