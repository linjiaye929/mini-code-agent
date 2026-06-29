# Durable Checkpoint and Safe Resume

## Purpose

M3c persists stable Agent input state and restores an interrupted Session without silently
replaying work that may have changed the outside world. Trace answers what happened; Checkpoint
stores the mutable state needed to continue.

## Stable Checkpoints

Runtime saves a Checkpoint:

- after `RunStarted` and before the first Provider request;
- after every complete ToolCall/ToolResult batch;
- never while ToolCalls are pending;
- never as a replacement for terminal `RunStopped`.

Each immutable snapshot contains the system prompt, full typed transcript, counters, token usage,
seen ToolCall IDs, Tool contract hash, and Workspace hash. Stable transcript validation requires
every assistant ToolCall batch to have one following user ToolResult batch with the same ordered
IDs.

## Atomic Storage

SQLite database schema v2 adds `checkpoints`. Existing Trace rows remain envelope version 1, so
the v1-to-v2 migration does not rewrite historical hashes.

`SessionCheckpointJournal.save` uses one `BEGIN IMMEDIATE` transaction to:

1. validate the active source Run and limits;
2. append `CheckpointSaved`;
3. advance Session sequence and hash head;
4. bind canonical Checkpoint JSON to the new sequence/head;
5. insert the Checkpoint row.

The event, projection, head, and payload commit or roll back together. Exact ID/payload retries
are idempotent; conflicting reuse fails.

## Compatibility

The Tool contract hash covers sorted names, descriptions, input schemas, and side-effect classes.
The Workspace hash covers a bounded deterministic manifest of relative paths and regular-file
bytes. Scan configuration is included in the hash. Symlinks, special files, replacement races,
and configured count/byte overflow fail closed.

Default exclusions include `.git`, `.venv`, `.worktrees`, `__pycache__`, and `node_modules`.
Excluded content is intentionally outside the compatibility claim.

## Resume Analysis

Analysis first verifies the complete Trace and Checkpoint payload hash. It then requires:

- the latest available Checkpoint;
- an active source Run;
- exact Tool and Workspace fingerprints;
- explicit permission for possible Provider or read-only Tool retry.

All events after the Checkpoint sequence are read in bounded pages. Any uncheckpointed
`ToolStarted` classified as write, execute, or network blocks Resume, even when a later
`ToolCompleted` exists. Completion proves that Runtime observed a result, not that the result was
included in the durable snapshot.

## Claim

`claim_resume` does not trust a caller-supplied plan. It reruns analysis with the supplied current
compatibility and policy, then compares the analyzed Trace head inside `BEGIN IMMEDIATE`.

If unchanged, the same transaction:

- appends `RunStopped(INTERRUPTED)` for the source Run;
- appends `RunStarted` for the resumed Run;
- marks the Checkpoint consumed by that Run.

Concurrent claims serialize through SQLite; exactly one consumes the snapshot. Runtime restores
messages and cumulative counters, writes an initial stable Checkpoint for the new Run, and starts
at the next logical turn without emitting a duplicate `RunStarted`.

## Failure Behavior

| Condition | Result |
| --- | --- |
| Checkpoint save fails | `PERSISTENCE_ERROR`; no next Provider request |
| Tool/Workspace differs | `RESUME_INCOMPATIBLE`; no mutation |
| Post-Checkpoint side effect | `INDETERMINATE_SIDE_EFFECT`; no mutation |
| Model/read replay not allowed | `REPLAY_REQUIRES_APPROVAL`; no mutation |
| Trace changed after analysis | `CHECKPOINT_STALE`; no mutation |
| Claim write fails | source/new Run, Trace, and consumption all roll back |
| Corrupt payload/Trace | static integrity error without stored content |

## Confidentiality and Non-Claims

Checkpoint JSON contains prompts, model text, Tool arguments/results, patches, and command output.
M3c stores it as bounded plaintext. Keep the database outside the model-controlled Workspace,
apply operating-system access controls, and do not treat configured event Secret scrubbing as
Checkpoint encryption.

M3c does not provide:

- encrypted state or key management;
- signed/authenticated audit records;
- distributed or multi-host coordination;
- automatic reconciliation of blocked side effects;
- exactly-once Provider billing or external Tool execution;
- an OS sandbox.

