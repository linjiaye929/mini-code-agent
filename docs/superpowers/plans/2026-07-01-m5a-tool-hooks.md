# M5a Governed Tool Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [x]`) syntax for tracking.

**Goal:** Add deterministic host-registered pre/post Tool Hooks that can block actions or observe
results without granting permissions, replacing results, or destabilizing the core Agent loop.

**Architecture:** Add immutable Hook contexts and audit records, a sequential timeout-bounded Hook
runner, and one optional integration point in `GovernedToolExecutor`. Pre-Hook failures are
fail-closed; post-Hook failures are isolated after execution.

**Tech Stack:** Python 3.12/3.13, asyncio, Pydantic v2, structural Protocols, monotonic timing,
existing Tool/Policy contracts, Pytest, pytest-asyncio, Ruff, strict Pyright.

---

## File Map

**Create**

- `src/mini_code_agent/hooks/__init__.py`: stable public Hook API.
- `src/mini_code_agent/hooks/models.py`: sources, phases, contexts, decisions, outcomes, and audit.
- `src/mini_code_agent/hooks/runner.py`: registration validation, ordering, timeout, and isolation.
- `tests/unit/hooks/test_hook_models.py`: model bounds and secret-free audit tests.
- `tests/unit/hooks/test_hook_runner.py`: ordering, failure, timeout, audit, and cancellation tests.
- `tests/integration/test_governed_tool_hooks_agent.py`: real Agent/Policy/Tool Hook behavior.
- `docs/architecture/governed-extensions.md`: Skills/Hooks composition and threat boundary.
- `docs/adr/0012-inert-skills-host-hooks.md`: extension trust decision.

**Modify**

- `src/mini_code_agent/policy/executor.py`: optional pre/post Hook invocation.
- `tests/unit/policy/test_executor.py`: exact lifecycle and monotonic authorization tests.
- `tests/smoke_test.py`: import stable Hook API.
- `docs/learning/knowledge-map.md`: L9 prerequisites, implementation notes, mappings, exercises.
- `docs/learning/progress.md`: M5a verification and release evidence.
- `docs/resume/project-profile.md`: Skills/Hooks resume highlight and evidence.
- `README.md`: bounded Skills/Hooks capability and non-claims.
- `SECURITY.md`: untrusted Skill and trusted in-process Hook boundary.
- `CHANGELOG.md`: v0.13 alpha changes.
- `pyproject.toml`: bump to `0.13.0a0` at release preparation.
- `uv.lock`: record project version metadata.

### Task 1: Define Hook contracts and audit records

**Files:**
- Create: `src/mini_code_agent/hooks/models.py`
- Create: `tests/unit/hooks/test_hook_models.py`

- [x] **Step 1: Write failing model tests**

Cover ID/source/priority bounds, immutable Tool contexts, valid decisions, elapsed limits, and
audit serialization that omits arguments, results, previews, and exception text:

```python
def test_audit_record_contains_only_bounded_metadata() -> None:
    record = HookAuditRecord(
        hook_id="protect-main",
        source=HookSource.MANAGED,
        phase=HookPhase.PRE_TOOL,
        outcome=HookOutcome.CONTINUED,
        tool_call_id="call-1",
        tool_name="write_file",
        elapsed_ms=3,
    )
    payload = record.model_dump(mode="json")
    assert "arguments" not in payload
    assert "result" not in payload
```

- [x] **Step 2: Run tests and verify collection fails**

Run: `uv run pytest tests/unit/hooks/test_hook_models.py -q`

Expected: FAIL because `mini_code_agent.hooks.models` does not exist.

- [x] **Step 3: Implement immutable models**

Define:

```python
class HookPhase(StrEnum):
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


class HookDecision(StrEnum):
    CONTINUE = "continue"
    BLOCK = "block"


class HookOutcome(StrEnum):
    CONTINUED = "continued"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class PreToolHookResult(BaseModel):
    decision: HookDecision
    public_reason: str = Field(default="Hook allowed the action.", min_length=1, max_length=500)
```

Add frozen `ToolHookContext`, `PostToolHookContext`, and `HookAuditRecord`. Contexts hold existing
frozen domain models and are never serialized to the model.

- [x] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/hooks/test_hook_models.py -q`

Expected: PASS.

- [x] **Step 5: Commit contracts**

```powershell
git add src/mini_code_agent/hooks/models.py tests/unit/hooks/test_hook_models.py
git commit -m "feat: define governed hook contracts"
```

### Task 2: Run Hooks deterministically with bounded failures

**Files:**
- Create: `src/mini_code_agent/hooks/runner.py`
- Create: `tests/unit/hooks/test_hook_runner.py`

- [x] **Step 1: Write failing runner tests**

Test registration bounds, duplicate IDs across phases, `(priority, id)` order, continue chain,
first block, exception, timeout, malformed result, audit failure, post failure continuation, and
cancellation propagation:

```python
@pytest.mark.asyncio
async def test_pre_hooks_run_by_priority_then_id_and_stop_on_block() -> None:
    calls: list[str] = []
    runner = ToolHookRunner(
        (
            pre_registration("z-last", priority=10, calls=calls),
            pre_registration("b-block", priority=0, calls=calls, block=True),
            pre_registration("a-first", priority=0, calls=calls),
        )
    )
    result = await runner.before_tool(context())
    assert calls == ["a-first", "b-block"]
    assert result.allowed is False
```

- [x] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/hooks/test_hook_runner.py -q`

Expected: FAIL because the runner is absent.

- [x] **Step 3: Implement protocols, registrations, and sinks**

Implement:

```python
class PreToolHook(Protocol):
    async def before_tool(self, context: ToolHookContext) -> PreToolHookResult: ...


class PostToolHook(Protocol):
    async def after_tool(self, context: PostToolHookContext) -> None: ...


@dataclass(frozen=True, slots=True)
class HookRegistration:
    hook_id: str
    source: HookSource
    priority: int
    phase: HookPhase
    handler: PreToolHook | PostToolHook


class ToolHookRunner:
    async def before_tool(self, context: ToolHookContext) -> HookGateResult: ...
    async def after_tool(self, context: PostToolHookContext) -> None: ...
```

Validate IDs with the same lowercase identifier policy used by other extension contracts. Limit
registrations to 64 and per-Hook timeout to `0.01..30` seconds. Use `time.monotonic_ns` injected
for deterministic elapsed tests. Add `NullHookAuditSink` and `RecordingHookAuditSink`.

- [x] **Step 4: Enforce phase-specific failure behavior**

For pre-Hooks, timeout/exception/malformed result/audit failure returns a static blocked
`HookGateResult`; cancellation propagates. For post-Hooks, record failure when possible, continue
to later Hooks, never alter the Tool result, and propagate cancellation.

- [x] **Step 5: Run runner and static tests**

Run:

```powershell
uv run pytest tests/unit/hooks/test_hook_runner.py -q
uv run pyright src/mini_code_agent/hooks/runner.py tests/unit/hooks/test_hook_runner.py
```

Expected: PASS.

- [x] **Step 6: Commit the runner**

```powershell
git add src/mini_code_agent/hooks/runner.py tests/unit/hooks/test_hook_runner.py
git commit -m "feat: run bounded tool hooks"
```

### Task 3: Integrate Hooks with governed Tool execution

**Files:**
- Modify: `src/mini_code_agent/policy/executor.py`
- Modify: `tests/unit/policy/test_executor.py`

- [x] **Step 1: Write failing executor lifecycle tests**

Add recording ActionGuard, Hooks, Policy, approval, and Tool dependencies. Assert exact order and
negative authority:

```python
@pytest.mark.asyncio
async def test_hook_continue_cannot_bypass_policy_deny() -> None:
    tool = RecordingTool(name="write_file", side_effect=SideEffect.WRITE)
    hooks = RecordingHookRunner(allowed=True)
    executor = executor_for(tool, policy=deny_write_policy(), hooks=hooks)

    result = await executor.execute(call())

    assert error_code(result) == "permission_denied"
    assert hooks.post_contexts == []
    assert tool.calls == []
```

Also test block-before-approval, pre failure, post observation of success/error, unchanged result
identity, and cancellation.

- [x] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/unit/policy/test_executor.py -q -k hook`

Expected: FAIL because `GovernedToolExecutor` has no Hook boundary.

- [x] **Step 3: Add the optional Hook runner**

Accept `hooks: ToolHookRunner | None = None`. After ActionGuard success, construct
`ToolHookContext` from the validated call, definition, preview, session mode, and trust source.
Block with the existing generic permission result when `before_tool` does not allow progress.

After `registry.execute`, pass the original result to `PostToolHookContext`, await `after_tool`,
then return the same result object. Do not invoke post-Hooks for validation, preview, guard,
pre-Hook, Policy, or approval denials.

- [x] **Step 4: Run executor regression**

Run:

```powershell
uv run pytest tests/unit/policy/test_executor.py -q
uv run pyright src/mini_code_agent/policy/executor.py tests/unit/policy/test_executor.py
```

Expected: all old and new tests pass.

- [x] **Step 5: Commit integration**

```powershell
git add src/mini_code_agent/policy/executor.py tests/unit/policy/test_executor.py
git commit -m "feat: enforce governed tool hooks"
```

### Task 4: Prove Agent lifecycle behavior

**Files:**
- Create: `tests/integration/test_governed_tool_hooks_agent.py`
- Create: `src/mini_code_agent/hooks/__init__.py`
- Modify: `tests/smoke_test.py`

- [x] **Step 1: Write end-to-end tests**

Compose `AgentRuntime`, `FakeProvider`, a real governed write/read Tool set, Policy, and Hooks:

- managed pre-Hook blocks a write before mutation;
- pre-Hook continue still requires Policy approval;
- post-Hook observes a permitted read result;
- a failing post-Hook does not change the Tool result or stop a later observer;
- stable public exports import from the package root.

- [x] **Step 2: Run integration and smoke tests**

Run:

```powershell
uv run pytest tests/integration/test_governed_tool_hooks_agent.py tests/smoke_test.py -q
```

Expected: PASS.

- [x] **Step 3: Run combined Skills/Hooks regression**

Run:

```powershell
uv run pytest tests/unit/skills tests/unit/hooks tests/unit/policy/test_executor.py tests/integration/test_governed_skills_agent.py tests/integration/test_governed_tool_hooks_agent.py -q
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
```

Expected: all pass.

- [x] **Step 4: Commit integration evidence**

```powershell
git add src/mini_code_agent/hooks/__init__.py tests/integration/test_governed_tool_hooks_agent.py tests/smoke_test.py
git commit -m "test: prove governed hook lifecycle"
```

### Task 5: Document architecture and learning material

**Files:**
- Create: `docs/architecture/governed-extensions.md`
- Create: `docs/adr/0012-inert-skills-host-hooks.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `README.md`
- Modify: `SECURITY.md`

- [x] **Step 1: Write architecture and ADR**

Document the Skill discovery/load data flow, source qualification, TOCTOU revalidation, Hook
ordering, monotonic authorization, failure table, composition example, and every M5a non-claim.
The ADR must record why arbitrary command/dynamic-import Hooks are deferred.

- [x] **Step 2: Expand L9 learning notes**

Add:

- prerequisite YAML/frontmatter, filesystem identity, SHA-256, TOCTOU, async Protocol, timeout,
  cancellation, and monotonic authorization concepts;
- Java mappings to `ServiceLoader`, SPI descriptors, Servlet/Spring interceptors, and immutable
  DTOs;
- Flink mappings to operator lifecycle callbacks and why user code is still trusted executable
  code;
- code-reading order and at least six exercises tied to exact M5a modules.

- [x] **Step 3: Add the resume highlight**

Use the established structure:

```text
Why: repository extensions are untrusted and silent precedence or executable imports create
authority escalation.
Technology: strict PyYAML/Pydantic contracts, hardened pathlib/stat checks, SHA-256 drift
revalidation, source-qualified catalog, async typed Hooks, timeout and Policy composition.
Function: lazily lists/loads inert Skills and runs deterministic Tool lifecycle veto/observers.
Improvement: avoids unconditional context injection and preserves Policy as the sole authority
grant.
Problem solved: extension prompt injection cannot directly register capabilities or bypass deny.
Evidence: focused unit/integration tests and exact release run IDs.
```

- [x] **Step 4: Update README and security model**

Describe M5a as bounded alpha functionality. State explicitly that Skills are untrusted data,
in-process Hooks are host-trusted, and there is no safe execution of repository Hook code or OS
sandbox.

- [x] **Step 5: Verify docs and commit**

Run:

```powershell
rg -n "TBD|TODO|command hook is safe|sandboxed hook" docs README.md SECURITY.md
git diff --check
```

Expected: no placeholders or unsupported safety claims.

```powershell
git add docs README.md SECURITY.md
git commit -m "docs: explain governed extension boundaries"
```

### Task 6: Prepare v0.13 alpha

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `CHANGELOG.md`
- Modify: `docs/learning/progress.md`

- [x] **Step 1: Set package version**

Set `project.version = "0.13.0a0"` and run `uv lock`.

- [x] **Step 2: Add changelog and provisional progress evidence**

Record Skills/Hooks behavior, security boundaries, migration impact, test inventory, and pending
remote release evidence. Do not claim a GitHub run or artifact hash before it exists.

- [x] **Step 3: Run all local release gates on Python 3.12**

Run:

```powershell
uv run --python 3.12 ruff format --check src tests
uv run --python 3.12 ruff check src tests
uv run --python 3.12 pyright
uv run --python 3.12 pytest --cov=mini_code_agent --cov-branch --cov-report=term-missing
uv run --python 3.12 bandit -q -r src
uv export --locked --no-dev --format requirements.txt -o build/runtime-requirements.txt
uv run --python 3.12 pip-audit -r build/runtime-requirements.txt
uv build
```

Expected: all pass, coverage at least 85%, wheel and sdist built.

- [x] **Step 4: Run the full suite on Python 3.13**

Run:

```powershell
uv run --python 3.13 pytest --cov=mini_code_agent --cov-branch --cov-report=term
```

Expected: all tests pass and coverage remains at least 85%.

- [x] **Step 5: Smoke-test installed wheel and sdist**

Create isolated temporary Python 3.12 and 3.13 environments. Install each artifact without the
source tree on `sys.path`, import `SkillCatalog` and `ToolHookRunner`, and run
`mini-code-agent --help`.

Expected: all four artifact/interpreter combinations pass.

- [x] **Step 6: Commit release preparation**

```powershell
git add pyproject.toml uv.lock CHANGELOG.md docs/learning/progress.md
git commit -m "docs: prepare 0.13 alpha release"
```

### Task 7: Review, merge, publish, and record evidence

**Files:**
- Review all M5a changes.
- Modify after release: `docs/learning/progress.md`
- Modify after release when needed: `docs/resume/project-profile.md`

- [x] **Step 1: Run final diff and security review**

Verify no Skill path/content leak, executable project extension, silent precedence, Hook
authorization grant, Hook result replacement, unbounded timeout/count, cancellation swallowing,
or unsupported sandbox claim.

- [x] **Step 2: Push the feature branch and require GitHub CI**

Push `codex/m5a-skills-hooks`, open a PR, and wait for every Windows/Linux Python 3.12/3.13,
security, and package job to pass.

- [x] **Step 3: Merge only the reviewed green commit**

Merge the PR, update local `main`, and verify the merge commit and remote branch state.

- [x] **Step 4: Tag and create the prerelease**

Create annotated tag `v0.13.0-alpha.0` on the reviewed release-code commit. Create a GitHub
prerelease and upload the exact locally verified wheel and sdist.

- [x] **Step 5: Verify remote release evidence**

Verify:

- tag target equals the intended code commit;
- release is a non-draft prerelease;
- remote asset names, sizes, and SHA-256 hashes equal local artifacts;
- final `main` GitHub Actions run passes all jobs.

- [x] **Step 6: Record immutable evidence**

Replace provisional progress entries with the commit SHA, PR URL, workflow run IDs, test counts,
coverage, tag, release URL, asset sizes, and SHA-256 values. Update resume evidence only with
facts supported by code and CI.

- [x] **Step 7: Commit and push evidence**

```powershell
git add docs/learning/progress.md docs/resume/project-profile.md
git commit -m "docs: record 0.13 release evidence"
git push origin main
```

- [x] **Step 8: Confirm final repository state**

Run:

```powershell
git status --short --branch
git log -5 --oneline
gh release view v0.13.0-alpha.0
```

Expected: clean `main` tracking `origin/main`, published prerelease, and recorded evidence.
