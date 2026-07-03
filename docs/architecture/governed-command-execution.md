# Governed Command Execution

[English](governed-command-execution.md) | [简体中文](governed-command-execution.zh-CN.md)

## Data Flow

```text
Model ToolCall
    |
    v
ToolRegistry
    |-- Draft 2020-12 argv/cwd/timeout/reason validation
    v
RunCommandTool.preview
    |-- WorkspaceBoundary resolves cwd
    |-- critical risk + exact argv + reason
    v
PolicyEngine
    |-- execute default: deny
    |-- optional executable_glob narrowing
    `-- explicit interactive ask/allow rule required
    v
CommandRunner
    |-- create_subprocess_exec (never shell=True)
    |-- stdin DEVNULL + minimal environment
    |-- concurrent bounded stdout/stderr
    `-- process-tree cleanup on limit/timeout/cancellation
    v
Correlated structured ToolResult
```

## Command Contract

`run_command` accepts one argv array, a workspace-relative cwd, a timeout from 1 to 300 seconds,
and a bounded reason. It does not accept shell text, stdin, environment overrides, background
mode, or detached execution.

The exact submitted argv appears in approval. `ActionPreview` classifies execution as critical.
Execute is denied by default; an application must install an explicit rule. Rules may narrow by
tool, risk, cwd resource, session/trust source, and `executable_glob`. The executable glob matches
the submitted first argv element, not a cryptographic executable identity.

Non-zero exit codes are normal command results. Spawn, output-I/O, and cleanup failures use
static error codes. Timeout and output-limit termination are result flags so the Agent can choose
a smaller command without parsing exception text.

## Limits

| Limit | Default | Hard maximum |
|---|---:|---:|
| Timeout requested by tool | 30 seconds | 300 seconds |
| Runner timeout policy | 300 seconds | 3,600 seconds |
| Combined retained stdout/stderr | 1 MiB | 8 MiB |
| Argv items | 64 | 64 |
| Characters per argument | 4,096 | 4,096 |
| Cleanup wait | 5 seconds | 10 seconds |

The stream readers continue draining and discarding after the retained-byte budget is exhausted.
This prevents pipe backpressure from deadlocking process termination without allowing memory to
grow with command output.

## Environment

The child inherits only a platform launch allowlist: PATH, temporary-directory, locale, home, and
required Windows system variables. API keys and arbitrary project variables are not inherited.
An explicit empty environment remains empty; `None` alone means derive the minimal environment
from the host.

Environment minimization reduces accidental secret inheritance. It does not prevent an approved
process from opening secret files available to the current OS user.

## Process Lifecycle

- POSIX starts a new session, sends SIGTERM to the process group, waits boundedly, then escalates
  to SIGKILL.
- Windows starts a new process group without a visible console and calls the absolute
  `%SystemRoot%\System32\taskkill.exe /T /F` path.
- Output overflow and timeout terminate the tree before returning.
- Cancellation shields bounded cleanup and then re-raises `CancelledError`.
- Unexpected pipe-reader failures terminate the tree and become `command_io_failed`.
- If tree cleanup fails, the runner still attempts to kill the root but returns
  `command_cleanup_failed`; partial cleanup is never reported as success.

Tests use a parent Python process that starts a heartbeat-writing grandchild. After runner return,
the heartbeat must stop changing. This verifies the lifecycle postcondition without relying on
fragile PID-reuse checks.

## Security Non-claims

This runner is not a sandbox. An approved process can read/write outside the workspace, access
the network, inspect host state, print sensitive file content, or deliberately detach descendants.
Process groups and `taskkill` are best-effort lifecycle controls, not security principals.

Regex or glob command filtering cannot make arbitrary command execution safe. Strong containment
requires a separate backend using containers, restricted OS identities/tokens, namespaces,
AppContainer, seccomp, or equivalent platform controls.

## Java and Flink Analogies

| Existing experience | Command runner concept |
|---|---|
| `ProcessBuilder(List<String>)` | argv-only `create_subprocess_exec` |
| `CompletableFuture.cancel()` | coroutine cancellation plus explicit OS-process cleanup |
| executor timeout | monotonic timeout and bounded termination |
| bounded queue/backpressure | retained-byte budget plus discard drain |
| Flink task failure classification | exit code vs timeout vs infrastructure error |
| task slot lifecycle | process group lifecycle, without security isolation |
