# Versioned Session and Append-Only Trace

[English](session-trace.md) | [简体中文](session-trace.zh-CN.md)

## Purpose

M3b persists bounded Agent lifecycle metadata after process exit. It provides:

- versioned Session records;
- Run lifecycle projections;
- append-only typed Trace events;
- per-Session sequence and SHA-256 chain verification;
- required persistence semantics when a journal is injected into `AgentRuntime`.

M3b does not persist prompts, messages, ToolCall arguments, ToolResults, patches, or command
output. It cannot resume a run. Checkpoint/Resume and replay prevention belong to M3c.

## Composition

```python
from pathlib import Path

from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.persistence import SqliteSessionTraceStore

with SqliteSessionTraceStore(Path("agent-state.db")) as store:
    session = store.create_session()
    runtime = AgentRuntime(
        provider,
        tools,
        journal=store.journal(session.session_id),
    )
    result = await runtime.run(user_prompt="Inspect the project.")
    verification = store.verify_trace(session.session_id)
```

The existing `events` sink remains best-effort UI/telemetry output. The `journal` is independent:
once supplied, append failure stops the Agent with `PERSISTENCE_ERROR`.

## SQLite Schema

Schema version 1 uses three tables:

| Table | Responsibility |
|---|---|
| `sessions` | status, last Run, event count, next sequence, Trace head |
| `runs` | start/stop, stop reason, turns, ToolCalls, cumulative token usage |
| `trace_events` | canonical event JSON, sequence, previous/current SHA-256 |

`PRAGMA user_version` is the database format version. Connections enable foreign keys,
`journal_mode=WAL`, `synchronous=FULL`, and a bounded busy timeout.

Each append executes under `BEGIN IMMEDIATE`:

1. load and validate Session counters/head;
2. check exact duplicate `event_id`;
3. enforce payload and Session event limits;
4. validate Run transition and event timestamp order;
5. update Run/Session projection;
6. insert one Trace row;
7. advance Session counters/head;
8. commit.

Any failure rolls back both projection and Trace insert. The Store uses parameterized SQL and
normalizes database/path/payload details out of public errors.

## Event Lifecycle

A successful one-tool run records:

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

`ToolStarted` is required before invoking the executor. If execution succeeds but
`ToolCompleted` cannot be persisted, the durable state remains started-only. Runtime stops before
later work. M3c must treat that call as indeterminate and must not replay it automatically.

`RunStopped` stores cumulative turns, ToolCall count, input tokens, and output tokens. A crash
before RunStopped leaves the Run and Session active; M3b does not guess a terminal state.

## Idempotency and Integrity

Every event has a generated `event_id`. Re-appending the same ID with identical canonical payload
and Session is a no-op. Reusing it with different content or Session returns `event_conflict`.

The per-Session sequence starts at 1. For each event:

```text
current_hash = SHA256(canonical_json(
    schema_version,
    session_id,
    sequence,
    previous_hash,
    event
))
```

`verify_trace` reads bounded pages, parses every payload through the typed `AgentEvent` union,
checks row metadata, contiguous sequence, previous hash, recomputed current hash, event count,
next sequence, and Session head. Projection and event pages share one SQLite read transaction so
a concurrent committed append cannot combine two individually valid snapshots into a false
corruption result.

The chain detects accidental or unsophisticated modification. It is not authenticated, signed,
tamper-proof, or protected from an attacker who can rewrite the database and all hashes.

## Limits and Secret Boundary

Defaults:

- 64 KiB canonical JSON per event;
- 100,000 events per Session;
- 1,000 rows per query/verification page;
- 250 ms SQLite busy timeout.

All are bounded immutable Pydantic settings. Invalid queries fail rather than clamp.

Agent events contain lifecycle metadata only. Prompts, arguments, results, diffs, and command
output never enter M3b Trace. Configured Secret values are scrubbed from bounded
`RunStopped.error` before hashing and SQL binding. Unknown secrets cannot be discovered
automatically.

## Failure Semantics

- Required journal write fails: stop with static `PERSISTENCE_ERROR`; perform no later work.
- Best-effort observer fails: continue unchanged.
- Cancellation occurs: attempt RunStopped journaling once, then re-raise cancellation even if
  persistence fails.
- Database is busy beyond the configured timeout: fail closed; do not retry indefinitely.
- Existing schema is newer than supported: reject without migration or downgrade.
- Trace row/projection/hash is malformed: return static `trace_corrupt`.

## Verification Evidence

Tests cover schema reopen, WAL/foreign keys, exact limits, deterministic ordering, idempotency,
conflicting IDs, invalid transitions, cross-Session ownership, lock timeout, transactional
rollback, typed reads, four corruption modes, configured Secret scanning, required-vs-best-effort
Runtime behavior, cancellation, and real governed file writes.

## Non-claims

- No Checkpoint, message snapshot, Resume, or side-effect replay.
- No JSONL/object storage for large payloads.
- No encryption at rest, signed audit log, remote database, replication, or distributed writers.
- No exactly-once semantics for external side effects.
