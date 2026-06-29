# Learning Progress

| Unit | Status | Evidence |
|---|---|---|
| L0 Python engineering foundation | Complete locally | Ruff、Pyright、25 passed、build、CLI doctor |
| L1 Agent Loop | Complete locally | 27 Runtime tests + deterministic ToolCall integration |
| L2 Provider and Tool Calling | In progress | Contracts and Fake Provider complete; real adapters pending |
| L3 Tool Registry | Not started | |
| L4 Workspace and Policy | Not started | |
| L5 File/Edit/Shell/Git tools | Not started | |
| L6 Context Budget | Not started | |
| L7 Session/Checkpoint/Trace | Not started | |
| L8 Git/test/repair | Not started | |
| L9 Skills and Hooks | Not started | |
| L10 MCP | Not started | |
| L11 Subagent and Worktree | Not started | |
| L12 CI, benchmark and release | In progress | CI workflow configured; remote run pending |

## L0 Notes

- `uv` owns the project interpreter, lock file, and reproducible commands.
- A `src` layout prevents tests from accidentally importing the repository directory.
- Pydantic validates runtime boundaries; Pyright checks internal contracts statically.
- Configuration precedence is explicit and tested.
- Logging applies recursive sensitive-field masking and configured-secret value scrubbing.
- On Windows, `python -m uv` works when the user Scripts directory is absent from `PATH`.
- TDD requires observing the expected failure before adding production behavior.

## L0 Review Lessons

- Validation errors are output channels too. Pydantic uses `hide_input_in_errors=True`, and
  environment parsing is normalized into the same application error type.
- Masking sensitive field names is insufficient. Configured secret values must also be removed
  from log messages, mapping keys, non-JSON objects, structured data, and exception text.
- A writable path is not automatically a usable data directory. Health checks must distinguish
  a directory from a regular file and stop at the nearest existing filesystem entry.
- `uv.lock` controls project dependencies, while isolated build dependencies need separate,
  hashed build constraints.
- A packaging smoke test must invoke the installed console script; calling the in-process Typer
  object cannot prove entry-point metadata is correct.

## L0 Verification

- Verified on 2026-06-29 with uv-managed Python 3.13.14 on Windows 11.
- `uv lock --check` and `uv sync --locked --all-groups`: passed.
- Ruff format/check and Pyright strict mode: passed.
- Pytest: 25 passed, 1 skipped because Windows denied symlink creation; branch-aware package
  coverage: 92.96%.
- Hashed Hatchling build produced wheel and source distribution.
- Both artifacts passed isolated, installed console-script smoke tests.
- `mini-code-agent doctor --json` reported healthy and did not expose an injected API key.
- GitHub Windows/Linux matrix: pending until the repository is pushed and Actions runs.

## L1/L2 Notes

- `Protocol` plays the role of a Java interface without forcing inheritance.
- Frozen Pydantic models are validated immutable DTOs at model, tool, and provider boundaries.
- The Agent Loop is an explicit state machine with hard turn, ToolCall, and timeout limits; it
  is not an unbounded `while` loop.
- ToolCall IDs act like correlation IDs: each executed call produces exactly one result with the
  same ID.
- Multi-call batches are preflighted before execution, preventing partial side effects when a
  later call is duplicated or over budget.
- Tool arguments and schemas use recursively immutable JSON views for deterministic replay while
  Pydantic serializers preserve standard JSON wire formats.
- M1 rejects write, execute, and network tool definitions; this declaration is enforced by the
  Runtime rather than trusted as documentation.
- Lifecycle events are best-effort observability: a sink failure cannot abort the run or mask
  cancellation.
- `ScriptedProvider` is analogous to a deterministic test double for an external service and
  enables full-loop tests without network calls or API keys.
- Cancellation is recorded and re-raised instead of swallowed, preserving asyncio structured
  concurrency semantics.
- Provider adapters own vendor message conversion and public error normalization; they cannot
  execute tools.

## M1 Local Verification

- uv-managed Python 3.13.14 project environment and isolated Python 3.12.13 on Windows 11.
- M1-focused tests: 48 passed.
- Full repository: 73 passed, 1 skipped because Windows denied symlink creation.
- Branch-aware package coverage: 95.82%.
- Ruff format/check and strict Pyright: passed.
- Hashed build plus isolated wheel/sdist console-script smoke: passed.
- Bandit source scan: no findings; pip-audit locked runtime dependencies: no known vulnerabilities.
- Real Anthropic and OpenAI-compatible adapters: not implemented; scheduled for M1b.
