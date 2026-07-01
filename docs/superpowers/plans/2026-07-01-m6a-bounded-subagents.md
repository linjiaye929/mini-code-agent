# M6a Bounded Analysis Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add host-profiled, non-recursive, read-only Subagents that run one to four independent
analysis tasks with structured concurrency and return bounded summaries plus hashed Tool evidence.

**Architecture:** Add a `subagents` package around the existing `AgentRuntime`. Trusted factories
create a distinct Provider and governed read-only Tool executor per child; the supervisor validates
exact capabilities and `TrustSource.SUBAGENT`, runs children in an `asyncio.TaskGroup`, and adapts
the ordered batch into one normal governed parent Tool.

**Tech Stack:** Python 3.12/3.13, asyncio TaskGroup/timeout, Pydantic v2, existing AgentRuntime,
ToolRegistry, GovernedToolExecutor, Policy/Hooks, Pytest/pytest-asyncio, Ruff, strict Pyright.

---

## File Map

**Create**

- `src/mini_code_agent/subagents/models.py`: immutable profiles, limits, child/batch results,
  statuses, evidence, and public errors.
- `src/mini_code_agent/subagents/contracts.py`: Provider/Tool factory and governed-child protocols.
- `src/mini_code_agent/subagents/evidence.py`: transcript evidence extraction and canonical result
  hashing.
- `src/mini_code_agent/subagents/events.py`: metadata-only Subagent lifecycle events and sinks.
- `src/mini_code_agent/subagents/supervisor.py`: child composition, one-child execution,
  structured fan-out/fan-in, timeout, cancellation, and result aggregation.
- `src/mini_code_agent/subagents/tools.py`: dynamic read-only parent Tool and multi-profile builder.
- `src/mini_code_agent/subagents/__init__.py`: stable host-facing exports.
- `tests/unit/subagents/test_models.py`
- `tests/unit/subagents/test_contracts.py`
- `tests/unit/subagents/test_evidence.py`
- `tests/unit/subagents/test_events.py`
- `tests/unit/subagents/test_supervisor.py`
- `tests/unit/subagents/test_tools.py`
- `tests/integration/test_governed_subagent_agent.py`
- `docs/architecture/governed-subagents.md`
- `docs/adr/0014-bounded-host-profiled-subagents.md`

**Modify**

- `src/mini_code_agent/policy/models.py`: add `TrustSource.SUBAGENT`.
- `tests/unit/policy/test_engine.py`: freeze the new public enum and matching behavior.
- `docs/architecture/threat-model.md`
- `docs/learning/knowledge-map.md`
- `docs/learning/progress.md`
- `docs/resume/project-profile.md`
- `README.md`
- `SECURITY.md`
- `CHANGELOG.md`
- `pyproject.toml`: bump to `0.15.0a0`.
- `uv.lock`
- release-version tests and `tests/smoke_test.py`.

## Task 1: Add Subagent provenance and immutable profiles

**Files:**
- Modify: `src/mini_code_agent/policy/models.py`
- Modify: `tests/unit/policy/test_engine.py`
- Create: `src/mini_code_agent/subagents/__init__.py`
- Create: `src/mini_code_agent/subagents/models.py`
- Create: `tests/unit/subagents/test_models.py`

- [x] **Step 1: Write failing provenance tests**

Add:

```python
def test_policy_enums_are_stable() -> None:
    assert {item.value for item in TrustSource} == {
        "user",
        "project",
        "model",
        "extension",
        "subagent",
    }


def test_rule_can_match_subagent_without_allowing_parent_model() -> None:
    engine = PolicyEngine(
        rules=(
            PolicyRule(
                id="allow-subagent-read",
                decision=PolicyDecision.ALLOW,
                rationale="Bounded child reads are allowed.",
                side_effect=SideEffect.READ_ONLY,
                trust_source=TrustSource.SUBAGENT,
            ),
        )
    )

    child = engine.evaluate(
        request(side_effect=SideEffect.READ_ONLY, trust_source=TrustSource.SUBAGENT)
    )
    parent = engine.evaluate(
        request(side_effect=SideEffect.READ_ONLY, trust_source=TrustSource.MODEL)
    )

    assert child.rule_id == "allow-subagent-read"
    assert parent.rule_id == "default-read-only"
```

- [x] **Step 2: Run the provenance tests and observe red**

Run:

```powershell
py -m uv run pytest tests/unit/policy/test_engine.py -q
```

Expected: the stable enum assertion fails because `subagent` does not exist.

- [x] **Step 3: Add the public provenance value**

Add exactly:

```python
class TrustSource(StrEnum):
    USER = "user"
    PROJECT = "project"
    MODEL = "model"
    EXTENSION = "extension"
    SUBAGENT = "subagent"
```

Run the focused Policy tests and expect all to pass.

- [x] **Step 4: Write failing profile/model tests**

Cover:

```python
def test_analysis_profile_is_exact_frozen_and_bounded() -> None:
    profile = profile_for(
        tool_names=("read_file", "search_text"),
        max_tasks=3,
        max_concurrency=2,
    )

    assert profile.mode == "analysis"
    assert profile.tool_names == ("read_file", "search_text")
    assert profile.limits.max_tasks == 3
    with pytest.raises(ValidationError):
        profile.tool_names = ("write_file",)  # type: ignore[misc]


@pytest.mark.parametrize(
    "tool_names",
    [
        (),
        ("read_file", "read_file"),
        ("delegate_analysis",),
    ],
)
def test_analysis_profile_rejects_empty_duplicate_or_recursive_tools(
    tool_names: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        profile_for(tool_names=tool_names)


def test_limits_reject_inconsistent_concurrency_and_evidence_budget() -> None:
    with pytest.raises(ValidationError):
        SubagentLimits(max_tasks=2, max_concurrency=3)
    with pytest.raises(ValidationError):
        profile_for(
            agent_limits=AgentLimits(max_tool_calls=65),
            limits=SubagentLimits(max_evidence_items=64),
        )


def test_child_and_batch_results_are_frozen_and_count_consistent() -> None:
    child = completed_child(ordinal=0)
    batch = SubagentBatchResult.from_children(
        profile_id="review",
        children=(child,),
        duration_ms=10,
    )

    assert batch.completed == 1
    assert batch.timed_out == 0
    assert len(batch.result_sha256) == 64
    with pytest.raises(ValidationError):
        SubagentBatchResult(
            **batch.model_dump(),
            completed=0,
        )
```

Also reject task/result/status IDs over bounds, non-static public errors, summaries over 32,000
characters, evidence over 256 items, `max_tasks > 4`, `max_concurrency > 4`, child timeout over 600,
batch timeout over 900, and batch timeout lower than child timeout.

- [x] **Step 5: Run model tests and observe collection failure**

Run:

```powershell
py -m uv run pytest tests/unit/subagents/test_models.py -q
```

Expected: import failure because the package does not exist.

- [x] **Step 6: Implement immutable models**

Implement these stable public shapes:

```python
class SubagentStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    BATCH_TIMED_OUT = "batch_timed_out"


class SubagentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tasks: int = Field(default=4, ge=1, le=4)
    max_concurrency: int = Field(default=2, ge=1, le=4)
    max_task_chars: int = Field(default=4_000, ge=1, le=20_000)
    child_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    batch_timeout_seconds: float = Field(default=300.0, gt=0, le=900)
    max_summary_chars: int = Field(default=8_000, ge=1, le=32_000)
    max_evidence_items: int = Field(default=64, ge=0, le=256)
    max_result_bytes: int = Field(default=131_072, ge=1, le=1_048_576)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.max_concurrency > self.max_tasks:
            raise ValueError("Subagent concurrency cannot exceed task count.")
        if self.batch_timeout_seconds < self.child_timeout_seconds:
            raise ValueError("Batch timeout cannot be lower than child timeout.")
        return self


class SubagentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    local_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=20_000)
    tool_names: tuple[str, ...] = Field(min_length=1, max_length=16)
    mode: Literal["analysis"] = "analysis"
    agent_limits: AgentLimits = Field(default_factory=AgentLimits)
    limits: SubagentLimits = Field(default_factory=SubagentLimits)

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("Subagent Tool names must be unique.")
        if self.local_name in self.tool_names or any(
            name.startswith("delegate_") for name in self.tool_names
        ):
            raise ValueError("Subagent profiles cannot recurse.")
        if self.agent_limits.max_tool_calls > self.limits.max_evidence_items:
            raise ValueError("Every child ToolCall requires an evidence slot.")
        return self
```

Add bounded `SubagentEvidenceItem`, `SubagentChildResult`, `SubagentBatchResult`, public error code
enum, and runtime error class. Freeze tuples and calculate count consistency in a model validator.
Do not include raw task, prompt, arguments, ToolResult content, or exception text.

- [x] **Step 7: Run model, Policy, Ruff, and Pyright checks**

Run:

```powershell
py -m uv run pytest tests/unit/subagents/test_models.py tests/unit/policy/test_engine.py -q
py -m uv run ruff check src/mini_code_agent/subagents src/mini_code_agent/policy/models.py tests/unit/subagents tests/unit/policy/test_engine.py
py -m uv run pyright src/mini_code_agent/subagents/models.py tests/unit/subagents/test_models.py
```

Expected: all pass.

- [x] **Step 8: Commit provenance and models**

```powershell
git add src/mini_code_agent/policy/models.py src/mini_code_agent/subagents/__init__.py src/mini_code_agent/subagents/models.py tests/unit/policy/test_engine.py tests/unit/subagents/test_models.py
git commit -m "feat: define bounded subagent profiles"
```

## Task 2: Define trusted composition and evidence contracts

**Files:**
- Create: `src/mini_code_agent/subagents/contracts.py`
- Create: `src/mini_code_agent/subagents/evidence.py`
- Create: `tests/unit/subagents/test_contracts.py`
- Create: `tests/unit/subagents/test_evidence.py`

- [x] **Step 1: Write failing composition tests**

Build fake factories and prove:

```python
def test_validate_child_tools_requires_exact_governed_subagent_contract(
    profile: SubagentProfile,
    governed_tools: GovernedToolExecutor,
) -> None:
    validate_child_tools(profile, governed_tools)


@pytest.mark.parametrize(
    "mutation",
    ["extra", "missing", "write", "not_governed", "model_provenance"],
)
def test_validate_child_tools_rejects_authority_drift(
    profile: SubagentProfile,
    mutation: str,
) -> None:
    tools = tools_for_mutation(mutation)
    with pytest.raises(SubagentCompositionError):
        validate_child_tools(profile, tools)
```

Test that Provider and Tool factories receive only profile, child ID, and pinned workspace root;
their exceptions map to one static composition error.

- [x] **Step 2: Write failing evidence tests**

Construct a real `AgentResult` transcript with two ToolCalls and correlated ToolResults:

```python
def test_extract_evidence_returns_only_bounded_hash_metadata() -> None:
    secret = "do-not-copy-result"
    result = agent_result_with_tools(
        (
            ("call-1", "read_file", secret, False),
            ("call-2", "search_text", "two", True),
        )
    )

    evidence = extract_subagent_evidence(result, max_items=2)

    assert [item.tool_name for item in evidence] == ["read_file", "search_text"]
    assert evidence[0].content_chars == len(secret)
    assert evidence[0].content_sha256 == sha256(secret.encode()).hexdigest()
    assert secret not in json.dumps([item.model_dump() for item in evidence])
```

Also reject duplicate/missing correlation, result-before-call, excessive items, malformed transcript
roles, and mismatched child result IDs. Verify canonical hashes ignore dictionary insertion order
and reject NaN/Infinity.

- [x] **Step 3: Run tests and observe red**

```powershell
py -m uv run pytest tests/unit/subagents/test_contracts.py tests/unit/subagents/test_evidence.py -q
```

Expected: missing modules/functions.

- [x] **Step 4: Implement composition protocols and validation**

Define:

```python
class SubagentProviderFactory(Protocol):
    def create(
        self,
        profile: SubagentProfile,
        child_id: str,
    ) -> ModelProvider: ...


class SubagentToolFactory(Protocol):
    def create(
        self,
        profile: SubagentProfile,
        workspace_root: Path,
    ) -> ToolExecutor: ...


class GovernedSubagentTools(ToolExecutor, Protocol):
    @property
    def governance_enforced(self) -> Literal[True]: ...

    def trust_source_for(self, tool_name: str) -> TrustSource: ...
```

`validate_child_tools()` must require exact ordered Tool names, all `READ_ONLY`,
`governance_enforced is True`, and `trust_source_for(name) is SUBAGENT`. Catch implementation
exceptions and raise only `SubagentCompositionError`.

- [x] **Step 5: Implement evidence extraction and canonical hash**

Walk messages in order. Register each assistant ToolCall exactly once; accept a user ToolResult only
after its call; emit one item in ToolCall order; require every call to have one result. Hash UTF-8
content but never retain it.

Expose:

```python
def extract_subagent_evidence(
    result: AgentResult,
    *,
    max_items: int,
) -> tuple[SubagentEvidenceItem, ...]: ...


def subagent_result_sha256(value: BaseModel) -> str: ...
```

Use canonical ASCII JSON with sorted keys, compact separators, `allow_nan=False`, and SHA-256.

- [x] **Step 6: Run focused and static checks**

```powershell
py -m uv run pytest tests/unit/subagents/test_contracts.py tests/unit/subagents/test_evidence.py -q
py -m uv run ruff check src/mini_code_agent/subagents tests/unit/subagents
py -m uv run pyright src/mini_code_agent/subagents tests/unit/subagents
```

- [x] **Step 7: Commit contracts and evidence**

```powershell
git add src/mini_code_agent/subagents/contracts.py src/mini_code_agent/subagents/evidence.py tests/unit/subagents/test_contracts.py tests/unit/subagents/test_evidence.py
git commit -m "feat: validate subagent capabilities and evidence"
```

## Task 3: Add metadata-only events and one-child execution

**Files:**
- Create: `src/mini_code_agent/subagents/events.py`
- Create: `src/mini_code_agent/subagents/supervisor.py`
- Create: `tests/unit/subagents/test_events.py`
- Create: `tests/unit/subagents/test_supervisor.py`

- [x] **Step 1: Write failing event tests**

Require immutable bounded events:

```python
def test_subagent_events_omit_task_prompt_summary_and_results() -> None:
    event = SubagentCompleted(
        parent_tool_call_id="parent-1",
        profile_id="review",
        child_id="subagent-1",
        ordinal=0,
        status=SubagentStatus.COMPLETED,
        duration_ms=12,
        turns=2,
        tool_calls=1,
        usage=TokenUsage(input_tokens=10, output_tokens=3),
        result_sha256="a" * 64,
    )

    payload = event.model_dump_json()
    assert "secret-task" not in payload
    assert set(event.model_dump()) == {
        "event_id",
        "timestamp",
        "type",
        "parent_tool_call_id",
        "profile_id",
        "child_id",
        "ordinal",
        "status",
        "duration_ms",
        "turns",
        "tool_calls",
        "usage",
        "result_sha256",
    }
```

Round-trip all four event types through `TypeAdapter[SubagentEvent]`; reject IDs, durations, counts,
and hashes over bounds; prove sink exceptions do not alter execution.

- [x] **Step 2: Write failing one-child supervisor tests**

Use injected deterministic ID and monotonic factories:

```python
@pytest.mark.asyncio
async def test_one_child_gets_fresh_context_exact_tools_and_bounded_result(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(final_text="review complete")
    tools = recording_governed_read_tools(tmp_path)
    supervisor = supervisor_for(
        tmp_path,
        providers=(provider,),
        tools=(tools,),
        child_ids=("subagent-1",),
    )

    batch = await supervisor.run_batch(
        parent_tool_call_id="parent-1",
        tasks=("Inspect parser bounds.",),
    )

    assert provider.requests[0].messages == (
        Message.user_text("Inspect parser bounds."),
    )
    assert [item.name for item in provider.requests[0].tools] == list(
        supervisor.profile.tool_names
    )
    assert batch.children[0].untrusted_summary == "review complete"
    assert batch.children[0].status is SubagentStatus.COMPLETED
```

Also test a non-completed `StopReason`, provider factory error, Tool factory error, duplicate object
identity, malformed AgentResult, and event order.

- [x] **Step 3: Run tests and observe red**

```powershell
py -m uv run pytest tests/unit/subagents/test_events.py tests/unit/subagents/test_supervisor.py -q
```

- [x] **Step 4: Implement Subagent events**

Keep the union separate from `AgentEvent` because parent run/turn context is unavailable at the Tool
boundary. Add `NullSubagentEventSink` and `RecordingSubagentEventSink`. Publishing catches ordinary
sink exceptions; cancellation is not involved because publishing is synchronous.

- [x] **Step 5: Implement child preparation and execution**

`SubagentSupervisor.__init__` snapshots profile/factories/root and validates the root. Before any
Provider call, `_prepare_children()` creates all providers/executors, rejects reused object IDs,
validates exact child Tools, and builds one `AgentRuntime` per task.

Run IDs use host IDs:

```python
run_id = f"subagent-{child_id}"
result = await runtime.run(
    user_prompt=task,
    system_prompt=profile.system_prompt,
    run_id=run_id,
)
```

Map completed, stopped, timed-out, and failed paths to static result models. Truncate summaries by
profile character limit before hashing. Never copy `AgentResult.messages` into a public model.

- [x] **Step 6: Run focused and static checks**

```powershell
py -m uv run pytest tests/unit/subagents/test_events.py tests/unit/subagents/test_supervisor.py -q
py -m uv run ruff check src/mini_code_agent/subagents tests/unit/subagents
py -m uv run pyright src/mini_code_agent/subagents tests/unit/subagents
```

- [x] **Step 7: Commit the one-child lifecycle**

```powershell
git add src/mini_code_agent/subagents/events.py src/mini_code_agent/subagents/supervisor.py tests/unit/subagents/test_events.py tests/unit/subagents/test_supervisor.py
git commit -m "feat: run isolated subagent lifecycles"
```

## Task 4: Add structured batch concurrency, timeout, and cancellation

**Files:**
- Modify: `src/mini_code_agent/subagents/supervisor.py`
- Modify: `tests/unit/subagents/test_supervisor.py`

- [x] **Step 1: Write failing concurrency tests**

Add deterministic gate providers:

```python
@pytest.mark.asyncio
async def test_batch_runs_children_concurrently_and_preserves_input_order(
    tmp_path: Path,
) -> None:
    gate = ConcurrencyGate(expected=2)
    providers = (
        GatedProvider(gate, result="first", delay_after_gate=0.05),
        GatedProvider(gate, result="second", delay_after_gate=0),
    )
    supervisor = supervisor_for(tmp_path, providers=providers, max_concurrency=2)

    result = await supervisor.run_batch(
        parent_tool_call_id="parent-1",
        tasks=("slow first", "fast second"),
    )

    assert gate.peak == 2
    assert [child.untrusted_summary for child in result.children] == [
        "first",
        "second",
    ]
```

Add:

- one child timeout while a sibling completes;
- ordinary child failure while a sibling completes;
- max concurrency one never overlaps;
- outer batch timeout marks every unfinished ordinal `BATCH_TIMED_OUT`;
- external cancellation cancels all gated providers and re-raises `CancelledError`;
- no pending task remains after completion/cancellation;
- completed/timed-out/failed counts and batch hash are deterministic.

- [x] **Step 2: Run concurrency tests and observe red**

```powershell
py -m uv run pytest tests/unit/subagents/test_supervisor.py -q -k "concurrent or timeout or cancel"
```

- [x] **Step 3: Implement TaskGroup fan-out/fan-in**

Use one result slot per input ordinal and one semaphore:

```python
results: list[SubagentChildResult | None] = [None] * len(tasks)
semaphore = asyncio.Semaphore(profile.limits.max_concurrency)

async def run_at(ordinal: int) -> None:
    async with semaphore:
        results[ordinal] = await self._run_child(prepared[ordinal])

try:
    async with asyncio.timeout(profile.limits.batch_timeout_seconds):
        async with asyncio.TaskGroup() as group:
            for ordinal in range(len(tasks)):
                group.create_task(run_at(ordinal))
except TimeoutError:
    for ordinal, result in enumerate(results):
        if result is None:
            results[ordinal] = self._batch_timeout_result(prepared[ordinal])
```

`_run_child()` wraps only its own timeout and ordinary failures. It must always re-raise
`CancelledError`. External cancellation must not be converted into batch timeout.

- [x] **Step 4: Verify focused and complete Subagent tests**

```powershell
py -m uv run pytest tests/unit/subagents -q
py -m uv run ruff check src/mini_code_agent/subagents tests/unit/subagents
py -m uv run pyright src/mini_code_agent/subagents tests/unit/subagents
```

- [x] **Step 5: Commit structured concurrency**

```powershell
git add src/mini_code_agent/subagents/supervisor.py tests/unit/subagents/test_supervisor.py
git commit -m "feat: coordinate bounded subagent batches"
```

## Task 5: Expose one governed parent Tool per profile

**Files:**
- Create: `src/mini_code_agent/subagents/tools.py`
- Create: `tests/unit/subagents/test_tools.py`
- Modify: `src/mini_code_agent/subagents/__init__.py`

- [x] **Step 1: Write failing Tool tests**

Cover:

```python
@pytest.mark.asyncio
async def test_preview_is_read_only_medium_risk_and_bounded(
    tool: SubagentAnalysisTool,
) -> None:
    preview = await tool.preview(
        call_for(tasks=("Inspect parser.", "Inspect serializer."), reason="Independent review.")
    )

    assert preview.side_effect is SideEffect.READ_ONLY
    assert preview.risk is RiskLevel.MEDIUM
    assert preview.resources == (".",)
    assert "2" in preview.summary
    assert "Inspect parser." not in preview.model_dump_json()


@pytest.mark.asyncio
async def test_execute_returns_deterministic_bounded_batch_json(
    tool: SubagentAnalysisTool,
) -> None:
    result = await tool.execute(call_for(tasks=("one", "two")))
    payload = json.loads(result.content)

    assert result.is_error is False
    assert payload["content_type"] == "subagent_batch_result"
    assert [child["ordinal"] for child in payload["children"]] == [0, 1]
```

Reject unknown fields, empty/too many/duplicate tasks, task length, wrong Tool name, bad reason,
oversized serialized result, supervisor failure, malformed result, and builder conflicts.

Prove `build_subagent_tools()` rejects duplicate profile IDs/local names and any parent local name
appearing in any child `tool_names`.

- [x] **Step 2: Run Tool tests and observe red**

```powershell
py -m uv run pytest tests/unit/subagents/test_tools.py -q
```

- [x] **Step 3: Implement dynamic Tool definitions**

The input schema is profile-specific:

```python
{
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": profile.limits.max_tasks,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": profile.limits.max_task_chars,
            },
        },
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["tasks", "reason"],
    "additionalProperties": False,
}
```

Snapshot a distinct `ToolDefinition` per profile. Preview never exposes task text. Execute delegates
to the supervisor, serializes ASCII canonical JSON, checks UTF-8 byte length, and returns static
errors. Re-raise cancellation.

- [x] **Step 4: Export the stable M6a API**

Export profile/limits/status/results/evidence/errors, factory protocols, event models/sinks,
supervisor, Tool, and builder. Do not export internal prepared-child or transcript walkers.

- [x] **Step 5: Verify Tool, Registry, and Policy paths**

```powershell
py -m uv run pytest tests/unit/subagents/test_tools.py tests/unit/tools/test_registry.py tests/unit/policy -q
py -m uv run ruff check src/mini_code_agent/subagents tests/unit/subagents
py -m uv run pyright src/mini_code_agent/subagents tests/unit/subagents
```

- [x] **Step 6: Commit the parent adapter**

```powershell
git add src/mini_code_agent/subagents tests/unit/subagents/test_tools.py
git commit -m "feat: expose governed analysis subagents"
```

## Task 6: Prove the real parent/child Agent path

**Files:**
- Create: `tests/integration/test_governed_subagent_agent.py`

- [x] **Step 1: Build real governed child Tools**

Create a test `SubagentToolFactory` that returns a new `GovernedToolExecutor` per child:

```python
def create(
    self,
    profile: SubagentProfile,
    workspace_root: Path,
) -> ToolExecutor:
    workspace = WorkspaceBoundary(workspace_root)
    registry = ToolRegistry((ReadFileTool(workspace), SearchTextTool(workspace)))
    return GovernedToolExecutor(
        registry,
        policy=PolicyEngine(),
        approval=StaticApprovalHandler(approved=False),
        session_mode=SessionMode.NON_INTERACTIVE,
        trust_source=TrustSource.SUBAGENT,
    )
```

Assert exact profile names equal the produced definitions.

- [x] **Step 2: Write the parent Agent integration**

Use a parent `ScriptedProvider` that calls `delegate_analysis` with two tasks, then stops. Use a
factory that returns two child scripted Providers; each child calls a real read-only Tool and then
returns a summary.

Assert:

- parent completes with one parent ToolCall;
- two child run IDs and fresh one-message contexts;
- real child read/search evidence hashes exist;
- no child task text appears in events;
- parent receives one ordered batch JSON result;
- child Tool trust source is `SUBAGENT`;
- parent and child Workspaces remain byte-identical.

- [x] **Step 3: Add deny, timeout, and non-recursion integration cases**

Prove:

- parent Policy deny prevents every provider-factory call;
- a child requesting `delegate_analysis` receives `unknown_tool`, then can return a final summary;
- one timed-out child does not stop its sibling;
- parent task cancellation cancels both children.

- [x] **Step 4: Run integration and leak assertions**

```powershell
py -m uv run pytest tests/integration/test_governed_subagent_agent.py -q
py -m uv run pytest tests/unit/subagents tests/integration/test_governed_subagent_agent.py -q
rg -n "task text|system_prompt|arguments|ToolResult content|exception" src/mini_code_agent/subagents/events.py
```

Expected: all tests pass; the event scan shows no payload fields.

- [x] **Step 5: Commit integration evidence**

```powershell
git add tests/integration/test_governed_subagent_agent.py
git commit -m "test: prove governed subagent delegation"
```

## Task 7: Security review and hardening

**Files:**
- Modify only files implicated by a failing regression test.

- [x] **Step 1: Run full branch coverage**

Run on Python 3.12 and 3.13:

```powershell
py -m uv sync --python 3.12 --locked --all-groups
py -m uv run --no-sync pytest --cov -q
py -m uv sync --python 3.13 --locked --all-groups
py -m uv run --no-sync pytest --cov -q
```

Record exact pass/skip counts and branch coverage.

- [x] **Step 2: Run static, security, and dependency gates**

```powershell
py -m uv run --no-sync ruff format --check .
py -m uv run --no-sync ruff check .
py -m uv run --no-sync pyright
py -m uv tool run --python 3.13 bandit -q -r src
py -m uv export --locked --no-dev --no-emit-project --format requirements.txt -o build/runtime-requirements.txt
py -m uv tool run --python 3.13 pip-audit -r build/runtime-requirements.txt
```

- [x] **Step 3: Inspect trust and concurrency boundaries**

```powershell
git diff main...HEAD -- src/mini_code_agent/subagents src/mini_code_agent/policy/models.py
rg -n "create_task|TaskGroup|CancelledError|except Exception|SUBAGENT|system_prompt|messages|content" src/mini_code_agent/subagents
rg -n "shell=True|subprocess|threading|daemon|worktree|write_file|edit_file|run_command|mcp" src/mini_code_agent/subagents
```

Confirm:

- every created asyncio task belongs to one TaskGroup;
- `CancelledError` is never swallowed;
- no child receives parent messages;
- no write/execute/network child Tool can pass M6a composition;
- no raw task, prompt, argument, result, or exception enters events/evidence;
- no recursive local Tool is admitted.

- [x] **Step 4: Fix every issue with red-green regression**

For each issue, add the smallest failing test, run it to observe red, apply the focused fix, rerun
the focused and full Subagent suites, and commit:

```powershell
git commit -m "fix: harden bounded subagent boundary"
```

If no issue is found, do not create an empty commit.

## Task 8: Document, teach, and prepare `0.15.0-alpha.0`

**Files:**
- Create: `docs/architecture/governed-subagents.md`
- Create: `docs/adr/0014-bounded-host-profiled-subagents.md`
- Modify: `docs/architecture/threat-model.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: release-version tests and `tests/smoke_test.py`

- [x] **Step 1: Write architecture, ADR, and threat model**

Document fresh child context, exact host profile, no recursion, SUBAGENT provenance, TaskGroup
lifetime, per-child/outer timeout, cancellation, evidence hashes, event omissions, and why
in-process children are not an OS sandbox.

- [x] **Step 2: Add L11 prerequisites and exercises**

Teach:

- coroutine/Task/TaskGroup versus Java Thread/ExecutorService/StructuredTaskScope;
- fan-out/fan-in and ordered aggregation;
- cancellation propagation and why `CancelledError` is re-raised;
- capability profiles versus RBAC/service accounts;
- fresh context versus forked context;
- child evidence hashes versus natural-language claims;
- why background `ASK` auto-denies.

Add at least eight code-reading exercises tied to exact M6a modules.

- [x] **Step 3: Update resume material**

For the Subagent highlight, include why it is needed, exact technology, function, optimization,
problem solved, measurable evidence, and non-claims. Do not claim token savings without a benchmark.

- [x] **Step 4: Bump and validate release contract**

Set package version to `0.15.0a0`, lock it, update all exact version tests, and import stable
Subagent API from installed-package smoke.

Run:

```powershell
py -m uv lock --check
py -m uv run pytest tests/unit/test_package.py tests/cli/test_cli.py tests/unit/tools/test_runtime_info.py tests/smoke_test.py -q
py -m uv run ruff format --check .
py -m uv run ruff check .
py -m uv run pyright
git diff --check
```

- [x] **Step 5: Commit documentation and release preparation**

```powershell
git add docs README.md SECURITY.md CHANGELOG.md pyproject.toml uv.lock tests
git commit -m "docs: prepare 0.15 subagent alpha"
```

## Task 9: Build, smoke, publish, and record evidence

**Files:**
- Modify after verified release: `CHANGELOG.md`
- Modify after verified release: `docs/learning/progress.md`
- Modify after verified release: `docs/resume/project-profile.md`

- [ ] **Step 1: Re-run final local gates**

Run Python 3.12/3.13 full coverage plus Ruff, strict Pyright, Bandit, and locked dependency audit.
Record exact counts and platform skips.

- [ ] **Step 2: Build byte-reproducible artifacts**

Resolve and verify cleanup targets, set `SOURCE_DATE_EPOCH=1580601600`, build twice with hashed build
constraints, run `tests/artifact_test.py`, and require identical SHA-256 for wheel and sdist.

- [ ] **Step 3: Smoke wheel and sdist on Python 3.12/3.13**

In four isolated environments with no source-tree `PYTHONPATH`:

- import the stable Subagent API;
- run the console version command;
- run the installed-package smoke;
- execute the governed parent/child integration's successful delegation and cancellation-safe close
  paths.

- [ ] **Step 4: Push and create PR**

```powershell
git push -u origin codex/m6a-bounded-subagents
gh pr create --base main --head codex/m6a-bounded-subagents --title "feat: add bounded analysis subagents" --body-file build/pr-body.md
gh pr checks --watch
```

Require quality plus Ubuntu/Windows on Python 3.12/3.13.

- [ ] **Step 5: Merge and publish**

After green PR checks:

```powershell
gh pr merge --merge --delete-branch
git switch main
git pull --ff-only
git tag -a v0.15.0-alpha.0 -m "v0.15.0-alpha.0"
git push origin v0.15.0-alpha.0
gh release create v0.15.0-alpha.0 dist\mini_code_agent-0.15.0a0-py3-none-any.whl dist\mini_code_agent-0.15.0a0.tar.gz --prerelease --verify-tag --title "v0.15.0-alpha.0" --notes-file build/release-notes.md
```

- [ ] **Step 6: Verify and record remote evidence**

Verify tag commit, non-draft prerelease, exact remote asset names/sizes/digests, merged-main CI, and
clean tracking main. Record local/CI counts, run IDs, release URL, hashes, and skips; commit and push:

```powershell
git add CHANGELOG.md docs/learning/progress.md docs/resume/project-profile.md docs/superpowers/plans/2026-07-01-m6a-bounded-subagents.md
git commit -m "docs: record 0.15 release evidence"
git push origin main
```

## Plan Self-Review

- Spec coverage: Tasks 1-6 cover provenance, profiles, exact composition, fresh context, evidence,
  events, structured concurrency, Policy integration, non-recursion, and real parent/child Agents.
- Scope: this plan contains only M6a read-only analysis Subagents. Worktree creation, candidate
  storage, adoption, and discard remain M6b.
- Type consistency: `SubagentProfile` owns `AgentLimits` and `SubagentLimits`; factories create
  exact child dependencies; the supervisor returns `SubagentBatchResult`; the parent Tool only
  validates/serializes that result.
- Security consistency: parent delegation uses MODEL provenance; child Tools use SUBAGENT;
  non-interactive child ASK never prompts; cancellation always propagates.
- Placeholder scan: every code change, test command, expected red/green result, commit, and release
  action is explicit.
