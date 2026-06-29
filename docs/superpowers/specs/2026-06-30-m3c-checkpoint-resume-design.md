# M3c Durable Checkpoint and Safe Resume Design

**Date:** 2026-06-30  
**Target:** `v0.9.0-alpha.0`

## Scope

M3c adds durable message checkpoints and a fail-closed Resume path on top of the M3b Session,
Run, and Trace log. It restores an interrupted conversation without automatically replaying
uncheckpointed side effects.

M3c does not claim distributed exactly-once execution, automatic reconciliation of external
systems, encrypted storage, multi-host leases, or recovery from an untrusted database owner.

## Safety Invariants

1. A Checkpoint is saved only at a stable model-input boundary: the transcript starts with the
   user goal and every assistant ToolCall has a matching ToolResult message.
2. The Checkpoint row and its `CheckpointSaved` Trace event commit in one SQLite transaction.
3. Resume verifies the complete Trace chain before trusting a Checkpoint.
4. Resume scans every event after the Checkpoint sequence. Any `ToolStarted` with `write`,
   `execute`, or `network` side effect blocks automatic Resume, whether or not it completed.
5. A started model request after the Checkpoint is retried only when `ResumePolicy` explicitly
   permits possible duplicate provider cost.
6. Tool contract and Workspace fingerprints must exactly match the saved values.
7. Claiming a Checkpoint, marking the source Run interrupted, and starting the resumed Run are one
   transaction. A consumed Checkpoint cannot be claimed twice.
8. Restored turn/tool/token counters remain cumulative, so Resume cannot evade configured limits.
9. Invalid, oversized, incompatible, stale, consumed, or corrupt Checkpoints fail with static
   errors that do not expose prompts, Tool arguments/results, paths, SQL, or raw exceptions.
10. A configured Checkpoint writer is required state: save failure stops Runtime before the next
    Provider call.

## Stable Boundary

Runtime saves:

- one initial Checkpoint after `RunStarted`, before the first Provider request;
- one Checkpoint after all ToolResults for a model turn have been appended;
- no Checkpoint between `ModelCompleted` and completion of all ToolCalls;
- no terminal Checkpoint after `RunStopped`.

This means a Checkpoint always represents a request that can be reconstructed. A crash during a
read-only Tool can return to the previous stable boundary. A crash after an uncheckpointed
side-effecting Tool cannot be retried automatically.

## Snapshot Model

`CheckpointSnapshot` contains:

- checkpoint format version;
- Session ID, source Run ID, and Checkpoint ID;
- Trace sequence and Trace head the snapshot is bound to;
- UTC creation time;
- system prompt and complete typed `Message` transcript;
- completed turns, ToolCall count, cumulative token usage, and seen ToolCall IDs;
- canonical Tool contract SHA-256;
- Workspace snapshot SHA-256.

The Tool fingerprint hashes sorted canonical Tool definitions, including name, description,
input schema, and side-effect class. The Workspace fingerprint hashes a bounded, deterministic
manifest of regular files under the configured root. Symlinks and scan-limit overflow fail
closed. The scanner excludes VCS internals and configured generated directories, but exclusions
are part of its immutable configuration and therefore part of the fingerprint input.

The SQLite row stores canonical JSON and its SHA-256. Prompts, Tool arguments/results, model text,
and command output therefore become durable plaintext. M3c restricts size and access but does not
claim encryption. A future codec can encrypt the canonical payload without changing Runtime
state semantics.

## SQLite Schema v2

Database schema version moves from 1 to 2. Trace envelope version remains 1.

The v1-to-v2 migration only adds `checkpoints` and indexes, then updates
`PRAGMA user_version`. Existing Session, Run, and Trace rows remain byte-for-byte unchanged.
Migration runs under `BEGIN IMMEDIATE` and rolls back completely on failure.

`checkpoints` stores:

- primary `checkpoint_id`;
- owning `session_id` and `source_run_id`;
- source `trace_sequence` and `trace_head_sha256`;
- format version, timestamp, payload JSON, and payload SHA-256;
- status: `available` or `consumed`;
- optional `resumed_run_id` and consumed timestamp.

There is at most one available latest Checkpoint per stable boundary, but old Checkpoints remain
queryable for diagnosis. Resume can claim only an available Checkpoint belonging to the current
active source Run.

## Transactional Save

`SessionCheckpointJournal.save(snapshot)`:

1. opens `BEGIN IMMEDIATE`;
2. loads and validates Session, active Run, and current Trace head;
3. validates snapshot identity, counters, message grammar, limits, and fingerprints;
4. creates `CheckpointSaved` with bounded metadata only;
5. appends the event and advances Session Trace state;
6. binds the snapshot to that new event sequence/head;
7. inserts the Checkpoint row;
8. commits.

Exact retry with the same Checkpoint ID and identical canonical payload is idempotent. Reusing an
ID with different content fails as a conflict.

## Resume Analysis

`analyze_resume(checkpoint_id, compatibility, policy)` is read-only and returns either a bounded
`ResumePlan` or a stable error:

1. verify the entire Session Trace;
2. load and hash-verify the Checkpoint;
3. require `available` status and current active source Run;
4. validate Tool and Workspace fingerprints;
5. read all Trace events after the Checkpoint in bounded pages;
6. reject any side-effecting `ToolStarted`;
7. reject a started-only read-only Tool unless policy allows replay;
8. reject a started-only model request unless policy allows provider retry;
9. reject unknown lifecycle shapes instead of guessing.

Completed read-only work after the snapshot is not reused; Resume returns to the stable snapshot
and may repeat it only under the selected policy.

## Atomic Claim and New Run

`claim_resume(plan, resumed_run_id)` reruns trusted eligibility analysis instead of trusting the
serializable plan. Under `BEGIN IMMEDIATE` it requires the analyzed event count and Trace head to
remain unchanged, closing the analysis-to-claim race. It then:

- appends `RunStopped(reason=interrupted)` for the source Run using cumulative snapshot metrics;
- appends `RunStarted` for the new Run;
- marks the Checkpoint consumed by the new Run;
- leaves Session active and commits all changes together.

Runtime receives `ResumeState` only after commit. It starts its loop at `turns + 1`, preserves
ToolCall IDs and cumulative limits, and does not emit a second `RunStarted`.

## Failure Semantics

- Checkpoint save failure: `PERSISTENCE_ERROR`, no next Provider call.
- Compatibility mismatch: Resume rejected, no Trace or projection mutation.
- Risky post-checkpoint action: `INDETERMINATE_SIDE_EFFECT`, no mutation.
- Policy-required model/read retry: `REPLAY_REQUIRES_APPROVAL`, no mutation.
- Concurrent claim: exactly one transaction succeeds; later claims see consumed/stale state.
- Corrupt payload/hash/Trace: `TRACE_CORRUPT`, no content in public error.
- Cancellation: last durable stable Checkpoint remains available; cancellation remains dominant.

## Limits

Defaults:

- 4 MiB canonical Checkpoint payload;
- 10,000 messages;
- 1,000 Checkpoints per Session;
- 20,000 Workspace files and 64 MiB total scanned bytes;
- existing 1,000-row query page and 250 ms SQLite busy timeout.

All limits are immutable bounded Pydantic fields. No query or filesystem scan is unbounded.

## Test Strategy

Unit tests cover:

- snapshot grammar, canonical hashing, size/count boundaries, and no mutable collections;
- schema v1-to-v2 migration, rollback, reopen, and future-version rejection;
- atomic Checkpoint event/row save, exact idempotency, conflict, lock timeout, and rollback;
- payload/hash corruption and static error hygiene;
- deterministic Tool and Workspace fingerprints, symlink rejection, exclusions, and scan limits;
- Resume analysis for no delta, model started/completed, read-only Tool states, and every
  side-effect class;
- compatibility mismatch, consumed/stale Checkpoint, duplicate claim, and concurrent claim;
- cumulative limits and resumed turn numbering;
- zero Provider/Tool calls when analysis, claim, or save fails.

Integration tests inject process-boundary failures:

1. before first Provider response, then explicitly retry the model request;
2. after a completed read-only Tool but before the next Checkpoint, then safely replay;
3. after a real governed write, then prove Resume is blocked and no second write occurs;
4. after a stable Checkpoint, close/reopen SQLite, claim into a new Run, and complete;
5. mutate Workspace or Tool schema and prove no Resume mutation;
6. race two claims and prove one winner.

## Learning Mapping

- Checkpoint payload is Flink operator state; Trace sequence/head is analogous to checkpoint
  metadata binding, not a distributed barrier.
- Resume claim is optimistic concurrency plus a transactional state transition.
- The post-checkpoint Trace scan is write-ahead evidence used to prevent unsafe replay.
- Tool/Workspace fingerprints are compatibility guards similar to serializer and job-graph
  compatibility checks.
- At-least-once Provider retry is explicit; external Tool exactly-once remains impossible without
  idempotency keys or target-system transactions.
