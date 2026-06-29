# Learning Progress

| Unit | Status | Evidence |
|---|---|---|
| L0 Python engineering foundation | Complete locally | Ruff、Pyright、25 passed、build、CLI doctor |
| L1 Agent Loop | Complete locally | 27 Runtime tests + deterministic ToolCall integration |
| L2 Provider and Tool Calling | Complete locally | Anthropic + OpenAI-compatible adapters; 124 focused tests passed |
| L3 Tool Registry | Complete locally | Draft 2020-12 validation, dispatch and bounded result contract |
| L4 Workspace and Policy | In progress | WorkspaceBoundary complete; Policy/approval scheduled for M2b |
| L5 File/Edit/Shell/Git tools | In progress | Read/Search complete; Write/Edit/Shell/Git scheduled for M2b/M2c |
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

- Package version: `0.2.0a0`; milestone tag target: `v0.2.0-alpha.0`.
- uv-managed Python 3.13.14 project environment and isolated Python 3.12.13 on Windows 11.
- M1-focused tests: 48 passed.
- Full repository: 73 passed, 1 skipped because Windows denied symlink creation.
- Branch-aware package coverage: 95.82%.
- Ruff format/check and strict Pyright: passed.
- Hashed build plus isolated wheel/sdist console-script smoke: passed.
- Bandit source scan: no findings; pip-audit locked runtime dependencies: no known vulnerabilities.
- Real Anthropic and OpenAI-compatible adapters were intentionally deferred to M1b.

## M1b Provider Notes

- Anthropic and OpenAI-compatible use the same domain contract but different wire contracts.
  The Adapter is an anti-corruption layer, not a conditional branch in Agent Runtime.
- Anthropic puts `tool_use` and `tool_result` inside ordered content blocks. OpenAI-compatible
  Chat Completions uses assistant `tool_calls` plus separate `tool` role messages.
- Both providers stream tool arguments as partial JSON. The parser emits safe metadata-bearing
  deltas but waits for the terminal lifecycle before parsing a complete JSON object.
- OpenAI tool chunks are sparse: only the first chunk normally carries `id` and `name`.
  Per-index state makes later arguments fragments attributable and detects metadata changes.
- HTTPX async context managers guarantee response cleanup. The Adapter closes only internally
  created clients; injected clients are caller-owned.
- Public provider errors contain a normalized code, retryability, and a static message. Raw
  bodies, exception strings, payloads, and API keys stay behind the boundary.
- 401/403 are non-retryable authentication errors; 429, timeouts, and transient server/network
  failures are retryable classifications. The Adapter does not perform retries.
- Body and cumulative SSE data limits stop memory growth before parsing. Base URL and endpoint
  validation prevent credentials, query strings, fragments, traversal, and absolute endpoint
  substitution.
- `httpx.MockTransport` provides deterministic protocol tests without network access or secrets.
  It proves conversion and failure behavior, not live account availability.
- Chat Completions is the explicit compatibility profile. OpenAI Responses will be a separate
  future Adapter because its state and semantic event model differ.

## M1b Learning Exercises

1. Trace the two interleaved tool calls in
   `tests/unit/providers/test_openai_compatible.py` and record state by tool index.
2. Explain why Anthropic `tool_result` blocks must be moved before text in a mixed user message.
3. Add a malformed SSE lifecycle case as a failing test, then identify which invariant rejects it.
4. Compare `ProviderError.retryable` with Flink restart strategy: classification is input to a
   policy, not the retry policy itself.
5. Explain why a live API smoke test and a MockTransport contract test prove different things.

## M1b Local Verification

- Implemented programmatic Anthropic Messages and OpenAI-compatible Chat Completions adapters.
- Implemented non-streaming and SSE text/tool-call paths, usage and request-ID normalization.
- Provider-focused suite: 124 passed across shared transport, both adapters, Fake Provider, and
  cross-adapter integration contracts.
- The same unchanged `AgentRuntime` completes a real-wire-format ToolCall round trip through
  either adapter using credential-free mock HTTP responses.
- Full repository on Python 3.12.13 and 3.13.14: 191 passed per interpreter; 1 test skipped
  because the Windows account cannot create symlinks.
- Python 3.13 branch-aware package coverage: 89.91%, above the configured 85% gate.
- Ruff format/check and strict Pyright: passed.
- Bandit source scan: no findings. pip-audit: no known vulnerabilities; the unpublished local
  package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.3.0a0-py3-none-any.whl` and
  `mini_code_agent-0.3.0a0.tar.gz`.
- Wheel and sdist each passed isolated installed console-script smoke tests on Python 3.12 and
  3.13.
- Package version: `0.3.0a0`; local milestone tag target: `v0.3.0-alpha.0`.
- No live provider credential test or remote GitHub Actions result has been claimed.

## M2a Workspace and Registry Notes

- `resolve()` alone is not a security policy. The implementation first validates a
  platform-independent relative syntax, then rejects links/junctions, resolves strictly, and
  proves containment with path components.
- Cross-platform safety means rejecting forms that only become dangerous on another OS:
  drive-relative paths, UNC, alternate data streams, Windows device names, and trailing
  dots/spaces.
- `Path.relative_to(root)` is the containment proof. String prefix checks confuse roots such as
  `repo` and `repo-backup` and ignore platform case rules.
- `stat` checks metadata before open; `fstat` confirms the opened handle is a regular file.
  Reading `limit + 1` detects growth beyond the configured size.
- Strict UTF-8 and NUL rejection form an explicit text policy. The boundary preserves line
  endings so Read does not silently rewrite source representation.
- Traversal order is deterministic and bounded by file count, cumulative bytes, and depth.
  `.git` is excluded case-insensitively.
- JSON Schema validation happens in Registry before executor dispatch. Tools also validate direct
  calls, so bypassing Registry does not turn invalid arguments into filesystem operations.
- A definition is snapshotted once. Dynamic property changes cannot make advertised schema and
  dispatched implementation disagree.
- Literal search avoids model-controlled regex complexity. Unicode casefold positions are mapped
  back to original columns instead of reporting offsets in the expanded folded string.
- Read/Search use `asyncio.to_thread` so bounded disk work does not block the Agent event loop.
  Cancellation cannot kill a worker thread, but remaining work is read-only and budget-bounded.
- Workspace checks are not process isolation and do not eliminate TOCTOU if another process can
  mutate the tree. This limitation is explicit rather than hidden behind a “sandbox” claim.

## M2a Exercises

1. Explain why `C:secret.txt` is not an ordinary relative path on Windows.
2. Compare `resolve(strict=True)` with Java NIO `toRealPath()` and identify the race after check.
3. Add a new invalid JSON Schema to a test and trace why the tool never reaches `execute`.
4. Trace a file growing from exactly the limit to limit-plus-one during read.
5. Explain why `Straße NEEDLE`.casefold() changes offsets and how the position map repairs them.

## M2a Local Verification

- M2a-focused suite: 99 passed; 1 symlink test skipped because this Windows account cannot create
  symlinks.
- Full repository on Python 3.12.13 and 3.13.14: 290 passed per interpreter; 2 Windows symlink
  tests skipped per interpreter.
- Python 3.13 branch-aware package coverage: 90.33%, above the configured 85% gate.
- Ruff format/check and strict Pyright: passed.
- Bandit source scan: no findings. pip-audit: no known vulnerabilities; the unpublished local
  package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.4.0a0-py3-none-any.whl` and
  `mini_code_agent-0.4.0a0.tar.gz`.
- Wheel and sdist each passed isolated installed console-script smoke tests on Python 3.12 and
  3.13.
- Package version: `0.4.0a0`; local milestone tag target: `v0.4.0-alpha.0`.
- Linux symlink behavior still requires remote CI evidence; no remote result is claimed.
