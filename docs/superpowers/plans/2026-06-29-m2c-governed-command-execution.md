# M2c Governed Command Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an argv-only, policy-governed local command tool with workspace cwd validation,
minimal environment inheritance, bounded output, timeout/cancellation cleanup, and explicit
non-sandbox security documentation.

**Architecture:** `RunCommandTool` validates model arguments and produces a critical-risk preview.
After `GovernedToolExecutor` grants an explicit execute rule, `CommandRunner` starts one process
group, drains bounded stdout/stderr concurrently, and owns timeout, output-limit, and cancellation
cleanup. `WorkspaceBoundary` remains the only authority for model-supplied cwd paths.

**Tech Stack:** Python 3.12/3.13, `asyncio.create_subprocess_exec`, `subprocess`, POSIX process
groups, Windows `taskkill`, Pydantic v2, JSON Schema Draft 2020-12, Pytest, strict Pyright.

---

## Security Invariants

1. No code path uses `shell=True` or parses a shell command string.
2. Execute remains denied by default; usefulness requires an explicit Policy Rule.
3. An `ask` decision executes only in an interactive session after approval.
4. Cwd is the workspace root or an existing safe directory under it.
5. Model input cannot set environment variables or stdin.
6. API keys and arbitrary host environment variables are not inherited.
7. Stdout/stderr, runtime, argv size, and cleanup wait all have hard limits.
8. Timeout, output limit, and cancellation attempt process-tree cleanup.
9. Cancellation is re-raised only after cleanup.
10. Public errors omit absolute paths, environment, arguments, and raw exceptions.
11. Non-zero exit is data; spawn/cleanup failures are tool errors.
12. Documentation never calls local execution a sandbox.

## File Map

- Modify `src/mini_code_agent/workspace/boundary.py`: public safe directory resolution.
- Create `src/mini_code_agent/command/models.py`: limits, request, and result DTOs.
- Create `src/mini_code_agent/command/environment.py`: minimal environment builder.
- Create `src/mini_code_agent/command/runner.py`: bounded subprocess lifecycle.
- Create `src/mini_code_agent/command/__init__.py`: stable exports.
- Modify `src/mini_code_agent/policy/models.py`: bounded argv in approval previews.
- Create `src/mini_code_agent/tools/run_command.py`: Tool contract and governed preview.
- Modify `src/mini_code_agent/tools/__init__.py`: lazy public tool export.
- Add tests under `tests/unit/command`, `tests/unit/tools`, and `tests/integration`.
- Update architecture, threat model, learning, resume, README, changelog, and release evidence.

## Task 1: Safe Workspace Cwd

- [ ] Add failing tests in `tests/unit/workspace/test_boundary.py` for root cwd, nested directory,
  missing directory, file-as-directory, traversal, `.git`, and symlink/junction rejection.
- [ ] Run:

```powershell
python -m uv run pytest tests/unit/workspace/test_boundary.py -q
```

Expected: failures because `resolve_directory` is not public.

- [ ] Add this contract to `WorkspaceBoundary`:

```python
def resolve_directory(self, untrusted_path: str | None = None) -> tuple[Path, str]:
    if untrusted_path is None or untrusted_path == ".":
        return self._root, "."
    resolved = self._resolve_directory(untrusted_path)
    return resolved, resolved.relative_to(self._root).as_posix()
```

The existing lexical and physical checks remain the only implementation path.

- [ ] Run focused tests, Ruff, and Pyright; commit:

```powershell
git commit -m "feat: resolve safe command directories"
```

## Task 2: Command Models and Minimal Environment

- [ ] Create failing `tests/unit/command/test_models.py` and
  `tests/unit/command/test_environment.py`.
- [ ] Prove limits reject zero/oversized output, timeout, argv count, and argument length.
- [ ] Prove the environment contains required platform launch keys but excludes
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_SECRET_ACCESS_KEY`, and arbitrary variables.
- [ ] Implement immutable DTOs:

```python
class CommandLimits(BaseModel):
    max_output_bytes: int = Field(default=1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    cleanup_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

class CommandRequest(BaseModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd: Path
    cwd_display: str
    timeout_seconds: int = Field(ge=1)

class CommandResult(BaseModel):
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_exceeded: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
```

- [ ] Implement `build_minimal_environment(source: Mapping[str, str]) -> dict[str, str]` with
  explicit POSIX and Windows allowlists, including case-insensitive Windows key lookup.
- [ ] Run focused tests and commit:

```powershell
git commit -m "feat: define bounded command contracts"
```

## Task 3: Normal Process Execution

- [ ] Write failing runner tests for successful stdout/stderr, Unicode replacement decoding,
  non-zero exit, cwd, stdin EOF, missing executable, and static error messages.
- [ ] Implement `CommandRunner.run(request) -> CommandResult` using:

```python
process = await asyncio.create_subprocess_exec(
    *request.argv,
    cwd=request.cwd,
    env=build_minimal_environment(os.environ),
    stdin=asyncio.subprocess.DEVNULL,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    start_new_session=(os.name != "nt"),
    creationflags=windows_creation_flags(),
)
```

- [ ] Wrap spawn failures in a typed `CommandError` with `command_not_found` or
  `command_start_failed`; never include `str(exc)`.
- [ ] Drain both streams concurrently and return exact output for normal bounded commands.
- [ ] Run focused tests and commit:

```powershell
git commit -m "feat: execute argv commands"
```

## Task 4: Output, Timeout, Cancellation, and Tree Cleanup

- [ ] Add failing tests for exact output limit, limit-plus-one, timeout, cancellation, and cleanup
  failure normalization.
- [ ] Add a platform-aware child/grandchild fixture that writes PIDs to a workspace file, then
  sleeps. After timeout/cancellation, poll boundedly and assert neither process remains where
  reliable PID inspection is available.
- [ ] Implement stream readers that retain at most the shared byte budget and signal overflow.
  The main waiter races process exit, overflow, and timeout.
- [ ] Implement `_terminate_tree`:

```python
if os.name == "nt":
    await asyncio.create_subprocess_exec(
        "taskkill", "/PID", str(process.pid), "/T", "/F",
        stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
else:
    os.killpg(process.pid, signal.SIGTERM)
    # bounded wait, then SIGKILL when still alive
```

- [ ] On `CancelledError`, shield bounded cleanup and then re-raise. On timeout/output overflow,
  return a structured result after cleanup.
- [ ] Run focused tests repeatedly to detect lifecycle flakes; commit:

```powershell
git commit -m "feat: bound command process lifecycle"
```

## Task 5: Governed `run_command` Tool

- [ ] Add failing `tests/unit/tools/test_run_command.py` for closed Schema, direct validation,
  preview, safe cwd, runner mapping, and error mapping.
- [ ] Extend `ActionPreview` with:

```python
type CommandArgument = Annotated[str, Field(min_length=1, max_length=4096)]
command: tuple[CommandArgument, ...] | None = Field(default=None, max_length=64)
```

- [ ] Implement `_RunCommandArguments` with argv, cwd `"."`, timeout 30, and reason. Validate the
  requested timeout against `CommandLimits.max_timeout_seconds`.
- [ ] Implement `RunCommandTool.preview` with `SideEffect.EXECUTE`, `RiskLevel.CRITICAL`, exact
  argv, relative cwd, and bounded reason.
- [ ] Implement `execute` by resolving cwd again and calling the injected `CommandRunner`.
- [ ] Add lazy export from `mini_code_agent.tools`; run focused tests and commit:

```powershell
git commit -m "feat: add governed command tool"
```

## Task 6: Policy and Agent Integration

- [ ] Add `tests/integration/test_governed_command_agent.py`.
- [ ] Prove default execute denial never starts a process.
- [ ] Prove an explicit `PolicyRule(decision=ASK, tool_glob="run_command")` plus approval executes.
- [ ] Prove approval denial and non-interactive `ask` execute nothing.
- [ ] Use `ScriptedProvider` for `run_command -> ToolResult -> final response` and verify
  correlation, exact preview argv, relative cwd, and structured output.
- [ ] Prove a plain registry containing `run_command` is rejected by `AgentRuntime`.
- [ ] Run integration plus full development suite; commit:

```powershell
git commit -m "test: verify governed command agent"
```

## Task 7: Documentation and `v0.6.0-alpha.0`

- [ ] Add `docs/architecture/governed-command-execution.md` and ADR 0005.
- [ ] Update threat model with host filesystem/network/process escape non-claims.
- [ ] Update learning notes with ProcessBuilder, cancellation, Flink timeout, backpressure, and
  process-group analogies.
- [ ] Update resume rows with why, implementation, function, solved problem, limits, and measured
  evidence. Keep sandbox and remote CI claims out.
- [ ] Update README and changelog; bump package/tests/lock to `0.6.0a0`.
- [ ] Run final gates:

```powershell
python -m uv lock --check
python -m uv run --isolated --python 3.12 --all-groups pytest --ignore=tests/smoke_test.py -q
python -m uv run --isolated --python 3.13 --all-groups pytest --ignore=tests/smoke_test.py --cov=mini_code_agent -q
python -m uv run --isolated --python 3.13 --all-groups pyright
python -m uv run --isolated --python 3.13 --with bandit bandit -q -r src
python -m uv run --isolated --python 3.13 --with pip-audit pip-audit
python -m uv build --build-constraint build-constraints.txt --require-hashes
```

- [ ] Smoke-test wheel and sdist on Python 3.12/3.13 with `--no-project`.
- [ ] Fast-forward merge to main, tag `v0.6.0-alpha.0`, and remove the owned worktree only after
  merged verification succeeds.
