# Governed Test Execution

[English](governed-test-execution.md) | [简体中文](governed-test-execution.zh-CN.md)

## Purpose

M4b adds a narrow test-execution capability for Agent verification. It does not expose another
arbitrary command interface. The host selects the Python environment and Pytest profile; the model
can only request existing workspace-relative test files or directories and explain why the run is
needed.

The capability has four boundaries:

1. `RunTestsTool` validates model input and prepares an approval preview.
2. `GovernedToolExecutor` applies execute policy and independent approval.
3. `PytestRunner` builds and runs one fixed argv profile with process budgets.
4. The JUnit parser converts an untrusted bounded report into typed diagnostics.

## Composition

```python
import sys
from pathlib import Path

from mini_code_agent.policy import (
    GovernedToolExecutor,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    SessionMode,
    StaticApprovalHandler,
    TrustSource,
)
from mini_code_agent.testing import PytestProfile, PytestRunner
from mini_code_agent.tools import RunTestsTool, ToolRegistry
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.workspace import WorkspaceBoundary

root = Path.cwd()
workspace = WorkspaceBoundary(root)
runner = PytestRunner(
    root,
    profile=PytestProfile(
        python_executable=Path(sys.executable),
        default_targets=("tests",),
        trusted_plugins=("pytest_asyncio.plugin",),
    ),
)
registry = ToolRegistry([RunTestsTool(workspace, runner)])
executor = GovernedToolExecutor(
    registry,
    policy=PolicyEngine(
        [
            PolicyRule(
                id="ask-project-tests",
                decision=PolicyDecision.ASK,
                rationale="Tests execute repository code.",
                tool_glob="run_tests",
                side_effect=SideEffect.EXECUTE,
            )
        ]
    ),
    approval=StaticApprovalHandler(approved=True),
    session_mode=SessionMode.INTERACTIVE,
    trust_source=TrustSource.MODEL,
)
```

Use the active environment's `sys.executable` path without resolving symlinks. On POSIX, resolving
`.venv/bin/python` can erase virtual-environment identity and select the base interpreter. The path
is host configuration, never a ToolCall argument.

## Model and Host Control

| Value | Owner | Boundary |
|---|---|---|
| Python executable | Host | Absolute path in immutable `PytestProfile` |
| Default targets | Host | Revalidated through `WorkspaceBoundary` |
| Trusted Pytest plugins | Host | At most 10 import-module names, fixed `-p` arguments |
| Timeout and `--maxfail` | Host | Immutable profile plus hard `PytestLimits` |
| Test targets | Model | At most 32 existing workspace files/directories |
| Reason | Model | Required, bounded approval text |
| cwd | Harness | Always the resolved workspace root |
| JUnit path | Harness | Random host temporary file |
| Remaining argv and environment | Harness | Fixed, not model-controlled |

An omitted `targets` field selects host defaults. An explicit empty list is invalid. If host
defaults are empty, Pytest performs normal discovery at the workspace root.

## Fixed Command

The generated argv is equivalent to:

```text
<host-python> -I -B -m pytest
  -q --disable-warnings --maxfail=<host-value>
  -p no:cacheprovider
  [-p <host-trusted-plugin>]...
  --junitxml=<managed-temporary-path>
  -- [validated-target]...
```

- `create_subprocess_exec` preserves argv boundaries and never invokes a shell.
- `-I` ignores user-site and `PYTHON*` startup influence.
- `-B` prevents Python bytecode cache writes from making an otherwise read-only test run dirty.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` removes ambient entry-point plugins.
- `-p no:cacheprovider` prevents the harness from creating `.pytest_cache`.
- `--` terminates option parsing before model-selected targets.

Project tests and `conftest.py` still execute and can write files despite `-B`. Trusted plugins are
host configuration because many projects require plugins such as `pytest-asyncio`, but allowing
the model to name one would turn imports into an execution primitive.

## Target Validation

The tool accepts files and directories. Every target:

- uses POSIX-style workspace-relative syntax;
- rejects absolute, drive, UNC, ADS, `.git`, `%`, backslash, node-ID `::`, and leading-dash
  components;
- rejects links and junctions through `WorkspaceBoundary`;
- must exist as a directory or regular file;
- is normalized to a workspace-relative display path;
- is deduplicated by resolved platform path identity.

The same preparation runs during preview and execution. A target removed or replaced after
approval fails instead of silently changing the command.

## Process and Report Status

Process status and diagnostic report status are independent:

| Process status | Meaning |
|---|---|
| `passed` | Pytest exit 0 |
| `failed` | Pytest exit 1 |
| `interrupted` | Pytest exit 2 |
| `internal_error` | Pytest exit 3 |
| `usage_error` | Pytest exit 4 |
| `no_tests` | Pytest exit 5 |
| `timed_out` | Process exceeded host timeout |
| `output_limit_exceeded` | Combined stdout/stderr exceeded budget |
| `unknown_exit` | Any other or absent exit without a stronger classification |

| Report status | Meaning |
|---|---|
| `complete` | Bounded report parsed successfully |
| `missing` | No report remained |
| `invalid` | Encoding, XML, field, or outcome structure was invalid |
| `unsafe` | File type or DTD/entity declaration was rejected |
| `too_large` | Byte or test-case budget was exceeded |

A corrupt report does not erase the process exit, stdout, stderr, or duration. It produces empty
report-derived counts and diagnostics with a non-complete report status. Exact full-path,
POSIX-path, and random-filename echoes of the managed report are replaced with
`<managed-junit-report.xml>` before serialization.

## JUnit Trust Boundary

Pytest creates the JUnit file, but repository test code runs in the same process tree and can
tamper with it. The parser therefore treats the file as attacker-controlled:

1. open one exact host-created path without following links where the platform supports it;
2. require a regular file;
3. read at most `max_report_bytes + 1`;
4. decode strict UTF-8;
5. reject DTD and entity declarations;
6. parse only `testsuite` or `testsuites`;
7. compute counts from bounded `testcase` elements;
8. reject contradictory outcomes and invalid attributes;
9. truncate returned diagnostic count and text deterministically.

Aggregate XML attributes are ignored because they are redundant untrusted claims. Counts come from
the actual case elements accepted by the parser.

## Cleanup and Cancellation

The random report file is created and closed before process launch so Windows can reopen it.
`try/finally` removes that exact path after success, command failure, parser failure, timeout,
output overflow, or cancellation. Cleanup never recursively removes a path controlled by test
code.

`CommandRunner` owns process-tree cleanup. Cancellation propagates only after its shielded
cleanup attempt, and the report cleanup then runs in the outer `finally`.

Direct path replacement prevents ordinary Pytest output or `sys.argv` echoes from exposing the
random temporary name. Arbitrary test code can transform, encode, split, or otherwise exfiltrate
data; preventing that requires process isolation rather than output substitution.

## Security Boundary

Approval answers whether the action may run. It is not isolation.

Approved tests run arbitrary project code as the Mini Code Agent OS user and may:

- read or modify any path available to that user;
- launch child processes;
- access inherited permitted network resources;
- read credentials available outside the runner's stripped environment through other OS channels;
- modify the workspace despite the cache provider being disabled.

Timeout, output limits, plugin control, minimal environment, path validation, and process cleanup
reduce accidental execution and resource abuse. They do not replace a container, restricted OS
account, VM, seccomp/job-object policy, or network sandbox.

Test stdout, stderr, and diagnostics can contain source code or secrets. They are returned to the
active model exchange but excluded from lifecycle Trace events. Checkpoint payloads remain bounded
plaintext and can contain complete ToolResults.
