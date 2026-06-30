# M4b Governed Pytest Execution and Structured Diagnostics Design

**Date:** 2026-06-30  
**Target:** `v0.11.0-alpha.0`

## Scope

M4b adds one model-callable execution tool:

- `run_tests`: run a host-configured Pytest profile against optional, bounded workspace-relative
  targets and return typed execution status, counts, and failure diagnostics.

M4b does not add lint execution, automatic edits, retries, repair decisions, or a Repair Loop.
Those remain M4c. It also does not add an operating-system sandbox.

## Approaches Considered

### Fixed Pytest profile plus built-in JUnit XML

This is the selected approach. The host owns the Python executable, timeout, failure cap, default
targets, and trusted plugin list. The model can only choose existing workspace-relative files or
directories and provide a reason. Pytest's built-in JUnit output gives a stable structured boundary
without another project-side plugin.

### Parse terminal output

Rejected because Pytest's terminal format changes with verbosity, plugins, localization, and
traceback mode. Regex parsing would turn presentation text into an unstable protocol.

### Load a Mini Code Agent Pytest plugin

Rejected for M4b because project import paths can shadow plugin modules and plugin loading adds
another code-execution and compatibility boundary. A future isolated worker protocol may use a
plugin, but the first test runner should depend only on Pytest's built-in reporter.

## Invariants

1. `run_tests` is `SideEffect.EXECUTE` and `RiskLevel.CRITICAL`.
2. Execute remains denied by default. Interactive use requires an explicit matching policy rule
   and approval; non-interactive `ASK` fails closed.
3. The model cannot supply an executable, arbitrary argv, working directory, environment variable,
   timeout, max-failure count, report path, or plugin name.
4. Test targets are optional existing files or directories under `WorkspaceBoundary`. Paths cannot
   contain node IDs or option syntax, traverse links, or escape the workspace.
5. The command uses argv only, runs at the workspace root, and places `--` before every target.
6. The dedicated runner uses a minimal inherited environment and forces
   `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Only host-configured trusted plugins may be added with fixed
   `-p` arguments.
7. Time, combined output bytes, report bytes, test-case count, diagnostic count, and text fields
   are bounded.
8. The JUnit report is untrusted. It is read as bounded bytes, must be strict UTF-8, rejects DTD and
   entity declarations, and is parsed only after these checks.
9. Report corruption does not erase the process result. The response preserves execution status
   and marks diagnostics as missing, invalid, unsafe, or too large.
10. Temporary report paths are host-created, omitted from tool results, and cleaned on success,
    failure, timeout, output overflow, parser failure, and cancellation.
11. Test stdout, stderr, traceback details, and source paths can contain repository secrets. They
    are returned to the current model call but are not written to bounded lifecycle Trace events.
12. Running tests executes arbitrary repository code with the Mini Code Agent process's OS
    identity. Approval and resource limits reduce accidental execution and resource abuse; they do
    not provide filesystem, process, credential, or network isolation.

## Host Profile

`PytestProfile` is immutable and contains:

- absolute Python executable path, defaulting to `sys.executable`;
- default workspace-relative targets, defaulting to project discovery;
- command timeout;
- `--maxfail` value;
- trusted plugin module names selected by the host.

`PytestLimits` bounds:

- 300-second timeout, configurable up to 3,600 seconds;
- 1 MiB combined stdout/stderr;
- 2 MiB JUnit report;
- 10,000 test cases;
- 100 returned diagnostics;
- 32 model-selected targets;
- 4,096 diagnostic message characters;
- 16,384 diagnostic detail characters.

The fixed command shape is:

```text
<host-python> -I -m pytest
  -q --disable-warnings --maxfail=<host-value>
  --junitxml=<host-temporary-file>
  [-p <host-trusted-plugin>]...
  -- [validated-target]...
```

`-I` prevents user-site and `PYTHON*` environment influence on interpreter startup.
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` prevents ambient entry-point plugins from silently expanding the
execution surface. Project `conftest.py`, tests, and explicitly trusted plugins still execute.

## Result Contracts

`PytestExecutionStatus` classifies:

- exit `0`: `passed`;
- exit `1`: `failed`;
- exit `2`: `interrupted`;
- exit `3`: `internal_error`;
- exit `4`: `usage_error`;
- exit `5`: `no_tests`;
- timeout: `timed_out`;
- combined output overflow: `output_limit_exceeded`;
- any other exit: `unknown_exit`.

`PytestReportStatus` is independent:

- `complete`;
- `missing`;
- `invalid`;
- `unsafe`;
- `too_large`.

`PytestRunResult` contains execution status, report status, exit code, duration, bounded stdout and
stderr, timeout/output flags, computed counts, bounded diagnostics, and a
`diagnostics_truncated` flag. It intentionally omits the temporary report path and actual command
argv.

Each `PytestDiagnostic` contains outcome (`failure` or `error`), test name, optional class/file/line,
bounded message, and bounded details. Counts are computed from `<testcase>` elements rather than
trusted aggregate attributes. A case with contradictory outcome elements invalidates the report.

## Data Flow

```text
ToolCall
  -> Tool Registry JSON Schema validation
  -> run_tests target validation
  -> ActionPreview with fixed command shape
  -> PolicyEngine
  -> independent approval
  -> host creates temporary report
  -> CommandRunner executes argv-only Pytest
  -> bounded stdout/stderr + process classification
  -> bounded untrusted JUnit read
  -> secure structural parse + typed diagnostics
  -> temporary report cleanup
  -> canonical ToolResult JSON
```

Preview uses the literal marker `<managed-junit-report.xml>` because the random report path is
allocated only after approval. Every executable, option, plugin, target, and working directory
shown in the preview otherwise matches execution. The policy engine currently matches the first
command argument, so the host-only report path does not weaken a policy predicate.

## Error Handling

- Invalid arguments and unsafe targets return stable tool errors without starting a process.
- Missing executable, startup failure, process I/O failure, and cleanup failure map existing
  `CommandErrorCode` values to static public errors.
- Pytest non-zero exits are normal typed results, not tool transport failures.
- Missing or bad JUnit reports preserve stdout/stderr and exit classification while setting the
  report status and zeroing report-derived counts.
- Cancellation propagates after process-tree cleanup and temporary-file cleanup.
- No parser exception, raw OS exception, report path, or command path is exposed in a public error.

## Tests

- immutable profile/limits/result models and N-1/N/N+1 boundaries;
- JUnit pass/fail/error/skip counts and bounded failure details;
- malformed XML, non-UTF-8, DTD/entity declarations, duplicate outcome nodes, report byte overflow,
  case overflow, and diagnostic truncation;
- exact fixed argv, target ordering, `--`, `-I`, trusted plugins, minimal environment override, and
  temporary-file cleanup;
- every Pytest exit status, timeout, output overflow, missing report, and unknown exit;
- files/directories, traversal, links, missing paths, node IDs, leading-dash paths, duplicates, and
  target count limits;
- policy default deny, interactive approval, rejection, and non-interactive fail-closed behavior;
- real subprocess integration against deterministic passing and failing sample projects;
- AgentRuntime integration proving structured diagnostics return to the model;
- regression checks for command, workspace, policy, Git, checkpoint, and Trace boundaries.

## Learning Mapping

- A fixed Pytest profile is comparable to a server-owned prepared statement: the caller supplies
  bounded values, not executable structure.
- JUnit XML is a versioned machine interface; terminal output is an operator-facing log.
- Execution status and report status are separate failure domains, like Flink job state versus
  checkpoint metadata state.
- `--maxfail`, timeout, output bytes, report bytes, and diagnostic caps form a multi-dimensional
  resource budget.
- Approval is authorization, not isolation. It answers whether an action may run, not what the
  resulting process can access.

