# Learning Progress

| Unit | Status | Evidence |
|---|---|---|
| L0 Python engineering foundation | Complete locally | Ruff、Pyright、21 tests、build、CLI doctor |
| L1 Agent Loop | Not started | |
| L2 Provider and Tool Calling | Not started | |
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
  from log messages, structured data, and exception text.
- A writable path is not automatically a usable data directory. Health checks must distinguish
  a directory from a regular file.
- `uv.lock` controls project dependencies, while isolated build dependencies need separate,
  hashed build constraints.
- A packaging smoke test must invoke the installed console script; calling the in-process Typer
  object cannot prove entry-point metadata is correct.

## L0 Verification

- Verified on 2026-06-29 with uv-managed Python 3.13.14 on Windows 11.
- `uv lock --check` and `uv sync --locked --all-groups`: passed.
- Ruff format/check and Pyright strict mode: passed.
- Pytest: 21 passed; branch-aware package coverage: 86.49%.
- Hashed Hatchling build produced wheel and source distribution.
- Both artifacts passed isolated, installed console-script smoke tests.
- `mini-code-agent doctor --json` reported healthy and did not expose an injected API key.
- GitHub Windows/Linux matrix: pending until the repository is pushed and Actions runs.
