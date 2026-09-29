# Resumable manual-page maintenance

Use only for human-maintained Markdown pages, after the host has established the
user's explicit or standing permission for those exact pages. A CLI flag or source
reference records authorization; it cannot manufacture it. Do not configure this
merely because the Skill is installed. Explain sync/shared-directory visibility
before accepting a destination. Generated libraries retain their separate pipeline.

Use an existing Python 3.9+ launcher. Run commands relative to the Rebuilder folder.

## Approve a bounded destination once

```text
<python> scripts/session_kb.py maintenance-configure --root /approved/private/notes --target projects/alpha.md --authorization-ref USER_TURN
```

Repeat `--target` for other explicitly approved existing Markdown pages. This saves
local configuration only; it starts no scheduler. Source repositories, generated
libraries, hidden/escaping paths and symlink targets are refused. The runtime checks
the configured scope on each stage/apply. Do not use this append-only helper for
identity changes, unresolved conflicts, deletion or arbitrary file replacement.

## Stage a reviewed durable delta

First inspect the target and compare the sourced outcome against it. Remove
duplicates and maintenance telemetry. Keep date, target/version, observed result,
remaining work and scope; do not infer acceptance from a build. Prepare the delta
in an approved private file, then run:

```text
<python> scripts/session_kb.py maintenance-stage --root /approved/private/notes --target projects/alpha.md --delta-file /approved/private/delta.md --source-ref SOURCE_TURN
<python> scripts/session_kb.py maintenance-status --root /approved/private/notes
```

The queue stores the sanitized delta and target/authorization hashes. Exact
target+text+source repeats return the existing item; semantic deduplication still
requires the Agent. Status returns control metadata, not queued prose. The original
input file is not deleted or sanitized in place: keep it private and avoid secrets.

## Apply and verify, or recover the same item

```text
<python> scripts/session_kb.py maintenance-apply --root /approved/private/notes --id DELTA_ID
<python> scripts/session_kb.py maintenance-apply --root /approved/private/notes --id DELTA_ID --commit
```

The first command is dry-run. The second appends one dated, sourced block, checks
the resulting bytes and only then marks it applied. After a crash between page
write and receipt, rerun the same item: matching bytes complete the receipt without
a duplicate append. Completed receipts omit the delta body. A later edit to that
block is not silently restored. Hashes are integrity checks, not an authentication
boundary against someone controlling the private directory.

A changed target, changed authorization or conflicting writer blocks application.
Inspect and reconcile rather than overwriting or deleting locks. Coordinate other
editors during commit: the lock protects cooperating writers, not arbitrary OS
processes. There is no cross-file transaction or guaranteed filesystem power-loss
recovery. A process-kill may require verifying stale lock ownership before retry.

After rechecking the current page and current permission, explicitly replace a
pending/cancelled attempt with newly supplied reviewed text and source:

```text
<python> scripts/session_kb.py maintenance-restage --root /approved/private/notes --id OLD_DELTA_ID --target projects/alpha.md --delta-file /approved/private/delta.md --source-ref SOURCE_TURN
```

This creates a new attempt bound to the current authorization and page baseline,
and retains a body-free superseded receipt for the old one. Applied/applying work
cannot be restaged. Repeating the exact restage returns the same replacement.
Configuration and ordinary staging never revive cancelled work automatically.

## Revoke or cancel

`maintenance-revoke --root PATH` disables future stage/apply operations and retains
pending records for inspection. `maintenance-cancel --root PATH --id DELTA_ID`
removes a pending item's stored body and marks it cancelled; it does not erase an
already-applied page block. An applying item needs inspection/recovery, not blind
cancellation. Do not represent either operation as erasing backups or source files.

No daemon or task-end hook is installed. Recovery is possible when the host next
executes the approved workflow; pending is never reported as updated.
