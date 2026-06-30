# M4c Bounded Repair Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, host-controlled Repair runtime that admits only clean Git repositories
and exact tracked edit scopes, performs bounded Agent repair attempts, verifies each attempt with
fixed Pytest diagnostics, and stops on trusted success or a typed safety/budget reason.

**Architecture:** Add a new `repair` package above the existing `AgentRuntime`. The coordinator
owns approval, Git/Pytest evidence, budgets, fingerprints, and stopping; an `AgentRepairWorker`
performs exactly one governed Agent attempt. Extend policy execution with a preview-based action
guard, Git with an exact tracked-path query, and SQLite with an independent hash-chained Repair
journal.

**Tech Stack:** Python 3.12/3.13, asyncio, Pydantic v2, existing Agent/Policy/Git/Pytest boundaries,
stdlib SQLite/SHA-256/canonical JSON, Pytest, pytest-asyncio, Ruff, strict Pyright.

---

## File Map

**Create**

- `src/mini_code_agent/repair/__init__.py`: stable public Repair API.
- `src/mini_code_agent/repair/models.py`: immutable request, limits, result, approval, worker, and
  stop models.
- `src/mini_code_agent/repair/fingerprint.py`: canonical scope and failure fingerprints.
- `src/mini_code_agent/repair/scope.py`: Workspace scope validation and preview action guard.
- `src/mini_code_agent/repair/approval.py`: explicit Repair approval protocol and static handlers.
- `src/mini_code_agent/repair/worker.py`: Repair worker protocol and Agent runtime adapter.
- `src/mini_code_agent/repair/events.py`: typed Repair lifecycle events and journal protocol.
- `src/mini_code_agent/repair/runtime.py`: deterministic admission, baseline, attempt, verification,
  and stopping state machine.
- `src/mini_code_agent/persistence/repair.py`: SQLite Repair journal/read/verify implementation.
- `tests/unit/repair/test_repair_models.py`: model and invariant tests.
- `tests/unit/repair/test_repair_fingerprint.py`: canonical fingerprint tests.
- `tests/unit/repair/test_repair_scope.py`: scope and action guard tests.
- `tests/unit/repair/test_repair_worker.py`: bounded prompt and Agent adapter tests.
- `tests/unit/repair/test_repair_events.py`: event model tests.
- `tests/unit/repair/test_repair_runtime.py`: Repair state-machine tests.
- `tests/unit/persistence/test_repair.py`: durable Repair journal tests.
- `tests/integration/test_bounded_repair_agent.py`: real Git/Pytest/governed Agent repair flow.
- `docs/architecture/bounded-repair-loop.md`: composition, state machine, security, and non-claims.
- `docs/adr/0011-host-controlled-bounded-repair.md`: architecture decision.

**Modify**

- `src/mini_code_agent/policy/models.py`: action guard result.
- `src/mini_code_agent/policy/executor.py`: optional pre-policy action guard.
- `src/mini_code_agent/policy/__init__.py`: export guard contracts.
- `src/mini_code_agent/git/client.py`: root property and exact tracked-path protocol/query.
- `src/mini_code_agent/git/__init__.py`: export tracked-path reader.
- `src/mini_code_agent/persistence/models.py`: Repair trace record/verification models.
- `src/mini_code_agent/persistence/schema.py`: schema v3 Repair tables and v2 migration.
- `src/mini_code_agent/persistence/store.py`: Repair journal/read/verify accessors.
- `src/mini_code_agent/persistence/__init__.py`: export Repair persistence API.
- `tests/unit/policy/test_executor.py`: guard ordering and compatibility.
- `tests/unit/git/test_git_client.py`: tracked-path argv/output/error tests.
- `tests/unit/persistence/test_schema.py`: v3 create/migrate/rollback tests.
- `tests/unit/persistence/test_store.py`: Repair store accessors.
- `tests/smoke_test.py`: installed Repair API imports.
- `README.md`, `CHANGELOG.md`: M4c capability and boundaries.
- `docs/architecture/threat-model.md`: Repair controls and non-claims.
- `docs/learning/knowledge-map.md`, `docs/learning/progress.md`: prerequisite and implementation
  learning material.
- `docs/resume/project-profile.md`: evidence-backed project description and highlight.
- `pyproject.toml`, `uv.lock`: `0.12.0a0`.

## Task 1: Repair Models and Canonical Fingerprints

**Files:**
- Create: `src/mini_code_agent/repair/models.py`
- Create: `src/mini_code_agent/repair/fingerprint.py`
- Create: `tests/unit/repair/test_repair_models.py`
- Create: `tests/unit/repair/test_repair_fingerprint.py`

- [x] **Step 1: Write failing model tests**

Cover limit hard caps, one-to-32 unique exact editable paths, bounded prompts/reasons, valid repair
identifiers, `RepairResult.succeeded`, attempt ordering, and consistency between terminal reasons
and final test data.

```python
def test_repair_limits_enforce_hard_caps() -> None:
    with pytest.raises(ValidationError):
        RepairLimits(max_attempts=11)
    with pytest.raises(ValidationError):
        RepairLimits(max_patch_bytes=8 * 1024 * 1024 + 1)


def test_result_succeeds_only_for_trusted_terminal_reasons() -> None:
    assert result(RepairStopReason.REPAIRED).succeeded is True
    assert result(RepairStopReason.ALREADY_PASSING).succeeded is True
    assert result(RepairStopReason.MAX_ATTEMPTS).succeeded is False
```

- [x] **Step 2: Run the model tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_models.py -q
```

Expected: collection fails because `mini_code_agent.repair.models` does not exist.

- [x] **Step 3: Implement immutable public models**

Define:

```python
class RepairStopReason(StrEnum):
    ALREADY_PASSING = "already_passing"
    REPAIRED = "repaired"
    NOT_APPROVED = "not_approved"
    INVALID_SCOPE = "invalid_scope"
    DIRTY_REPOSITORY = "dirty_repository"
    TEST_INFRASTRUCTURE_ERROR = "test_infrastructure_error"
    TEST_MUTATED_REPOSITORY = "test_mutated_repository"
    WORKER_FAILED = "worker_failed"
    NO_PROGRESS = "no_progress"
    SCOPE_VIOLATION = "scope_violation"
    PATCH_LIMIT = "patch_limit"
    REPEATED_FAILURE = "repeated_failure"
    MAX_ATTEMPTS = "max_attempts"
    TIME_LIMIT = "time_limit"
    PERSISTENCE_ERROR = "persistence_error"


class RepairLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_elapsed_seconds: float = Field(default=900, gt=0, le=3600)
    max_patch_bytes: int = Field(default=256 * 1024, ge=1, le=8 * 1024 * 1024)
    max_same_failure: int = Field(default=2, ge=1, le=5)
    max_prompt_chars: int = Field(default=64 * 1024, ge=1024, le=256 * 1024)
```

Add bounded `RepairRequest`, `RepairPreview`, `RepairWorkerRequest`, `TestSummary`,
`RepairAttemptRecord`, and `RepairResult`. Use `extra="forbid"` and `frozen=True` everywhere.

- [x] **Step 4: Run model tests and verify GREEN**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_models.py -q
```

Expected: all model tests pass.

- [x] **Step 5: Write failing fingerprint tests**

```python
def test_failure_fingerprint_ignores_order_details_and_duration() -> None:
    left = failed_result(diagnostics=(diagnostic("b", details="one"), diagnostic("a")))
    right = failed_result(
        duration_ms=999,
        diagnostics=(diagnostic("a"), diagnostic("b", details="changed")),
    )
    assert failure_sha256(left) == failure_sha256(right)


def test_failure_fingerprint_changes_for_message_or_status() -> None:
    assert failure_sha256(failed_result(message="left")) != failure_sha256(
        failed_result(message="right")
    )
```

- [x] **Step 6: Run fingerprint tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_fingerprint.py -q
```

Expected: import failure for `failure_sha256`.

- [x] **Step 7: Implement canonical fingerprinting**

Use `json.dumps(..., ensure_ascii=False, separators=(",", ":"), sort_keys=True)` and UTF-8
SHA-256. Sort normalized diagnostics by outcome, file, line, class, test name, and message.
Exclude duration, stdout, stderr, and details.

- [x] **Step 8: Run focused tests and commit**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_models.py tests/unit/repair/test_repair_fingerprint.py -q
git add src/mini_code_agent/repair/models.py src/mini_code_agent/repair/fingerprint.py tests/unit/repair
git commit -m "feat: define bounded repair contracts"
```

Expected: focused tests pass and the commit succeeds.

## Task 2: Exact Tracked Scope and Policy Action Guard

**Files:**
- Create: `src/mini_code_agent/repair/scope.py`
- Create: `tests/unit/repair/test_repair_scope.py`
- Modify: `src/mini_code_agent/policy/models.py`
- Modify: `src/mini_code_agent/policy/executor.py`
- Modify: `src/mini_code_agent/policy/__init__.py`
- Modify: `src/mini_code_agent/git/client.py`
- Modify: `src/mini_code_agent/git/__init__.py`
- Modify: `tests/unit/policy/test_executor.py`
- Modify: `tests/unit/git/test_git_client.py`

- [x] **Step 1: Write failing Git tracked-path tests**

Assert the exact command contains `ls-files --error-unmatch -z --`, uses normalized exact paths,
deduplicates neither input nor output silently, rejects missing/extra/duplicate/malformed output,
and exposes a resolved `workspace_root`.

```python
result = await client.tracked_paths(("src/a.py", "tests/test_a.py"))
assert result == ("src/a.py", "tests/test_a.py")
assert runner.requests[-1].argv[-6:] == (
    "ls-files", "--error-unmatch", "-z", "--", "src/a.py", "tests/test_a.py"
)
```

- [x] **Step 2: Run tracked-path tests and verify RED**

```powershell
python -m uv run pytest tests/unit/git/test_git_client.py -q
```

Expected: `GitClient` has no `tracked_paths`.

- [x] **Step 3: Implement hardened tracked-path evidence**

Add:

```python
class GitTrackedPathReader(Protocol):
    async def tracked_paths(self, paths: tuple[str, ...]) -> tuple[str, ...]: ...


class GitService(GitStatusReader, GitDiffReader, GitTrackedPathReader, Protocol):
    @property
    def workspace_root(self) -> Path: ...
```

Validate one-to-32 unique POSIX display paths with no NUL, invoke the fixed command, split NUL
records, reject replacement characters and non-exact output sets, and return the requested order.

- [x] **Step 4: Run Git tests and verify GREEN**

```powershell
python -m uv run pytest tests/unit/git/test_git_client.py tests/unit/git/test_git_models.py -q
```

Expected: all Git tests pass.

- [x] **Step 5: Write failing action-guard and scope tests**

Add tests proving:

- `RepairScope.create` resolves, canonicalizes, sorts, and fingerprints exact regular files;
- duplicate identities, directories, links, missing paths, and more than 32 files fail;
- read-only previews pass;
- exact scoped writes pass;
- missing/multiple/out-of-scope resources, execute, and network fail;
- guard denial happens before policy approval and tool execution;
- omitting a guard preserves existing executor behavior.

```python
guard = RepairActionGuard(scope)
assert guard.evaluate(read_preview()).allowed is True
assert guard.evaluate(write_preview(resources=("src/app.py",))).allowed is True
assert guard.evaluate(write_preview(resources=("README.md",))).allowed is False
```

- [x] **Step 6: Run scope/guard tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_scope.py tests/unit/policy/test_executor.py -q
```

Expected: missing `RepairScope`, `RepairActionGuard`, and executor guard support.

- [x] **Step 7: Implement the preview action guard**

Add immutable `ActionGuardResult` and:

```python
class ActionGuard(Protocol):
    def evaluate(self, preview: ActionPreview) -> ActionGuardResult: ...


class AllowAllActionGuard:
    def evaluate(self, preview: ActionPreview) -> ActionGuardResult:
        del preview
        return ActionGuardResult(allowed=True)
```

`GovernedToolExecutor` accepts `guard: ActionGuard | None = None`, evaluates it immediately after a
valid preview, catches guard exceptions as static `permission_denied`, and never calls policy,
approval, or execution after denial.

- [x] **Step 8: Implement `RepairScope` and `RepairActionGuard`**

Scope fingerprint canonical payload:

```json
{"editable_paths":["src/a.py","src/b.py"],"version":1}
```

Only `READ_ONLY` is universally allowed. `WRITE` requires one or more resources and every resource
must be an exact scope member. `EXECUTE` and `NETWORK` are denied.

- [x] **Step 9: Run focused tests and commit**

```powershell
python -m uv run pytest tests/unit/git tests/unit/policy/test_executor.py tests/unit/repair/test_repair_scope.py -q
git add src/mini_code_agent/git src/mini_code_agent/policy src/mini_code_agent/repair/scope.py tests/unit/git tests/unit/policy/test_executor.py tests/unit/repair/test_repair_scope.py
git commit -m "feat: enforce exact tracked repair scope"
```

Expected: focused tests pass and the commit succeeds.

## Task 3: Explicit Repair Approval and Agent Worker Adapter

**Files:**
- Create: `src/mini_code_agent/repair/approval.py`
- Create: `src/mini_code_agent/repair/worker.py`
- Create: `tests/unit/repair/test_repair_worker.py`

- [x] **Step 1: Write failing approval and worker tests**

Test static approve/deny recording, approval handler exceptions, worker scope marker, deterministic
run IDs, canonical bounded request envelope, and rejection when the fixed instruction plus request
exceeds `max_prompt_chars`.

```python
result = await worker.run(worker_request(attempt=2))
assert runtime.calls[0].run_id == "repair-1-attempt-2"
assert json.loads(extract_envelope(runtime.calls[0].user_prompt))["attempt"] == 2
assert result.stop_reason is StopReason.COMPLETED
```

- [x] **Step 2: Run tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_worker.py -q
```

Expected: missing approval and worker modules.

- [x] **Step 3: Implement approval contracts**

```python
class RepairApprovalHandler(Protocol):
    async def approve(self, preview: RepairPreview) -> bool: ...


class StaticRepairApprovalHandler:
    def __init__(self, *, approved: bool) -> None:
        self.approved = approved
        self.requests: list[RepairPreview] = []

    async def approve(self, preview: RepairPreview) -> bool:
        self.requests.append(preview)
        return self.approved
```

Also provide `DenyAllRepairApprovalHandler`.

- [x] **Step 4: Implement the worker protocol and adapter**

Use a narrow runtime protocol instead of depending on a concrete class:

```python
class AgentRunner(Protocol):
    async def run(
        self, *, user_prompt: str, system_prompt: str = "", run_id: str | None = None
    ) -> AgentResult: ...


class RepairWorker(Protocol):
    @property
    def scope_sha256(self) -> str: ...
    async def run(self, request: RepairWorkerRequest) -> AgentResult: ...
```

Serialize only bounded test summaries, not stdout/stderr. Build one fixed instruction and canonical
JSON envelope. Validate the complete prompt length before invoking the Agent.

- [x] **Step 5: Run tests and commit**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_worker.py -q
git add src/mini_code_agent/repair/approval.py src/mini_code_agent/repair/worker.py tests/unit/repair/test_repair_worker.py
git commit -m "feat: adapt agent repair attempts"
```

Expected: all worker tests pass.

## Task 4: Typed Repair Events and In-Memory Journal

**Files:**
- Create: `src/mini_code_agent/repair/events.py`
- Create: `tests/unit/repair/test_repair_events.py`

- [x] **Step 1: Write failing event tests**

Cover identifiers, timestamps, attempt ranges, SHA-256 fields, bounded errors, event union
round-trips, exact duplicate recording, and terminal consistency.

```python
event = RepairAttemptStarted(
    repair_id="repair-1",
    attempt=1,
    failure_sha256="a" * 64,
)
journal.append(event)
assert journal.events == [event]
```

- [x] **Step 2: Run tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_events.py -q
```

Expected: missing Repair event types.

- [x] **Step 3: Implement event family and journal protocol**

Create `RepairStarted`, `RepairAttemptStarted`, `RepairVerificationStarted`,
`RepairAttemptCompleted`, and `RepairStopped`, all frozen and extra-forbid. Define:

```python
RepairEvent = (
    RepairStarted
    | RepairAttemptStarted
    | RepairVerificationStarted
    | RepairAttemptCompleted
    | RepairStopped
)


class RepairJournal(Protocol):
    def append(self, event: RepairEvent) -> None: ...
```

Provide `NullRepairJournal` and `RecordingRepairJournal`. Lifecycle events contain only hashes,
counts, statuses, IDs, attempts, elapsed time, and a bounded static error.

- [x] **Step 4: Run tests and commit**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_events.py -q
git add src/mini_code_agent/repair/events.py tests/unit/repair/test_repair_events.py
git commit -m "feat: define repair lifecycle events"
```

Expected: all event tests pass.

## Task 5: SQLite Schema v3 and Durable Repair Journal

**Files:**
- Create: `src/mini_code_agent/persistence/repair.py`
- Create: `tests/unit/persistence/test_repair.py`
- Modify: `src/mini_code_agent/persistence/models.py`
- Modify: `src/mini_code_agent/persistence/schema.py`
- Modify: `src/mini_code_agent/persistence/store.py`
- Modify: `src/mini_code_agent/persistence/__init__.py`
- Modify: `tests/unit/persistence/test_schema.py`
- Modify: `tests/unit/persistence/test_store.py`

- [x] **Step 1: Write failing schema v3 tests**

Assert fresh creation includes `repair_runs` and `repair_events`, v1 and v2 migrate without
rewriting existing rows, failed migration rolls back new objects/version, and future version 4 is
rejected.

```python
assert version == DATABASE_SCHEMA_VERSION == 3
assert {"repair_runs", "repair_events"} <= tables
```

- [x] **Step 2: Run schema tests and verify RED**

```powershell
python -m uv run pytest tests/unit/persistence/test_schema.py -q
```

Expected: schema version remains 2 and Repair tables are absent.

- [x] **Step 3: Implement sequential v1/v2 to v3 migration**

Add:

```sql
CREATE TABLE repair_runs (
    repair_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'stopped')),
    stop_reason TEXT,
    scope_sha256 TEXT NOT NULL CHECK (length(scope_sha256) = 64),
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    next_sequence INTEGER NOT NULL CHECK (next_sequence >= 1),
    trace_head_sha256 TEXT NOT NULL CHECK (length(trace_head_sha256) = 64)
);

CREATE TABLE repair_events (
    repair_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_sha256 TEXT NOT NULL CHECK (length(previous_sha256) = 64),
    event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
    PRIMARY KEY (repair_id, sequence),
    FOREIGN KEY (repair_id) REFERENCES repair_runs(repair_id)
);
```

Run migrations sequentially: v1 adds checkpoints, then v2 adds Repair tables, then verifies all
required tables.

- [x] **Step 4: Run schema tests and verify GREEN**

```powershell
python -m uv run pytest tests/unit/persistence/test_schema.py -q
```

Expected: schema tests pass.

- [x] **Step 5: Write failing journal tests**

Cover required start-first transitions, attempt ordering, start/verification/completion sequence,
terminal stop, no events after stop, event-ID idempotency/conflict, timestamp order, size/event
limits, busy timeout, four payload/hash/projection tamper cases, reopen/read pagination, and
started-only incomplete detection.

- [x] **Step 6: Run journal tests and verify RED**

```powershell
python -m uv run pytest tests/unit/persistence/test_repair.py -q
```

Expected: missing SQLite Repair journal.

- [x] **Step 7: Implement canonical Repair codec and journal**

`repair.py` must:

- encode/decode `RepairEvent` through a `TypeAdapter`;
- hash `{repair_id, sequence, previous_sha256, schema_version, event}`;
- use `BEGIN IMMEDIATE`, parameterized SQL, existing busy timeout and `synchronous=FULL`;
- create the projection atomically with `RepairStarted`;
- append and update projection in one transaction;
- stop projection atomically with `RepairStopped`;
- validate chain/projection during reads and verification;
- redact only the bounded `RepairStopped.error` using configured secrets.

- [x] **Step 8: Add store accessors**

Expose:

```python
store.repair_journal()
store.read_repair_trace(repair_id, after_sequence=0, limit=100)
store.verify_repair_trace(repair_id)
```

Return typed `RepairTraceRecord` and `RepairTraceVerification`.

- [x] **Step 9: Run persistence tests and commit**

```powershell
python -m uv run pytest tests/unit/persistence -q
git add src/mini_code_agent/persistence tests/unit/persistence
git commit -m "feat: persist repair lifecycle trace"
```

Expected: all persistence tests pass.

## Task 6: Repair Admission and Baseline Verification

**Files:**
- Create: `src/mini_code_agent/repair/runtime.py`
- Create: `tests/unit/repair/test_repair_runtime.py`

- [x] **Step 1: Write failing admission tests**

Build recording fake Git/Pytest/worker/approval/journal dependencies. Cover:

- constructor root mismatch;
- invalid scope before approval;
- approval reject/exception with zero Git/Pytest/worker calls;
- dirty staged/unstaged/untracked/rename/unmerged/submodule status;
- tracked-path mismatch;
- worker scope mismatch;
- journal start failure;
- baseline already passing;
- baseline infrastructure result;
- baseline test mutation detected by changed status or diff;
- volatile mode must be explicit.

```python
result = await runtime.run(request())
assert result.stop_reason is RepairStopReason.ALREADY_PASSING
assert worker.calls == []
assert pytest.calls == [("tests",)]
```

- [x] **Step 2: Run admission tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_runtime.py -q
```

Expected: missing `RepairRuntime`.

- [x] **Step 3: Implement constructor and dependency protocols**

Add narrow protocols for Git, tests, clock, and worker. Require identical resolved Workspace,
Git, and Pytest roots. Require a journal unless `allow_volatile=True`; in volatile mode install
`NullRepairJournal`.

- [x] **Step 4: Implement approval and admission**

Order:

1. validate request and scope read-only;
2. build bounded preview;
3. call approval;
4. revalidate scope;
5. read status and both staged/unstaged diff;
6. require clean evidence;
7. require exact tracked paths;
8. compare worker scope fingerprint;
9. persist `RepairStarted`.

Convert expected failures to static typed results; propagate `CancelledError`.

- [x] **Step 5: Implement baseline test evidence**

Read status and unstaged diff immediately before and after `PytestRunner.run`. Require both hashes
unchanged. Classify only complete exit-1 failures with at least one failed/error case as
repairable. Return already-passing only for complete exit-0 reports.

- [x] **Step 6: Run admission tests and commit**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_runtime.py -q
git add src/mini_code_agent/repair/runtime.py tests/unit/repair/test_repair_runtime.py
git commit -m "feat: admit bounded repair sessions"
```

Expected: admission/baseline tests pass.

## Task 7: Bounded Attempt Loop and Terminal Evidence

**Files:**
- Modify: `src/mini_code_agent/repair/runtime.py`
- Modify: `tests/unit/repair/test_repair_runtime.py`

- [x] **Step 1: Write failing attempt-loop tests**

Cover one-attempt success, worker non-completion/exception, no diff, repeated diff, ordinary exact
modification, staged/new/deleted/renamed/unmerged/submodule/out-of-scope changes, patch limit,
test mutation, infrastructure failure after repair, repeated failure, changed failure followed by
max attempts, elapsed budget before worker/test, journal failure at every event, and cancellation.

```python
assert result.stop_reason is RepairStopReason.REPAIRED
assert len(result.attempts) == 1
assert result.attempts[0].worker_run_id == "repair-1-attempt-1"
assert result.final_test.status is PytestExecutionStatus.PASSED
```

- [x] **Step 2: Run attempt tests and verify RED**

```powershell
python -m uv run pytest tests/unit/repair/test_repair_runtime.py -q
```

Expected: attempt assertions fail because runtime stops after baseline.

- [x] **Step 3: Implement worker-attempt validation**

Before each worker and test boundary, compare monotonic elapsed time. Persist
`RepairAttemptStarted`; call one worker; require `StopReason.COMPLETED`; read status/staged and
unstaged diff; enforce only ordinary unstaged `M` entries exactly within scope; require non-empty,
new, bounded diff.

- [x] **Step 4: Implement verification and stall detection**

Persist `RepairVerificationStarted`, run the original targets, verify test non-mutation, create one
immutable attempt record, then persist `RepairAttemptCompleted`. Count canonical failure hashes
including baseline. Return `REPEATED_FAILURE` at the configured count and `MAX_ATTEMPTS` only after
recording the final distinct repairable failure.

- [x] **Step 5: Implement fail-closed stopping**

Every ordinary return must append one `RepairStopped`. If that append fails, return
`PERSISTENCE_ERROR` without attempting a second terminal append. Bound public errors to 500
characters and never include dependency exception text. Cancellation bypasses result creation.

- [x] **Step 6: Run Repair unit tests and commit**

```powershell
python -m uv run pytest tests/unit/repair -q
git add src/mini_code_agent/repair/runtime.py tests/unit/repair/test_repair_runtime.py
git commit -m "feat: orchestrate bounded repair attempts"
```

Expected: all Repair unit tests pass.

## Task 8: Real Governed Repair Integration

**Files:**
- Create: `tests/integration/test_bounded_repair_agent.py`
- Create: `src/mini_code_agent/repair/__init__.py`
- Modify: `src/mini_code_agent/persistence/__init__.py`
- Modify: `src/mini_code_agent/policy/__init__.py`
- Modify: `src/mini_code_agent/git/__init__.py`

- [x] **Step 1: Write failing end-to-end repair test**

Create a temporary Git repository with:

```python
def add(left: int, right: int) -> int:
    return left - right
```

and a failing test expecting addition. Compose:

- `WorkspaceBoundary`;
- real `GitClient`;
- fixed `PytestRunner`;
- `ReadFileTool`, `EditFileTool`, and `GitDiffTool`;
- `GovernedToolExecutor` with `RepairActionGuard`;
- explicit write approval policy;
- `ScriptedProvider` that reads then applies one hash-guarded edit;
- `AgentRuntime` with SQLite Agent journal/checkpoints;
- `AgentRepairWorker`;
- `RepairRuntime` with SQLite Repair journal.

Assert trusted pass, only `src/calculator.py` changed, no staged/new files, one worker run, one repair
attempt, valid Agent trace, valid Repair trace, and no patch/test output in Repair lifecycle rows.

- [x] **Step 2: Run integration test and verify RED**

```powershell
python -m uv run pytest tests/integration/test_bounded_repair_agent.py -q
```

Expected: public Repair composition or runtime behavior is incomplete.

- [x] **Step 3: Complete public exports and integration**

Export only stable models, protocols, runtime, worker, scope guard, approval handlers, events, and
SQLite Repair types. Avoid importing heavy runtime modules through unrelated package roots.

- [x] **Step 4: Add adversarial integration cases**

Add real tests for:

- worker attempts to edit an out-of-scope tracked file and receives `permission_denied` before
  disk mutation;
- dirty repository rejects before Pytest/Provider;
- a test modifies a tracked file and triggers `test_mutated_repository`;
- interrupted started-only SQLite Repair trace reopens as incomplete and is never replayed.

- [x] **Step 5: Run integration and regression suites**

```powershell
python -m uv run pytest tests/integration/test_bounded_repair_agent.py tests/integration/test_governed_pytest_agent.py tests/integration/test_readonly_git_agent.py tests/integration/test_governed_write_agent.py -q
python -m uv run pytest tests/unit/repair tests/unit/git tests/unit/policy tests/unit/persistence -q
```

Expected: all focused integration/regression tests pass.

- [x] **Step 6: Commit**

```powershell
git add src/mini_code_agent/repair src/mini_code_agent/git/__init__.py src/mini_code_agent/policy/__init__.py src/mini_code_agent/persistence/__init__.py tests/integration/test_bounded_repair_agent.py
git commit -m "test: verify governed repair workflow"
```

Expected: commit succeeds.

## Task 9: Documentation, Learning Material, Resume Evidence, and Release

**Files:**
- Create: `docs/architecture/bounded-repair-loop.md`
- Create: `docs/adr/0011-host-controlled-bounded-repair.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture/threat-model.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `tests/smoke_test.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [x] **Step 1: Write architecture and ADR**

Document exact composition, approval boundary, clean/tracked scope admission, action guard, test
classification, Git evidence checks, canonical failure fingerprint, attempt state machine,
required Repair journal, crash non-resume rule, and all design non-claims.

- [x] **Step 2: Update learning and resume documents in parallel**

Learning document must explain:

- feedback control versus an ordinary Agent loop;
- why status clean does not imply an ignored file is tracked;
- canonical fingerprints and repeated-failure stopping;
- control-plane approval versus data-plane write governance;
- final-state drift detection versus isolation;
- required journal and why interrupted Repair is not automatically resumed.

Resume material must retain the table columns:

```text
Why needed | Technical implementation | Function | Problem solved | Evidence
```

Do not claim benchmark improvement until a fixed defect suite exists and is measured.

- [x] **Step 3: Update version and smoke contract**

Set `pyproject.toml` to `0.12.0a0`, regenerate `uv.lock`, import `RepairRuntime`,
`AgentRepairWorker`, and `RepairActionGuard` in `tests/smoke_test.py`, and add an
`[0.12.0-alpha.0]` changelog section.

- [x] **Step 4: Run all local quality gates**

```powershell
python -m uv lock --check
python -m uv run --isolated --python 3.12 --all-groups pytest --ignore=tests/smoke_test.py -q
python -m uv run --isolated --python 3.13 --all-groups pytest --ignore=tests/smoke_test.py --cov=mini_code_agent -q
python -m uv run ruff format --check .
python -m uv run ruff check .
python -m uv run pyright
python -m bandit -q -r src
python -m pip_audit --strict --desc
```

Expected: lock current; all tests pass on both Python versions; branch coverage is at least 85%;
Ruff, strict Pyright, Bandit, and dependency audit report no failures.

- [x] **Step 5: Build and smoke exact artifacts**

Build wheel and sdist with `build-constraints.txt --require-hashes`, compute SHA-256, install each
artifact separately on Python 3.12 and 3.13, run `tests/smoke_test.py`, and verify
`mini-code-agent --version` reports `0.12.0a0`.

- [ ] **Step 6: Complete plan, merge, tag, and publish**

Mark checkboxes, commit evidence, fast-forward merge to `main`, rerun merged verification, tag
`v0.12.0-alpha.0`, push `main` and tag, create a GitHub prerelease with exact wheel/sdist assets,
verify digests and the final Ubuntu/Windows x Python 3.12/3.13 Actions run, then record URLs and
hashes in learning/resume evidence.
