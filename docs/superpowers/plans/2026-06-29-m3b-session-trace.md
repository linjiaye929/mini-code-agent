# M3b Versioned Session and Append-Only Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned SQLite Session store and append-only typed Trace whose required journal
can stop Agent execution safely when persistence fails.

**Architecture:** `SqliteSessionTraceStore` owns schema version 1, bounded Session/Run queries,
transactional event append, projections, and SHA-256 verification. A Session-bound
`EventJournal` is required when injected into `AgentRuntime`; the existing `EventSink` remains
best-effort observability. Checkpoint/message persistence and Resume remain outside M3b.

**Tech Stack:** Python 3.12/3.13, stdlib `sqlite3`, Pydantic v2, canonical JSON, SHA-256,
`Protocol`, Pytest, strict Pyright.

---

## Invariants

1. One SQLite transaction appends an event and updates Session/Run projections.
2. Trace sequence is strictly increasing per Session and starts at 1.
3. Every row binds schema version, Session, sequence, previous hash, and canonical event JSON.
4. The same `event_id` and canonical payload is idempotent; conflicting reuse fails closed.
5. Event payloads contain lifecycle metadata, not prompts, ToolCall arguments, or ToolResults.
6. `ToolStarted` is journaled before Tool execution.
7. A required journal failure stops before any later Provider or Tool operation.
8. Observer `EventSink` failure remains best effort and cannot change behavior.
9. Cancellation is re-raised even when cancellation journaling fails.
10. All list/read operations and SQLite waits have explicit limits.
11. Public persistence errors contain no SQL, absolute path, payload, Secret, or raw exception.
12. M3b makes no Checkpoint, Resume, encryption, signed audit, or exactly-once claim.

## File Map

- Modify `src/mini_code_agent/agent/events.py`: event IDs, started events, bounded stop metrics.
- Modify `src/mini_code_agent/agent/models.py`: persistence stop reason.
- Modify `src/mini_code_agent/agent/runtime.py`: required journal and ordered lifecycle writes.
- Create `src/mini_code_agent/persistence/errors.py`: stable persistence failures.
- Create `src/mini_code_agent/persistence/models.py`: limits and immutable records.
- Create `src/mini_code_agent/persistence/schema.py`: schema version 1 SQL and setup.
- Create `src/mini_code_agent/persistence/store.py`: Session/Run/Trace operations.
- Create `src/mini_code_agent/persistence/journal.py`: Session-bound journal implementation.
- Create `src/mini_code_agent/persistence/__init__.py`: supported public exports.
- Add `tests/unit/persistence/` and persistence/runtime integration tests.
- Add architecture/ADR/threat/learning/resume/release evidence.

## Task 1: Durable Event Contracts

**Files:**

- Modify: `src/mini_code_agent/agent/events.py`
- Modify: `tests/unit/agent/test_events.py`

- [ ] Add failing tests proving every event has a valid immutable UUID `event_id`,
  `ModelStarted`/`ToolStarted` belong to `AgentEvent`, string/metric bounds reject oversized data,
  and `RunStopped` serializes cumulative ToolCall/usage values.

```python
model_started = ModelStarted(
    run_id="run-1",
    turn=1,
    request_id="run-1:1",
)
tool_started = ToolStarted(
    run_id="run-1",
    turn=1,
    tool_call_id="call-1",
    tool_name="write_file",
    side_effect=SideEffect.WRITE,
)
stopped = RunStopped(
    run_id="run-1",
    turns=1,
    reason=StopReason.COMPLETED,
    tool_calls=1,
    usage=TokenUsage(input_tokens=10, output_tokens=5),
)
assert len({model_started.event_id, tool_started.event_id, stopped.event_id}) == 3
```

- [ ] Run the new tests and confirm import/model failures.

```powershell
python -m pytest tests/unit/agent/test_events.py -q
```

- [ ] Add bounded fields and event types:

```python
def _event_id() -> str:
    return str(uuid4())


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(
        default_factory=_event_id,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$",
    )
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ModelStarted(EventBase):
    type: Literal["model_started"] = "model_started"
    turn: int = Field(ge=1, le=100)
    request_id: str = Field(min_length=1, max_length=193)


class ToolStarted(EventBase):
    type: Literal["tool_started"] = "tool_started"
    turn: int = Field(ge=1, le=100)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    side_effect: SideEffect
```

Define the infrastructure-neutral required journal contract beside `EventSink`:

```python
class EventJournal(Protocol):
    def append(self, event: AgentEvent) -> None: ...
```

Bound all existing event counters and names. Add to `RunStopped`:

```python
tool_calls: int = Field(default=0, ge=0, le=1000)
usage: TokenUsage = Field(default_factory=TokenUsage)
error: str | None = Field(default=None, max_length=500)
```

- [ ] Run event and Runtime tests; update constructors only where Runtime evidence should include
  real counts.

```powershell
python -m pytest tests/unit/agent -q
python -m ruff check src/mini_code_agent/agent tests/unit/agent
python -m pyright --pythonpath .\.venv\Scripts\python.exe
```

- [ ] Commit.

```powershell
git add src/mini_code_agent/agent/events.py tests/unit/agent
git commit -m "feat: define durable lifecycle events"
```

## Task 2: Persistence Models, Errors, and Schema

**Files:**

- Create: `src/mini_code_agent/persistence/errors.py`
- Create: `src/mini_code_agent/persistence/models.py`
- Create: `src/mini_code_agent/persistence/schema.py`
- Create: `src/mini_code_agent/persistence/__init__.py`
- Create: `tests/unit/persistence/test_models.py`
- Create: `tests/unit/persistence/test_schema.py`

- [ ] Add failing model tests for identifier patterns, immutable bounded limits, status enums,
  record metadata, and exact `N-1/N/N+1` event/list/busy-timeout boundaries.

```python
limits = SessionTraceLimits()
assert limits.max_event_bytes == 65_536
assert limits.max_events_per_session == 100_000
assert limits.max_query_rows == 1_000
assert limits.busy_timeout_ms == 250
with pytest.raises(ValidationError):
    SessionTraceLimits(max_query_rows=0)
```

- [ ] Define stable errors:

```python
class PersistenceErrorCode(StrEnum):
    DATABASE_UNAVAILABLE = "database_unavailable"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    SESSION_EXISTS = "session_exists"
    SESSION_NOT_FOUND = "session_not_found"
    RUN_CONFLICT = "run_conflict"
    INVALID_TRANSITION = "invalid_transition"
    EVENT_CONFLICT = "event_conflict"
    LIMIT_EXCEEDED = "limit_exceeded"
    TRACE_CORRUPT = "trace_corrupt"
    STORAGE_FAILED = "storage_failed"


class PersistenceError(RuntimeError):
    def __init__(self, code: PersistenceErrorCode, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
```

- [ ] Define immutable `SessionTraceLimits`, `SessionRecord`, `RunRecord`, `TraceRecord`, and
  `TraceVerification` with bounded timestamps, counters, status enums, hashes, and payload length.

- [ ] Add failing schema tests for new database initialization, reopen without mutation,
  foreign-key/WAL/synchronous/busy-timeout configuration, database-path-as-directory, and
  unsupported nonzero `PRAGMA user_version`.

- [ ] Implement schema version 1 with parameter-free constant DDL for `sessions`, `runs`, and
  `trace_events`. Required keys:

```sql
PRIMARY KEY (session_id, sequence)
UNIQUE (event_id)
FOREIGN KEY (session_id) REFERENCES sessions(session_id)
FOREIGN KEY (run_id) REFERENCES runs(run_id)
```

Add indexes on `(session_id, created_at)`, `(session_id, started_at)`, and
`(session_id, event_type, sequence)`. Set `user_version = 1` only after all DDL succeeds.

- [ ] Normalize setup failures to static `PersistenceError`; no error includes database path,
  SQL, or raw SQLite text. Run tests and static checks.

```powershell
python -m pytest tests/unit/persistence/test_models.py tests/unit/persistence/test_schema.py -q
python -m ruff format --check src tests
python -m ruff check src tests
python -m pyright --pythonpath .\.venv\Scripts\python.exe
```

- [ ] Commit.

```powershell
git add src/mini_code_agent/persistence tests/unit/persistence
git commit -m "feat: create versioned session schema"
```

## Task 3: Bounded Session and Run Queries

**Files:**

- Create: `src/mini_code_agent/persistence/store.py`
- Modify: `src/mini_code_agent/persistence/__init__.py`
- Create: `tests/unit/persistence/test_store.py`

- [ ] Add failing tests for explicit initialization, create/get/list Session, duplicate ID,
  unknown ID, stable descending list order with ID tie-breaker, explicit row limits, close/reopen,
  and context-manager setup/close.

```python
with SqliteSessionTraceStore(database) as store:
    created = store.create_session("session-1")
    assert store.get_session("session-1") == created
    assert store.list_sessions(limit=1) == (created,)

with SqliteSessionTraceStore(database) as reopened:
    assert reopened.get_session("session-1").schema_version == 1
```

- [ ] Implement `SqliteSessionTraceStore` with one short connection per public operation, the
  configured busy timeout, `sqlite3.Row`, parameterized SQL, and static error normalization.
  Public signatures:

```python
def create_session(self, session_id: str | None = None) -> SessionRecord: ...
def get_session(self, session_id: str) -> SessionRecord: ...
def list_sessions(self, *, limit: int = 100) -> tuple[SessionRecord, ...]: ...
def get_run(self, session_id: str, run_id: str) -> RunRecord: ...
def list_runs(self, session_id: str, *, limit: int = 100) -> tuple[RunRecord, ...]: ...
```

- [ ] Validate IDs before SQL. Clamp nothing: out-of-range limits fail with `LIMIT_EXCEEDED`.
  Database rows are parsed through Pydantic; malformed rows return `TRACE_CORRUPT`.

- [ ] Add tests proving paths, injected malformed IDs, raw SQL errors, and Session contents do not
  appear in public errors.

- [ ] Run store tests and checks; commit.

```powershell
python -m pytest tests/unit/persistence/test_store.py -q
python -m ruff check src/mini_code_agent/persistence tests/unit/persistence
python -m pyright --pythonpath .\.venv\Scripts\python.exe
git add src/mini_code_agent/persistence tests/unit/persistence
git commit -m "feat: persist bounded session metadata"
```

## Task 4: Transactional Event Append and Projections

**Files:**

- Create: `src/mini_code_agent/persistence/journal.py`
- Modify: `src/mini_code_agent/persistence/store.py`
- Modify: `src/mini_code_agent/persistence/__init__.py`
- Create: `tests/unit/persistence/test_journal.py`

- [ ] Add failing lifecycle tests for the complete successful order, strictly increasing sequence,
  Session status/head/counters, Run projection metrics, and close/reopen persistence.

Expected event order:

```python
(
    "run_started",
    "model_started",
    "model_completed",
    "tool_started",
    "tool_completed",
    "model_started",
    "model_completed",
    "run_stopped",
)
```

- [ ] Define the Session-bound implementation of `agent.events.EventJournal`:

```python
class SessionEventJournal:
    def __init__(self, store: SqliteSessionTraceStore, session_id: str) -> None: ...
    def append(self, event: AgentEvent) -> None:
        self._store.append_event(self._session_id, event)
```

Expose `store.journal(session_id)` only after verifying the Session exists.

- [ ] Implement canonical event serialization with sorted compact JSON. Scrub configured Secret
  values only from bounded free-form `RunStopped.error`; identifiers, enum values, hashes, and
  numeric usage remain unchanged.

- [ ] Implement current hash:

```python
envelope = {
    "schema_version": 1,
    "session_id": session_id,
    "sequence": sequence,
    "previous_sha256": previous_sha256,
    "event": event_payload,
}
current_sha256 = sha256(canonical_json(envelope)).hexdigest()
```

- [ ] In one `BEGIN IMMEDIATE` transaction:

1. load and validate Session counters/head;
2. enforce event count and payload byte limits;
3. handle exact duplicate `event_id` as an idempotent no-op;
4. reject conflicting event-ID reuse;
5. apply transition/projection SQL;
6. insert the Trace row;
7. increment Session sequence/count/head and commit.

- [ ] Add negative tests for event before `RunStarted`, duplicate active Run, cross-Session Run,
  event after `RunStopped`, duplicate terminal stop, unknown Session, event/session limit,
  payload boundaries, rollback after injected insert failure, and busy-lock timeout.

- [ ] Ensure `ToolStarted` carries `SideEffect`, and `RunStopped` writes turns, ToolCalls, usage,
  stop reason, timestamps, and terminal Session/Run status.

- [ ] Run persistence tests and checks; commit.

```powershell
python -m pytest tests/unit/persistence -q
python -m ruff format --check src tests
python -m ruff check src tests
python -m pyright --pythonpath .\.venv\Scripts\python.exe
git add src/mini_code_agent/persistence tests/unit/persistence
git commit -m "feat: append transactional session traces"
```

## Task 5: Trace Query and Integrity Verification

**Files:**

- Modify: `src/mini_code_agent/persistence/store.py`
- Modify: `src/mini_code_agent/persistence/models.py`
- Modify: `tests/unit/persistence/test_journal.py`

- [ ] Add failing tests for `read_trace(after_sequence, limit)`, deterministic ascending order,
  invalid boundaries, complete verification, empty Session verification, payload tampering,
  previous-hash tampering, deleted middle row, reordered sequence, and projection-head mismatch.

- [ ] Implement:

```python
def read_trace(
    self,
    session_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 100,
) -> tuple[TraceRecord, ...]: ...

def verify_trace(self, session_id: str) -> TraceVerification: ...
```

`verify_trace` reads in bounded pages of at most `max_query_rows`, recomputes every canonical
envelope, checks contiguous sequence and previous hash, then compares event count, next sequence,
and Session head. It returns only count/head metadata.

- [ ] Parse stored event JSON through `TypeAdapter(AgentEvent)` before returning a `TraceRecord`.
  Invalid JSON, unknown event type/schema, or inconsistent row metadata returns
  `TRACE_CORRUPT`.

- [ ] Prove a configured API-key value in `RunStopped.error` is stored as `***`, absent from raw
  database bytes after close, and does not appear in any public exception.

- [ ] Run tests/checks and commit.

```powershell
python -m pytest tests/unit/persistence -q
python -m ruff check src tests
python -m pyright --pythonpath .\.venv\Scripts\python.exe
git add src/mini_code_agent/persistence tests/unit/persistence
git commit -m "feat: verify append-only trace integrity"
```

## Task 6: Required Journal in Agent Runtime

**Files:**

- Modify: `src/mini_code_agent/agent/models.py`
- Modify: `src/mini_code_agent/agent/runtime.py`
- Modify: `tests/unit/agent/test_runtime.py`

- [ ] Add failing tests proving:

1. required journal receives `RunStarted` before the first provider call;
2. `ModelStarted` precedes Provider I/O;
3. `ToolStarted` precedes a real read/write executor call;
4. `ToolCompleted` follows execution;
5. journal failure at each event returns `PERSISTENCE_ERROR` with no later Provider/Tool calls;
6. observer failure remains best effort;
7. cancellation is re-raised when journal append also fails;
8. returned messages preserve all state accumulated before failure.

- [ ] Add `StopReason.PERSISTENCE_ERROR = "persistence_error"` and a journal constructor parameter:

```python
def __init__(
    self,
    provider: ModelProvider,
    tools: ToolExecutor,
    *,
    limits: AgentLimits | None = None,
    events: EventSink | None = None,
    journal: EventJournal | None = None,
    context: ContextPreparer | None = None,
) -> None: ...
```

- [ ] Refactor mutable local run values into a private slots dataclass so one outer
  `PersistenceError` boundary can construct an accurate result:

```python
@dataclass(slots=True)
class _RunState:
    run_id: str
    messages: list[Message]
    usage: TokenUsage
    seen_call_ids: set[str]
    turns: int = 0
    tool_calls: int = 0
```

- [ ] Implement `_emit`: required journal first, then best-effort observer. Normalize any unknown
  journal exception to `PersistenceError(STORAGE_FAILED, "Agent state could not be persisted.")`.

- [ ] Emit `ModelStarted` immediately before each provider call and `ToolStarted` immediately
  before each tool call. Populate cumulative values in every normal/cancelled `RunStopped`.

- [ ] On persistence failure, do not call the failed journal again. Publish one best-effort
  observer-only `RunStopped(PERSISTENCE_ERROR)` and return a static-error `AgentResult`.

- [ ] In cancellation handlers, make a best-effort journal/observer stop emission and always
  re-raise the original cancellation.

- [ ] Run all Agent, Provider, Context, and governed-tool tests. Commit.

```powershell
python -m pytest tests/unit/agent tests/unit/providers tests/unit/context tests/integration -q
python -m ruff format --check src tests
python -m ruff check src tests
python -m pyright --pythonpath .\.venv\Scripts\python.exe
git add src/mini_code_agent/agent tests/unit/agent
git commit -m "feat: require configured event journal"
```

## Task 7: Persistent Agent Integration

**Files:**

- Create: `tests/integration/test_persistent_trace_agent.py`

- [ ] Add an integration test that creates a Session, injects its journal, runs
  `ToolCall -> ToolResult -> final response`, closes/reopens SQLite, and verifies:

- Session and Run are terminal with exact turns/ToolCalls/usage;
- ordered events match the lifecycle contract;
- ToolCall IDs correlate without storing arguments/results;
- `verify_trace` returns the same count/head as Session;
- provider requests and `AgentResult` remain unchanged by persistence.

- [ ] Add an integration test with a governed write tool proving `ToolStarted(WRITE)` is durable
  before mutation and journal failure prevents any later write/tool call.

- [ ] Deliberately alter one stored payload in a copied database and prove verification returns
  `TRACE_CORRUPT` without exposing the payload.

- [ ] Run integration and full development tests; commit.

```powershell
python -m pytest tests/integration/test_persistent_trace_agent.py -q
python -m pytest --ignore=tests/smoke_test.py -q
git add tests/integration/test_persistent_trace_agent.py
git commit -m "test: verify persistent agent trace"
```

## Task 8: Documentation and `v0.8.0-alpha.0`

**Files:**

- Create: `docs/architecture/session-trace.md`
- Create: `docs/adr/0007-sqlite-session-trace.md`
- Modify: `docs/architecture/threat-model.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: version contract tests and `uv.lock`

- [ ] Document SQLite-only rationale, schema/transaction boundaries, Session/Run projections,
  event ordering, hash-chain semantics, busy-timeout behavior, configured Secret scrubbing, and
  all non-claims.

- [ ] Add learning material for SQLite transactions/WAL, event sourcing/materialized views,
  idempotency keys, Kafka/Flink analogies, indeterminate side effects, and exact code-reading
  exercises.

- [ ] Update the resume row with why, technology, function, solved problem, limitations, and only
  measured tests/coverage. Do not claim Checkpoint/Resume, encryption, tamper-proof audit, or
  exactly-once.

- [ ] Add version-first failing tests for `0.8.0a0`, update package metadata/lock, README, and
  Changelog.

- [ ] Run release gates independently so PowerShell cannot mask an earlier exit code:

```powershell
python -m uv lock --check
python -m uv run --isolated --python 3.12 --all-groups pytest --ignore=tests/smoke_test.py -q
python -m uv run --isolated --python 3.13 --all-groups pytest --ignore=tests/smoke_test.py --cov=mini_code_agent -q
python -m uv run --isolated --python 3.13 --all-groups ruff format --check .
python -m uv run --isolated --python 3.13 --all-groups ruff check .
python -m uv run --isolated --python 3.13 --all-groups pyright --pythonpath .\.venv\Scripts\python.exe
python -m uv run --isolated --python 3.13 --with bandit bandit -q -r src
python -m uv run --isolated --python 3.13 --with pip-audit pip-audit
python -m uv build --build-constraint build-constraints.txt --require-hashes
```

- [ ] Smoke-test the exact `0.8.0a0` wheel and sdist on Python 3.12/3.13 with `--no-project`.

- [ ] Fast-forward merge to main, verify the merged full suite and CLI version in an isolated
  environment, tag `v0.8.0-alpha.0`, then remove only the owned clean worktree and branch.
