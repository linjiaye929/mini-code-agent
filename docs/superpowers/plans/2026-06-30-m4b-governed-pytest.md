# M4b Governed Pytest Execution and Structured Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a policy-governed, host-configured Pytest tool that returns bounded structured test
diagnostics without exposing arbitrary command construction to the model.

**Architecture:** A new `mini_code_agent.testing` package owns immutable contracts, secure bounded
JUnit parsing, and a Pytest subprocess service. `RunTestsTool` validates model-selected targets
through `WorkspaceBoundary`, previews a fixed command, and delegates execution through the existing
Tool Registry and `GovernedToolExecutor`.

**Tech Stack:** Python 3.12/3.13, asyncio, Pydantic v2, stdlib `tempfile`, `defusedxml`, Pytest
built-in JUnit XML, existing argv-only `CommandRunner`, Pytest, Ruff, strict Pyright.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/mini_code_agent/testing/models.py` | Limits, host profile, statuses, counts, diagnostics, result |
| `src/mini_code_agent/testing/errors.py` | Stable report/parser error codes |
| `src/mini_code_agent/testing/junit.py` | Bounded secure JUnit read and parse |
| `src/mini_code_agent/testing/pytest_runner.py` | Fixed argv construction, subprocess classification, cleanup |
| `src/mini_code_agent/testing/__init__.py` | Public testing contracts |
| `src/mini_code_agent/command/runner.py` | Trusted fixed environment overrides for dedicated runners |
| `src/mini_code_agent/tools/run_tests.py` | Model-facing execute tool and action preview |
| `src/mini_code_agent/tools/__init__.py` | Lazy tool export |
| `tests/unit/testing/*` | Contract, parser, and runner tests |
| `tests/unit/tools/test_run_tests.py` | Tool schema, preview, target, and error tests |
| `tests/integration/test_governed_pytest_agent.py` | Policy, real Pytest, and AgentRuntime flow |
| `docs/architecture/governed-test-execution.md` | Runtime and threat-boundary explanation |
| `docs/adr/0010-fixed-pytest-junit-boundary.md` | Decision and alternatives |

### Task 1: Testing Contracts

**Files:**
- Create: `tests/unit/testing/test_pytest_models.py`
- Create: `src/mini_code_agent/testing/models.py`
- Create: `src/mini_code_agent/testing/errors.py`
- Create: `src/mini_code_agent/testing/__init__.py`

- [x] **Step 1: Write failing contract tests**

Cover immutable `PytestLimits`, absolute host executable validation, plugin module-name validation,
status enums, count consistency, diagnostic text limits, and N-1/N/N+1 bounds. The intended public
shape is:

```python
limits = PytestLimits(max_report_bytes=1024, max_cases=10, max_diagnostics=2)
profile = PytestProfile(
    python_executable=Path(sys.executable),
    timeout_seconds=30,
    max_failures=5,
    trusted_plugins=("pytest_asyncio.plugin",),
)
result = PytestRunResult(
    status=PytestExecutionStatus.FAILED,
    report_status=PytestReportStatus.COMPLETE,
    exit_code=1,
    duration_ms=20,
    stdout="",
    stderr="",
    timed_out=False,
    output_limit_exceeded=False,
    counts=PytestCounts(total=1, passed=0, failed=1, errors=0, skipped=0),
    diagnostics=(diagnostic,),
    diagnostics_truncated=False,
)
```

- [x] **Step 2: Verify RED**

Run:

```powershell
python -m uv run pytest tests/unit/testing/test_pytest_models.py -q
```

Expected: collection fails because `mini_code_agent.testing` does not exist.

- [x] **Step 3: Implement minimal immutable contracts**

Use `ConfigDict(extra="forbid", frozen=True)`, `StrEnum`, bounded Pydantic fields, and model
validators. `PytestProfile.python_executable` must be absolute; trusted plugins must match
`^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$`.

- [x] **Step 4: Verify GREEN and commit**

```powershell
python -m uv run pytest tests/unit/testing/test_pytest_models.py -q
git add src/mini_code_agent/testing tests/unit/testing/test_pytest_models.py
git commit -m "feat: define pytest execution contracts"
```

### Task 2: Bounded JUnit Parser

**Files:**
- Create: `tests/unit/testing/test_junit.py`
- Create: `src/mini_code_agent/testing/junit.py`

- [x] **Step 1: Write the first failing parser test**

Write a UTF-8 JUnit document containing pass, failure, error, and skipped cases. Assert computed
counts and typed diagnostics rather than aggregate XML attributes:

```python
parsed = parse_junit_report(report_path, limits)
assert parsed.counts == PytestCounts(total=4, passed=1, failed=1, errors=1, skipped=1)
assert [item.outcome for item in parsed.diagnostics] == [
    PytestDiagnosticOutcome.FAILURE,
    PytestDiagnosticOutcome.ERROR,
]
```

- [x] **Step 2: Verify RED**

```powershell
python -m uv run pytest tests/unit/testing/test_junit.py -q
```

Expected: import failure for `parse_junit_report`.

- [x] **Step 3: Implement bounded read and minimal parse**

Read at most `max_report_bytes + 1`, require a regular file, decode strict UTF-8, reject case
insensitive `<!DOCTYPE` and `<!ENTITY`, parse only `testsuites`/`testsuite` roots, and compute counts
from `testcase` children.

- [x] **Step 4: Verify GREEN**

```powershell
python -m uv run pytest tests/unit/testing/test_junit.py -q
```

- [x] **Step 5: Add failing adversarial parser tests**

Add one focused test for each of: missing file, report size N-1/N/N+1, invalid UTF-8, malformed XML,
DTD, entity declaration, unknown root, missing case name, contradictory outcome children,
`max_cases + 1`, diagnostic truncation, and message/detail truncation.

- [x] **Step 6: Implement stable report statuses and limits**

Return an internal parsed-report value on success. Raise only typed `PytestReportError` values with
codes `missing`, `invalid`, `unsafe`, and `too_large`; never include input text or paths in public
messages.

- [x] **Step 7: Verify and commit**

```powershell
python -m uv run pytest tests/unit/testing/test_junit.py -q
git add src/mini_code_agent/testing/junit.py src/mini_code_agent/testing/errors.py tests/unit/testing/test_junit.py
git commit -m "feat: parse bounded pytest junit reports"
```

### Task 3: Dedicated Pytest Runner

**Files:**
- Modify: `src/mini_code_agent/command/runner.py`
- Modify: `tests/unit/command/test_runner.py`
- Create: `tests/unit/testing/test_pytest_runner.py`
- Create: `src/mini_code_agent/testing/pytest_runner.py`

- [x] **Step 1: Test trusted environment overrides first**

Assert `CommandRunner(environment=..., environment_overrides=...)` preserves the minimal base,
adds only explicit fixed values, rejects invalid names/NUL/oversized values, and cannot be changed
through `CommandRequest`.

- [x] **Step 2: Verify RED**

```powershell
python -m uv run pytest tests/unit/command/test_runner.py -k environment_override -q
```

Expected: constructor rejects the unknown `environment_overrides` argument.

- [x] **Step 3: Implement trusted environment overrides**

Validate at construction, merge after `build_minimal_environment`, and keep the resulting mapping
private. Do not add environment fields to model-controlled command requests.

- [x] **Step 4: Write failing runner command-shape and classification tests**

Inject a recording command runner and assert the request command starts with:

```python
(
    profile.python_executable.as_posix(),
    "-I",
    "-m",
    "pytest",
    "-q",
    "--disable-warnings",
    f"--maxfail={profile.max_failures}",
    f"--junitxml={managed_path}",
)
```

Assert trusted `-p` pairs occur before a final `--`, targets follow it unchanged, cwd is the
workspace root, and the preview uses `<managed-junit-report.xml>`.

- [x] **Step 5: Verify RED**

```powershell
python -m uv run pytest tests/unit/testing/test_pytest_runner.py -q
```

Expected: import failure for `PytestRunner`.

- [x] **Step 6: Implement execution and cleanup**

Build a dedicated `CommandRunner` with profile limits and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Create and close one random temporary XML file before launch;
wrap command execution and report parsing in `try/finally`; unlink only that exact path. Classify
timeout/output overflow before numeric exit codes.

- [x] **Step 7: Add RED tests for all result paths**

Cover exits 0-5 and unknown, timeout, output overflow, command errors, valid report, missing report,
invalid report, oversized report, cancellation, and temporary-path omission from serialized
results.

- [x] **Step 8: Implement result preservation**

Map parser errors to `PytestReportStatus` while preserving process status, exit, duration,
stdout/stderr, and flags. Report-derived fields are empty when report status is not complete.

- [x] **Step 9: Verify and commit**

```powershell
python -m uv run pytest tests/unit/command tests/unit/testing -q
git add src/mini_code_agent/command/runner.py src/mini_code_agent/testing tests/unit/command/test_runner.py tests/unit/testing
git commit -m "feat: execute fixed pytest profiles"
```

### Task 4: Governed `run_tests` Tool

**Files:**
- Create: `tests/unit/tools/test_run_tests.py`
- Create: `src/mini_code_agent/tools/run_tests.py`
- Modify: `src/mini_code_agent/tools/__init__.py`

- [x] **Step 1: Write failing tool schema and preview tests**

The model-facing call is exactly:

```python
ToolCall(
    id="tests-1",
    name="run_tests",
    arguments={
        "targets": ["tests/unit"],
        "reason": "Verify the changed unit behavior.",
    },
)
```

Assert a closed schema, optional `targets`, required `reason`, `SideEffect.EXECUTE`,
`RiskLevel.CRITICAL`, workspace-root cwd, validated resources, and fixed preview argv.

- [x] **Step 2: Verify RED**

```powershell
python -m uv run pytest tests/unit/tools/test_run_tests.py -q
```

Expected: import failure for `RunTestsTool`.

- [x] **Step 3: Implement target validation and tool result**

Resolve each target as an existing directory or regular file using `WorkspaceBoundary`; reject
duplicates, links, missing paths, traversal, node IDs, leading-dash path components, and target
overflow. Use host default targets only when the model omits targets. Serialize
`PytestRunResult.model_dump(mode="json")` as canonical compact JSON.

- [x] **Step 4: Add RED negative tests**

Cover direct execution bypassing registry validation, wrong tool name, invalid reason, file and
directory targets, `../`, `.git`, absolute paths, links/junctions, `file.py::test_name`, `-k`,
duplicates, and 31/32/33 targets.

- [x] **Step 5: Implement stable errors and lazy export**

Return static `invalid_arguments` or existing `WorkspaceErrorCode` values without leaking resolved
host paths. Add `RunTestsTool` to `tools.__all__` and lazy `__getattr__`.

- [x] **Step 6: Verify and commit**

```powershell
python -m uv run pytest tests/unit/tools/test_run_tests.py tests/unit/tools/test_registry.py -q
git add src/mini_code_agent/tools tests/unit/tools/test_run_tests.py
git commit -m "feat: add governed run tests tool"
```

### Task 5: Policy and Real Pytest Integration

**Files:**
- Create: `tests/integration/test_governed_pytest_agent.py`

- [x] **Step 1: Write policy RED tests**

Assert direct `ToolRegistry` execution can run only in the low-level contract test, while
`GovernedToolExecutor` defaults to deny. Add explicit `ASK` policy tests for approved, rejected,
approval failure, and non-interactive sessions.

- [x] **Step 2: Add deterministic real-project fixtures**

Create temporary synchronous tests with one passing case and one failing case. Run the real current
Python/Pytest executable through the governed tool and assert exit `1`, complete report, counts,
diagnostic test name, and no report path in the payload.

- [x] **Step 3: Add AgentRuntime integration**

Use `FakeProvider` to emit one `run_tests` call and then a final answer. Assert the structured tool
result reaches the second provider request and no stdout, traceback, or report payload is persisted
as a lifecycle Trace event.

- [x] **Step 4: Verify RED, implement only missing integration fixes, then GREEN**

```powershell
python -m uv run pytest tests/integration/test_governed_pytest_agent.py -q
```

Expected RED: the first missing policy/tool integration behavior fails. After minimal fixes,
expected GREEN: all integration cases pass.

- [x] **Step 5: Commit**

```powershell
git add tests/integration/test_governed_pytest_agent.py src
git commit -m "test: verify governed pytest agent flow"
```

### Task 6: Documentation, Version, and Release

**Files:**
- Create: `docs/architecture/governed-test-execution.md`
- Create: `docs/adr/0010-fixed-pytest-junit-boundary.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: version assertions under `tests/`

- [x] **Step 1: Write architecture, threat boundary, and ADR**

Document fixed versus model-controlled inputs, policy flow, report trust, status separation,
resource limits, plugin autoload behavior, cancellation cleanup, and the explicit no-sandbox
nonclaim.

- [x] **Step 2: Update learning and resume evidence**

Add a Java/Flink mapping for prepared command profiles, machine protocols, independent failure
domains, and bounded feedback. Add one resume highlight in the exact form: why used, technology,
implemented function, measured improvement, solved problem, limitations, and test evidence.

- [x] **Step 3: RED version test, then bump**

Change the expected version to `0.11.0a0`, verify failure, then update `pyproject.toml`, package
metadata, lockfile, README capability matrix, and Changelog.

- [x] **Step 4: Run focused and full local gates**

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

Build wheel and sdist with hashes, install each artifact separately on Python 3.12 and 3.13, run
`tests/smoke_test.py`, and verify `mini-code-agent --version` reports `0.11.0a0`.

- [x] **Step 6: Complete plan, commit, merge, tag, and publish**

Mark every completed checkbox, commit release evidence, fast-forward merge to `main`, rerun merged
verification, tag `v0.11.0-alpha.0`, push `main` and the tag, create a GitHub prerelease with wheel
and sdist assets, and verify the resulting GitHub Actions run succeeds.
