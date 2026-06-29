# M3b Versioned Session and Append-Only Trace Design

## Goal

Persist bounded Agent lifecycle facts in a versioned local Session store so runs remain
queryable and traceable after process exit, while making persistence failure a first-class stop
condition when durable journaling is enabled.

## Scope Boundary

M3 remains split by consistency model:

- M3a bounds each provider request and keeps the complete transcript only in memory;
- M3b persists Session metadata, Run projections, and append-only typed lifecycle events;
- M3c persists checkpoints/messages, validates resume compatibility, and prevents side-effect
  replay.

M3b does not persist prompts, messages, ToolCall arguments, ToolResult content, file patches,
command output, or model response text. It cannot resume a run. A process crash can leave an
active run and a `ToolStarted` without `ToolCompleted`; M3c will treat that state as indeterminate.

## Approaches Considered

### 1. SQLite-only event log and projections (selected)

Store canonical event JSON, a hash chain, Session metadata, and Run projections in one SQLite
database. One transaction appends an event and updates its projections. Current event payloads are
bounded metadata, so an external object store is not yet needed.

This gives the clearest crash and concurrency semantics with no cross-file commit problem.

### 2. SQLite index plus JSONL trace

This matches the original high-level product sketch and makes traces easy to inspect with text
tools. However, appending JSONL and committing its SQLite index cannot be atomic. Recovery needs a
reconciliation protocol for orphan file lines and missing indexes before Checkpoint/Resume exists.

### 3. JSONL-only sessions and traces

This minimizes dependencies but makes concurrent sequence allocation, projection queries,
versioned migrations, idempotency, and bounded list operations harder. It is not selected.

Large payloads can move to content-addressed files in a later milestone without changing the
Session/Run/Trace contracts.

## Architecture

```text
AgentRuntime
    |
    | required when configured
    v
EventJournal.append(AgentEvent)
    |
    v
SqliteSessionTraceStore
    |-- BEGIN IMMEDIATE
    |-- validate session/run transition
    |-- canonicalize + scrub configured secret values
    |-- enforce event/session limits
    |-- allocate per-session sequence
    |-- append trace row + hash-chain link
    |-- update Session and Run projections
    `-- COMMIT

AgentRuntime
    |
    | independent best effort
    v
EventSink.publish(AgentEvent)
```

The Store owns database schema, transactions, limits, queries, and trace verification. A journal
returned by `store.journal(session_id)` binds events to exactly one Session. `AgentRuntime` remains
unaware of SQLite and Session schema.

## Domain Models

### Identifiers

`session_id`, `run_id`, and `event_id` use 1-96 ASCII letters, digits, dots, underscores, or
hyphens and must start with an alphanumeric character. Runtime-generated event IDs use UUID text.

### Session

`SessionRecord` is immutable and contains:

- `session_id`, `schema_version`;
- `created_at`, `updated_at`;
- status: `ready`, `active`, `completed`, or `stopped`;
- optional `last_run_id`;
- `event_count`, `next_sequence`, and `trace_head_sha256`.

Creating a Session is explicit and idempotency is not inferred from a duplicate ID. Duplicate
creation returns `session_exists`.

### Run

`RunRecord` contains:

- `run_id`, `session_id`, start/stop timestamps;
- status: `active`, `completed`, or `stopped`;
- optional stop reason;
- completed turns, ToolCall count, and cumulative input/output usage.

`RunStarted` creates the projection. `RunStopped` finalizes it. A database reopened after a crash
keeps an unclosed run as `active`; M3b does not guess whether an in-flight external action
completed.

### Trace Event Envelope

Each row contains:

- schema version and per-session sequence starting at 1;
- `session_id`, `run_id`, `event_id`, event type, and timestamp;
- canonical compact event JSON;
- previous row SHA-256 and current row SHA-256.

The current hash is SHA-256 over canonical JSON containing schema version, Session ID, sequence,
previous hash, and the canonical event object. The all-zero 64-character hash is the first
previous hash.

This chain is tamper-evident against accidental edits. It is not authenticated, signed,
tamper-proof, or a substitute for filesystem permissions.

## Event Lifecycle

M3b adds:

- `ModelStarted(turn, request_id)` before provider I/O;
- `ToolStarted(turn, tool_call_id, tool_name, side_effect)` before Tool execution;
- bounded `event_id` to every event;
- cumulative ToolCall/usage fields to `RunStopped`.

Expected successful ToolCall run order:

```text
RunStarted
ModelStarted
ModelCompleted(tool_call)
ToolStarted
ToolCompleted
ModelStarted
ModelCompleted(stop)
RunStopped
```

Every journal append precedes the corresponding best-effort observer publish. `ToolStarted` must
be durable before invoking the tool. A missing `ToolCompleted` therefore means "started,
completion unknown", never "safe to replay".

## Runtime Failure Semantics

`AgentRuntime` accepts two independent outputs:

- `events`: existing best-effort observer; failures never change Agent behavior;
- `journal`: optional required persistence; once supplied, append failure stops the run.

Journal exceptions are normalized to a typed static `PersistenceError`. Runtime returns
`StopReason.PERSISTENCE_ERROR`, preserves the in-memory messages accumulated so far, performs no
later provider/tool work, and sends a static public message. It does not recursively retry the
failed journal.

Cancellation remains dominant. Runtime makes one best-effort attempt to journal/publish
`RunStopped(CANCELLED)` and re-raises `CancelledError` even if persistence is unavailable.

If persistence fails after a side-effecting tool returns but before `ToolCompleted` commits, the
trace contains `ToolStarted` only. Runtime stops before any next tool. M3b makes no exactly-once
claim; M3c must not automatically replay an indeterminate action.

## SQLite Schema and Transactions

Schema version 1 uses:

- `sessions`;
- `runs`;
- `trace_events`;
- SQLite `PRAGMA user_version = 1`.

Connections enable foreign keys, WAL journal mode, full synchronous writes, and a bounded busy
timeout. Every append opens a short transaction with `BEGIN IMMEDIATE`, validates the previous
head, inserts one event, updates projections and Session counters, then commits.

Constraints enforce:

- one Session owner for each Run;
- unique `event_id`;
- unique `(session_id, sequence)`;
- monotonic Session counters;
- bounded statuses and non-negative metrics;
- terminal Run transitions only once.

Appending the same event object again is an idempotent no-op only when its stored canonical payload
and Session match. Reusing an event ID with different content or Session fails closed.

The Store uses parameterized SQL exclusively. It normalizes SQLite, validation, corruption, lock,
limit, and transition failures into stable error codes without returning SQL text, absolute paths,
event payloads, or raw exceptions.

## Limits and Secret Handling

`SessionTraceLimits` defaults:

- 64 KiB maximum canonical event payload;
- 100,000 events per Session;
- 1,000 rows maximum per list/read call;
- 250 ms SQLite busy timeout, configurable up to 5 seconds.

All limits are immutable bounded Pydantic fields.

Typed Agent events intentionally contain lifecycle metadata rather than prompts, arguments,
results, patches, or command output. The journal additionally replaces explicitly configured
secret values in bounded free-form error text before hashing and storage. Identifiers and enum
fields are not rewritten. The journal cannot discover unknown secrets. Callers must pass
configured Provider keys when constructing the Store.

`RunStopped.error` is bounded. Runtime-generated errors remain static public text.

## Query and Verification API

The Store exposes bounded operations:

- create/get/list Sessions;
- get/list Runs for one Session;
- read Trace rows after a sequence with an explicit limit;
- verify a complete Session hash chain and projection head;
- create a Session-bound `EventJournal`;
- close/setup through explicit context management.

Ordering is deterministic. No API returns unbounded rows. Trace verification returns counts and
head hash, not event payloads in an error.

## Test Strategy

Unit tests cover:

- model bounds, immutability, identifier validation, and event serialization;
- schema creation/reopen and unsupported `user_version`;
- Session create/get/list limits and duplicate IDs;
- complete event lifecycle projections;
- monotonic sequence and hash-chain verification;
- idempotent same-event append and conflicting event-ID reuse;
- unknown Session, event-before-RunStarted, duplicate Run, event-after-stop, and cross-Session Run;
- event/session limits, busy lock timeout, rollback, malformed rows, and static errors;
- configured-secret scrubbing and no raw payload/path/SQLite exception leakage;
- observer best-effort behavior versus required journal failure;
- provider/tool zero-additional-call behavior after persistence failure;
- cancellation dominance and `ToolStarted` before real side effects.

Integration tests run a deterministic Agent through ToolCall and final response, close/reopen the
database, verify ordered events and Session/Run projections, then detect deliberate trace
corruption.

Property-style boundary tests exercise event sizes and list limits at `N-1/N/N+1`.

## Learning Mapping

- SQLite transaction plus projections maps to a compact local event-sourcing design.
- The append-only Trace is analogous to a Kafka log; Session/Run tables are materialized views.
- `event_id` is an idempotency key, while `(session_id, sequence)` is ordering state.
- WAL is not a Flink checkpoint and the hash chain is not exactly-once processing.
- `ToolStarted` without `ToolCompleted` corresponds to an indeterminate external side effect.
- Database backpressure is a bounded busy timeout followed by fail-closed stop, not an unbounded
  retry loop.

## Non-claims

- No prompt, transcript, ToolResult, patch, or command-output persistence.
- No Checkpoint, Resume, migration from future schemas, or side-effect replay.
- No distributed writers, remote database, replication, encryption at rest, or signed audit log.
- No authenticated tamper protection.
- No Linux or remote CI claim until the repository is published and CI runs.
