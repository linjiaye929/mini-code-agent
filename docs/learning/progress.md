# Learning Progress

| Unit | Status | Evidence |
|---|---|---|
| L0 Python engineering foundation | Complete locally | Ruff、Pyright、25 passed、build、CLI doctor |
| L1 Agent Loop | Complete locally | 27 Runtime tests + deterministic ToolCall integration |
| L2 Provider and Tool Calling | Complete locally | Anthropic + OpenAI-compatible adapters; 124 focused tests passed |
| L3 Tool Registry | Complete locally | Draft 2020-12 validation, dispatch and bounded result contract |
| L4 Workspace and Policy | Complete locally | WorkspaceBoundary + deterministic Policy + approval governance |
| L5 File/Edit/Command/Git tools | Complete locally | Read/Search/Write/Edit/argv Command plus hardened Git status/diff |
| L6 Context Budget | Complete locally | Deterministic estimator, atomic selection, side-effect pinning, runtime integration |
| L7 Session/Checkpoint/Trace | Complete locally | M3b Trace plus M3c stable Checkpoint/fail-closed Resume |
| L8 Git/test/repair | Complete and released | M4a Git + M4b Pytest + M4c bounded Repair |
| L9 Skills and Hooks | Complete and released | Inert Skills + monotonic Tool Hooks; v0.13 evidence |
| L10 MCP | Complete and released | Governed stdio, exact grants, real SDK integration; v0.14 evidence |
| L11 Subagent and Worktree | M6a released; M6b implementation complete, release gate in progress | Host-profiled analysis plus governed Worktree candidates/adoption |
| L12 CI, benchmark and release | In progress | v0.15 released; v0.16 local/cross-version gates in progress |

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

## M2b Policy and Governed Write Notes

- Permission is a control-plane decision outside the prompt. The model proposes a ToolCall but
  cannot turn `deny` into `allow` or approve its own `ask`.
- `GovernedToolExecutor` is analogous to a Spring interceptor: Schema validation, preview,
  policy, approval, and dispatch happen in a fixed order around tool execution.
- Ordered first-match rules are intentionally simple. Determinism and explainability are more
  valuable here than a hidden rule-merging algorithm.
- Non-interactive `ask` is denied before the approval handler. This prevents an accidentally
  permissive handler from converting unattended execution into implicit approval.
- `read_file` hashes complete raw bytes, including content outside a returned line window. The
  hash plays the role of a JPA optimistic-lock version.
- Existing writes require the exact hash; new writes are create-only. An approval therefore
  cannot silently authorize replacing a different ordinary snapshot.
- Edit requires exactly one literal match. This rejects ambiguous patches instead of guessing
  which occurrence the model intended.
- Same-directory temp files plus `os.replace` prevent partial replacement. They do not provide
  database isolation against a hostile concurrent external writer.
- Approval previews are bounded and contain only relative resources, static summary, bounded
  reason, risk, and diff. Approval is not code review or sandboxing.
- Blocking filesystem mutation runs through `asyncio.to_thread`, keeping the Agent event loop
  responsive while retaining strict byte and diff budgets.

## M2b Exercises

1. Trace one `edit_file` call from JSON Schema validation to the second hash check and list every
   point where it can fail without mutation.
2. Compare `expected_sha256` with JPA `@Version`: identify what both prevent and what filesystem
   replacement cannot guarantee.
3. Explain why an unattended `ask` must not call an auto-approve handler.
4. Change a file after preview but before execute and verify that execution returns `conflict`.
5. Explain why `os.replace` prevents partial content but not the final-check race.

## M2b Local Verification

- Governed-write focused tests: 32 unit tests plus 3 end-to-end integration tests passed.
- Full development suite on Python 3.12.13 and 3.13.14: 348 passed per interpreter; 2 Windows
  symlink privilege skips per interpreter.
- Branch-aware package coverage: 90.35%, above the configured 85% gate.
- Ruff format/check and strict Pyright: passed.
- Bandit source scan: no findings. pip-audit: no known vulnerabilities; the unpublished local
  package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.5.0a0-py3-none-any.whl` and
  `mini_code_agent-0.5.0a0.tar.gz`.
- Wheel and sdist each passed isolated installed console-script smoke tests on Python 3.12 and
  3.13.
- Package version: `0.5.0a0`; local milestone tag target: `v0.5.0-alpha.0`.
- Linux behavior and remote GitHub Actions still require remote evidence; no remote result is
  claimed.

## M2c Governed Command Notes

- An argv array is data passed directly to an executable. A shell string is a program in a
  second language with interpolation, expansion, redirects, and platform-specific quoting.
- `asyncio.create_subprocess_exec` corresponds to Java `ProcessBuilder(List<String>)`; avoiding
  `shell=True` removes shell parsing but does not make the executable safe.
- Coroutine cancellation does not automatically own an OS process. The runner must terminate the
  process tree, close/drain pipes, await the process, and only then propagate cancellation.
- Stdout and stderr share a retained-byte budget. After overflow, readers continue discarding
  bytes until termination so pipe backpressure cannot deadlock cleanup.
- A minimal environment prevents accidental API-key inheritance. It cannot stop a process from
  opening credential files under the same OS identity.
- Execute defaults to deny. An explicit rule can narrow tool, cwd, risk, session, trust source,
  and submitted executable; interactive `ask` still requires approval.
- POSIX process groups and Windows `taskkill /T /F` are lifecycle boundaries. They are not
  containers, restricted identities, or sandboxes.
- Non-zero exit is command data, while spawn/I/O/cleanup failures are infrastructure errors. This
  is analogous to distinguishing Flink task result, timeout, and TaskManager failure.

## M2c Exercises

1. Explain why `["python", "-m", "pytest"]` has different parsing semantics from a shell string.
2. Trace timeout from `asyncio.wait` through process-tree termination and pipe draining.
3. Explain why stopping capture at 1 MiB without draining can deadlock `process.wait`.
4. Compare coroutine cancellation, Java Future cancellation, and OS process termination.
5. List what minimal environment and cwd validation prevent, then list what requires a sandbox.

## M2c Local Verification

- Command/Policy/Tool focused suite: 70 passed after final cleanup hardening.
- Full Python 3.12.13 and 3.13.14 development suite: 395 passed per interpreter; 3 Windows
  symlink privilege skips per interpreter.
- Branch-aware package coverage: 89.73%, above the configured 85% gate.
- Ruff format/check and strict Pyright: passed.
- Bandit: no unsuppressed findings; the deliberate `subprocess` constants import has a scoped
  B404 suppression while execution remains argv-only. pip-audit found no known vulnerabilities;
  the unpublished local package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.6.0a0-py3-none-any.whl` and
  `mini_code_agent-0.6.0a0.tar.gz`.
- Wheel and sdist each passed isolated installed console-script smoke tests on Python 3.12 and
  3.13.
- Package version: `0.6.0a0`; local milestone tag target: `v0.6.0-alpha.0`.
- Linux process behavior and remote GitHub Actions still require remote evidence; no remote
  result is claimed.

## M3a Deterministic Context Budget Notes

- `ContextManager` is request admission control. `AgentRuntime` still owns the complete in-memory
  transcript, while each Provider sees only a validated `ContextWindow`.
- `Utf8TokenEstimator` counts canonical request JSON bytes plus framing. It is deterministic and
  provider-neutral, but is not an exact tokenizer and does not justify a token-savings claim.
- The first user goal and newest completed unit are required. ToolCall and ToolResult batches are
  correlated by ID sets and selected or omitted together.
- Only all-read-only exchanges are optional. Write, execute, network, mixed, and unknown-tool
  exchanges are pinned because erasing action evidence can cause a fresh-ID repeat.
- Optional history is retained as a newest contiguous suffix; pinned exchanges remain in original
  order even across omitted read-only history.
- A static omission marker records bounded counts and SHA-256 of the complete canonical
  transcript. The hash supports identity/equality evidence, not recovery, authentication, or
  Secret protection.
- Fixed goal overflow, latest-unit overflow, and pinned-history overflow are distinct internal
  failures. Runtime exposes one static context-limit message and makes no Provider call.
- `ContextCompacted` is best-effort observability. Event-sink failure cannot change request
  selection.
- M3a has no rolling model summary, artifact externalization, durable Session, or crash recovery.

## M3a Exercises

1. Trace budgets 609, 610, and 611 through `test_compaction_keeps_a_contiguous_recent_suffix_at_boundaries`.
2. Explain why a completed `write_file` exchange must remain visible even when its ToolCall ID
   cannot repeat.
3. Change a historical tool name to one absent from current definitions and explain the fail-safe
   classification.
4. Compare the complete `AgentResult.messages` with the third Provider request in the large-result
   integration test.
5. Explain why observed Provider usage cannot predict the next request and why the estimator is
   injected.

## M3a Local Verification

- Context manager unit suite: 18 passed.
- Context, Runtime, and context integration suite: 77 passed.
- Full Python 3.12.13 and 3.13.14 development suite: 440 passed per interpreter; 3 Windows
  symlink privilege skips per interpreter.
- Python 3.13 branch-aware package coverage: 90.12%, above the configured 85% gate.
- Ruff format/check and strict Pyright: passed.
- Bandit: no unsuppressed findings. pip-audit found no known dependency vulnerabilities; the
  unpublished local package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.7.0a0-py3-none-any.whl` and
  `mini_code_agent-0.7.0a0.tar.gz`.
- Wheel and sdist each passed isolated installed console-script smoke tests on Python 3.12 and
  3.13.
- Package version: `0.7.0a0`; local milestone tag target: `v0.7.0-alpha.0`.
- Linux behavior and remote GitHub Actions still require remote evidence; no remote result is
  claimed.

## M3b Versioned Session and Trace Notes

- M3b uses SQLite as both event log and projection store because a SQLite index plus JSONL payload
  cannot share one atomic commit.
- `sessions` and `runs` are materialized lifecycle projections. `trace_events` is the append-only
  source of ordered evidence; neither is a Checkpoint.
- `PRAGMA user_version = 1` identifies the database format. Future versions are rejected rather
  than silently downgraded or guessed.
- Each append uses `BEGIN IMMEDIATE`, updates projection, inserts one event, advances counters and
  hash head, then commits. Trigger fault injection proves all changes roll back together.
- `event_id` handles exact retry idempotency. `(session_id, sequence)` establishes deterministic
  order. Reusing an event ID with different payload or Session fails closed.
- `ModelStarted` is recorded before Provider I/O and `ToolStarted` before Tool execution.
  `ToolStarted` without `ToolCompleted` is indeterminate external state.
- Required `EventJournal` failure stops later work with `PERSISTENCE_ERROR`; UI/log `EventSink`
  remains best effort.
- Cancellation remains dominant: Runtime attempts a terminal event once and re-raises
  `CancelledError` even if journaling fails.
- Trace hashes bind schema, Session, sequence, previous hash, and typed event payload. They detect
  inconsistent data but are not signatures or authenticated audit records.
- Typed events exclude prompts, arguments, ToolResults, patches, and command output. Configured
  Secret values are replaced only in free-form stop errors.
- WAL, full synchronous writes, foreign keys, event/query budgets, and bounded busy timeout are
  local durability controls, not distributed consistency.

## M3b Exercises

1. Trace `RunStarted -> ModelStarted -> ModelCompleted -> ToolStarted -> ToolCompleted ->
   ModelStarted -> ModelCompleted -> RunStopped` and identify which operations occur between
   each pair.
2. Explain why writing JSONL first and SQLite second cannot guarantee atomicity across a crash.
3. Re-append the same event object, then reuse its ID with a different Run; compare outcomes.
4. Hold `BEGIN IMMEDIATE` in a second connection and measure where the busy timeout becomes
   `PERSISTENCE_ERROR`.
5. Trigger failure on the second governed `write_file` ToolStarted and explain why only the first
   file exists.
6. Modify one payload and recompute no hashes; then explain why an attacker able to rewrite all
   hashes is outside this integrity claim.
7. Compare an active Run, a started-only Tool, and a Checkpoint; define what M3c must decide for
   each.

## M3b Local Verification

- Persistence unit suite: 42 passed.
- Runtime/Context/Persistence/Integration focused suite: 152 passed.
- Full Python 3.12.13 and 3.13.14 development suite: 505 passed per interpreter; 3 Windows
  symlink privilege skips per interpreter.
- Python 3.13 branch-aware package coverage: 90.09%, above the configured 85% gate.
- Ruff format/check and strict Pyright: passed.
- Persistence and integration tests also pass with unclosed SQLite connections promoted to
  `ResourceWarning` errors.
- Bandit: no unsuppressed findings. pip-audit found no known dependency vulnerabilities; the
  unpublished local package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.8.0a0-py3-none-any.whl` and
  `mini_code_agent-0.8.0a0.tar.gz`.
- The exact wheel and sdist passed four isolated console-script smoke tests on Python 3.12 and
  3.13.

## M3c Checkpoint and Resume Notes

- SQLite schema v2 adds Checkpoints through a transactional v1 migration while Trace envelope
  version and historical hashes remain unchanged.
- Runtime saves only stable model-input state: initially and after complete ToolResult batches.
- Canonical payload hashes detect accidental snapshot corruption; Tool and bounded Workspace
  fingerprints reject incompatible recovery environments.
- Resume verifies the complete Trace and scans every post-Checkpoint event in pages. Write,
  execute, and network actions block automatic Resume even when ToolCompleted exists.
- Provider and read-only retries require independent explicit policy.
- Claim reanalyzes caller input, compares the Trace head under `BEGIN IMMEDIATE`, interrupts the
  source Run, starts a new Run, and consumes the Checkpoint atomically.
- Checkpoints contain full conversation/tool state as bounded plaintext. Encryption, signed audit,
  distributed leases, external reconciliation, and exactly-once execution remain non-claims.

## M3c Local Verification

- Full Python 3.12.13 and 3.13.14 development suite: 551 passed per interpreter; 4 Windows
  symlink privilege skips per interpreter.
- Python 3.13 branch-aware package coverage: 89.89%, above the configured 85% gate.
- Resume analysis/claim plus process-boundary integration: 16 passed.
- Concurrent claim test produced exactly one winner in five consecutive runs.
- Ruff and strict Pyright: passed.
- Bandit: no unsuppressed findings. pip-audit found no known dependency vulnerabilities; the
  unpublished local package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.9.0a0-py3-none-any.whl` and
  `mini_code_agent-0.9.0a0.tar.gz`.
- The exact wheel and sdist passed four isolated console-script smoke tests on Python 3.12 and
  3.13.

## M4a Read-only Git Notes

- `--porcelain=v2 -z` is a versioned machine protocol. NUL framing preserves spaces, tabs,
  newlines, rename source paths, and leading dashes without shell-style guessing.
- Git status has separate index/worktree XY states. This maps naturally to database committed
  state versus uncommitted in-memory changes, but neither layer is locked by an observation.
- A nominally read-only Git command can execute fsmonitor, external diff, textconv, pager, or
  submodule behavior from configuration. The client disables these extension points explicitly.
- `--no-optional-locks` prevents refresh-only index writes; tests compare index bytes and mtime.
- Workspace must equal repository top-level. Rejecting nested parent repositories prevents an
  Agent scoped to one folder from reading sibling changes.
- `--` terminates options. M4a avoids model-controlled pathspec entirely, making leading-dash
  filenames ordinary data.
- Status and diff SHA-256 values identify exactly what the Agent observed. They do not prove the
  repository stayed unchanged afterward.
- M4a is evidence only: no add, commit, reset, clean, checkout, fetch, push, or automatic repair.

## M4a Exercises

1. Compare human `git status` with porcelain v2 and explain why only one is a parser contract.
2. Create filenames containing spaces, tabs, newlines, and a leading dash; inspect NUL framing.
3. Configure `core.fsmonitor` and `diff.external` to write a marker, then trace why M4a suppresses
   both.
4. Run status repeatedly and compare `.git/index` bytes/mtime with and without optional locks.
5. Configure Workspace as a child of a larger repository and explain the information-boundary
   rejection.
6. Change a file immediately after obtaining the status SHA-256 and explain why the fingerprint
   is evidence identity, not optimistic locking.

## M4a Local Verification

- Full Python 3.12.13 and 3.13.14 development suite: 583 passed per interpreter; 4 Windows
  symlink privilege skips per interpreter.
- Python 3.13 branch-aware package coverage: 89.97%, above the configured 85% gate.
- Git models/parser/client, Tool, and Agent focused suite: 32 passed.
- Git plus Command regression suite: 56 passed.
- Real Git extension suppression and byte-identical index tests: passed.
- Ruff and strict Pyright: passed.
- Bandit: no unsuppressed findings. pip-audit found no known dependency vulnerabilities; the
  unpublished local package itself was skipped because it is not on PyPI.
- Hashed build produced `mini_code_agent-0.10.0a0-py3-none-any.whl` and
  `mini_code_agent-0.10.0a0.tar.gz`.
- The exact wheel and sdist passed four isolated console-script smoke tests on Python 3.12 and
  3.13.
- GitHub prerelease `v0.10.0-alpha.0` contains both artifacts; remote Actions runs
  `28415910745` and `28416108426` completed successfully.

## M4b Governed Pytest Notes

- `run_tests` is a dedicated execute Tool, not an alias for arbitrary `run_command`. The model can
  select only existing workspace-relative files/directories and provide an approval reason.
- The host owns the absolute Python executable, timeout, `maxfail`, default targets, and trusted
  plugins. Profile models are immutable and validated before execution.
- Fixed argv uses `python -I -B -m pytest`, disables bytecode, ambient plugin, and Pytest cache
  writes, and places `--` before validated targets.
- The host preserves the active `sys.executable` path. Resolving a POSIX venv symlink selects the
  base interpreter and loses environment-local Pytest under isolated startup.
- Execute remains denied by default. Interactive `ASK` requires independent approval;
  non-interactive `ASK`, rejection, and approval exceptions start no process.
- Pytest exit classification is separate from JUnit report status. A missing or invalid report
  preserves exit, duration, stdout, and stderr rather than becoming an undifferentiated Tool error.
- JUnit is untrusted test output. The parser bounds bytes before strict UTF-8/XML parsing, rejects
  DTD/entities and contradictory outcomes, recomputes counts from cases, and bounds every returned
  diagnostic dimension.
- Temporary report paths are host-created, omitted as fields, directly echoed forms are replaced,
  and files are cleaned in `finally`; transformed exfiltration still requires a sandbox.
- Real Agent integration proves stdout and failure details reach the next Provider request while
  lifecycle SQLite Trace stores only typed start/completion metadata.
- M4b is diagnostics only. It does not choose edits, retry tests, or claim a Repair Loop.
- Approval and resource limits are not an OS sandbox; approved tests retain the Agent user's
  filesystem, process, and network authority.

## M4b Exercises

1. Trace a `run_tests` ToolCall through Registry, preview, Policy, approval, runner, JUnit parser,
   ToolResult, and the next Provider request.
2. Compare exit 1 with report status `invalid`; explain why both facts must be retained.
3. Add `pytest-asyncio` first through ambient installation and then through `trusted_plugins`;
   explain which execution surface the host explicitly authorized.
4. Replace JUnit aggregate counts with false values and verify typed counts do not change.
5. Trigger output overflow and timeout separately; verify process trees and report files are
   cleaned.
6. Explain why disabling `.pytest_cache` is hygiene rather than a workspace immutability guarantee.

## M4b Local Verification

- Full Python 3.12.13 and 3.13.14 development suite: 678 passed per interpreter; 5 Windows
  symlink privilege skips per interpreter.
- Python 3.13 branch-aware package coverage: 90.25%, above the configured 85% gate.
- Contract/parser/runner/tool/policy/real-Pytest/Agent coverage includes invalid XML, DTD/entity,
  byte/case/diagnostic boundaries, every Pytest exit code, timeout/output status, target escape,
  default deny, approval, rejection, non-interactive mode, and Trace exclusion.
- Ruff format/check and strict Pyright: passed.
- Bandit: no findings after using `defusedxml` for untrusted JUnit. pip-audit found no known
  vulnerabilities in the locked runtime dependency export.
- Hashed build produced `mini_code_agent-0.11.0a0-py3-none-any.whl`
  (`70570686680de139a0647a3c177831ac6335a53afcf0107128c8dbd751eac30c`) and
  `mini_code_agent-0.11.0a0.tar.gz`
  (`b958037de04030428e817c2a17595088aeff287a4d12284815075d6c23e18d06`).
- The exact wheel and sdist each passed isolated installed-package smoke tests on Python 3.12 and
  3.13, including imports of `PytestRunner` and `RunTestsTool`.
- Initial remote run `28442095039` exposed two Linux assumptions: resolving a POSIX venv executable
  erased environment identity, and opening a directory failed before `fstat`. Both received
  focused fixes and regression coverage.
- GitHub Actions runs `28442443209` and final merged-main run `28442701360` passed quality plus
  Ubuntu/Windows on Python 3.12/3.13.
- Annotated tag `v0.11.0-alpha.0` targets commit
  `575ecb8f3a73eadb9598b1eda2e52fdfadcde56e`. The verified GitHub prerelease is
  <https://github.com/linjiayebat/mini-code-agent/releases/tag/v0.11.0-alpha.0>; its uploaded wheel
  and sdist SHA-256 digests match the locally smoke-tested artifacts listed above.

## M4c Bounded Repair Notes

- `RepairRuntime` 是独立于普通 Agent Loop 的宿主控制平面：一个 Worker 调用只对应一次
  repair attempt，baseline、Pytest target、成功判定、预算和停止原因都由宿主拥有。
- Admission 要求显式批准、三个 root 完全一致、仓库干净、editable path 是现存普通文件，
  并用 literal top-level pathspec 逐一证明 exact tracked set。
- `RepairActionGuard` 在普通 Policy 前拒绝越界 write、execute 和 network；普通
  Schema/Policy/approval 仍继续保护允许的 read 和 scope write。
- baseline 和每轮验证均使用固定 `python -I -B -m pytest`。成功必须同时满足 process
  passed 与 JUnit complete，模型最终文本不参与判定。
- 每次 Worker 后核对 branch、staged/unstaged status、exact path set、submodule、完整 patch
  和 Workspace identity；测试前后证据不一致会以 `test_mutated_repository` 停止。
- canonical failure fingerprint 排除易抖动 stdout/stderr/duration/details；attempt、time、
  patch、prompt 和 same-failure 各自有硬限制。
- SQLite schema v3 的 Repair journal 与 Agent Trace 分离，但同样使用 UUID 幂等键、
  canonical JSON、SHA-256 前驱链和事务式 projection。started-only Repair 不自动恢复。
- Repair 是 library-level composition，不是 OS sandbox、自动 rollback、自动 commit、
  Worktree isolation 或 CLI workflow。

## M4c Exercises

1. 从 `RepairRequest` 跟踪 clean admission、baseline、Worker、Git validation、retest 和
   `RepairStopped`。
2. 对比 clean status 与 exact tracked query，解释 ignored path 为什么必须单独拒绝。
3. 让 Worker 请求 scope 外 write，验证 ordinary approval handler 未被调用且文件未改变。
4. 让测试在验证阶段修改源码，验证 passing JUnit 仍不能产生 `repaired`。
5. 让两轮 diagnostics 相同但 stdout 不同，验证 repeated-failure 仍能停止。
6. 打开 SQLite 检查 Repair rows，说明为什么不保存 patch 和 diagnostics，同时为什么 Agent
   Checkpoint 仍属于敏感明文。

## M4c Local Verification

- Repair contract、scope、fingerprint、worker、event、runtime、SQLite migration/journal 和
  accessors 均有单元测试。
- 真实集成测试覆盖一次 Agent read/edit 修复成功、scope 外写入 pre-policy 拒绝、dirty
  repository 在 Provider/Pytest 前拒绝，以及 baseline test mutation 在 Provider 前停止。
- Python 3.12.13 与 3.13.14 完整开发套件各为 798 passed、6 skipped；skip 均为 Windows
  symlink 权限条件。Python 3.13 分支覆盖率为 90.88%，超过 85% 门槛。
- Ruff format/check、strict Pyright、Bandit 与锁定运行时依赖 pip-audit 均通过。
- 并发 claim 回归在同一测试内执行 10 个独立数据库轮次，并额外连续执行 20 次，共 200
  轮均保持一个 winner 和一个 `checkpoint_stale` loser。
- 代码审查新增迁移失败保持 v2 可重试、成功终态必须有可信测试证据、editable path 单项
  1 KiB 上限回归。主分支初始 CI `28465232075` 暴露 Session projection/event 双快照竞态；
  Session 与 Repair 验证已改为单 SQLite 只读事务，write contention 统一使 Resume plan
  stale。
- 哈希约束构建生成 `mini_code_agent-0.12.0a0-py3-none-any.whl`
  (`77c52333421cf367201d5bdb7c24efaa1b0caa39502caea94cf833115ae588cd`) 和
  `mini_code_agent-0.12.0a0.tar.gz`
  (`f6534836943eed929aa0fae61f50aaa1ecb83d9cf475419ca119bdcdd44ddec8`)；二者在 Python
  3.12/3.13 的四组隔离安装 smoke 均通过。
- 修复 PR CI `28466399363` 与最终 main CI `28466523593` 的 quality、Ubuntu/Windows ×
  Python 3.12/3.13 五个 job 全部通过。
- Annotated tag `v0.12.0-alpha.0` 解引用到
  `071ea0556fa294cddbadc9c5698a79fb9a104b7d`。GitHub prerelease
  <https://github.com/linjiayebat/mini-code-agent/releases/tag/v0.12.0-alpha.0> 已上传 wheel
  与 sdist，远端 asset digest 与上述本地 smoke 制品一致。

## M5a Governed Skills and Hooks Notes

- Skill 不是“一个会执行的插件”，而是带 source/trust/version/SHA 的不可信 Markdown
  数据。`list_skills` 只给 descriptor，`load_skill` 显式消耗 context。
- managed/user/project 是宿主选择的 provenance，不由 YAML 声明。qualified ID 避免
  project Skill 静默覆盖 user/managed Skill；同 source 冲突全部 quarantine。
- restricted PyYAML 与 Pydantic 分两层：前者拒绝 duplicate key、alias、custom tag 和
  非字符串 key，后者拒绝 unknown field、非法 name/SemVer 和越界文本。
- discovery 与 load 之间存在 TOCTOU。load 除 SHA 外还重验 root/dir/file、reparse、
  open-handle device/inode/ctime、metadata 和 byte count；漂移必须 rediscover。
- Hook 授权是 monotonic：pre-Hook 的 continue 只表示进入后续 Policy，不是 allow；
  block/timeout/exception/invalid/audit failure 都在 Tool 前 fail closed。
- post-Hook 发生在 ToolResult 已产生之后，因此普通失败只能隔离并继续 observer，不能把
  已完成副作用改写成“未执行”。取消仍传播，调用方保留不确定边界。
- Hook audit 不保存 arguments、resources、diff、result、Skill body 或 exception text。
  M5a 只有 null/recording sink；没有 `run_id/turn` 的 Tool API 前不虚构 durable audit。
- project command/HTTP/prompt Hook、动态 Python import、Skill supporting files 和 MCP
  均明确不在 M5a 范围。

## M5a Exercises

1. 跟踪 project Skill 从 `SKILL.md` 到 descriptor，再到 fingerprint-required ToolResult。
2. 替换相同字节文件，比较 SHA 与 file identity 分别能证明什么。
3. 让 Skill 指示写文件，证明正文可影响模型意图但不能改变 Policy authority。
4. 让 pre-Hook continue 配合 deny Policy，定位最终拒绝发生在哪一层。
5. 让 pre/post Hook 分别 timeout，解释为什么前者阻止 Tool、后者保留 ToolResult。
6. 设计 command Hook 时列出进程 argv、环境、cwd、输出、timeout、approval 和 sandbox
   必须新增的边界，说明为什么不能直接 `subprocess`。

## M5a Local Verification

- Skill/Hook model、parser、catalog、lazy load、Tool、runner、Policy integration 和真实 Agent
  集成测试已加入。Python 3.12.13 与 3.13.14 完整开发套件各为 867 passed、8 skipped；
  skip 均为 Windows symlink privilege 条件。
- 两个解释器的 branch-aware package coverage 均为 90.86%，超过配置的 85% 门槛。
- Ruff format/check、strict Pyright、Bandit 和去除 editable 项目后的 locked runtime
  pip-audit 均通过；pip-audit 未发现已知第三方依赖漏洞。
- 最终哈希约束构建生成 `mini_code_agent-0.13.0a0-py3-none-any.whl`，大小 145319 bytes，
  SHA-256 为 `0192ff3fa003ed1332d364fc29a8d173b8f9fd8e187e682aa81165dbd8684d01`；
  `mini_code_agent-0.13.0a0.tar.gz` 大小 624112 bytes，SHA-256 为
  `b751e177b9827538400ab68f0922c323857c8d7ba65f551a32baafb455264e71`。
- 上述 exact wheel/sdist 在 Python 3.12/3.13 的四组隔离环境中均通过 console-script
  smoke，并验证 `SkillCatalog` 与 `ToolHookRunner` 可从安装包导入。
- PR #3 <https://github.com/linjiayebat/mini-code-agent/pull/3> 的 CI run `28470334222`
  以及合并提交 `f4f8dcb5864147214f4a9d2d3030c5af8bfec7b5` 的 main CI run
  `28470432091` 均通过 quality、Ubuntu/Windows × Python 3.12/3.13 五个 job。
- Annotated tag `v0.13.0-alpha.0` 解引用到上述合并提交。非 draft GitHub prerelease
  <https://github.com/linjiayebat/mini-code-agent/releases/tag/v0.13.0-alpha.0> 已发布；
  远端 wheel/sdist 的名称、大小和 SHA-256 digest 与本地四组 smoke 制品完全一致。

## M5b Governed MCP Notes

- MCP 是互操作协议，不是权限系统。local stdio server 在 initialize 前后都已经是拥有当前
  用户 OS 权限的进程，因此必须在 process open 前单独审批完整 executable/argv/cwd。
- Profile 要求 absolute existing executable；command/cwd 在构造和启动前各校验一次，
  拒绝 relative、missing、symlink、junction/reparse、non-regular 和不可执行路径。
- connection approval 只授权启动一个进程。每次 ToolCall 仍走 Registry、ActionPreview、
  Hook、Policy 和 Tool approval；MCP alias 的 provenance 固定为
  `TrustSource.EXTENSION`。
- server identity、protocol 和 Tools capability 通过后，仍要把 `tools/list` 与 host grant
  做 exact set equality。unexpected/missing/duplicate/pagination/listChanged/task-required
  或 schema hash drift 都让整次连接失败，不做 partial admission。
- host grant 决定 local alias、description、SideEffect 和 RiskLevel。server instructions、
  title、description、annotation、icons 与 `_meta` 不进入 model Tool definition。
- SDK v1 的 stdio/ClientSession 使用 AnyIO context，退出具有 Task affinity。production
  adapter 让 dedicated owner worker 进入/退出 context，caller Task 只通过 proxy 发请求和
  signal close，避免跨 Task cancel-scope 与 Windows pipe 泄漏。
- result 只接受 text 与 object-shaped structured JSON，并经过 block/char/UTF-8 byte/
  depth/node/string/finite-number/output-schema 上限；unsupported content 整体失败。
- 调用串行且不自动 retry。read-only timeout 可报告 timeout；side-effect Tool timeout/
  cancellation 只能报告 completion unknown，不能声称远端操作已回滚。
- stderr 丢弃可以避免无界日志和 secret 进入 Trace，但会降低诊断能力。M5b 不宣称 durable
  MCP lifecycle audit、package signature、argument-file hash 或 OS sandbox。

## M5b Exercises

1. 跟踪 `McpServerProfile -> approval_request -> build_stdio_parameters`，列出哪一层能看到
   SecretStr 值，哪一层只能看到环境变量名称。
2. 修改 fixture server name/version，比较 identity mismatch 与 input schema drift 的
   error code 和共同的“零 Tool admission”结果。
3. 在 server description 和 `_meta` 放入 secret/prompt injection，证明 snapshot 和
   model-facing definition 都不包含它。
4. 用 extension deny Policy 调用真实 stdio Tool，检查 call log 不存在；再改成 ASK 且拒绝
   Tool approval，证明 connection approval 不能替代 action approval。
5. 从另一个 asyncio Task 调用 `aclose()`，画出 owner worker、proxy 和 AnyIO context 的
   任务关系。
6. 构造 129 个 Tool、129 个 text block、超长 text、深层 JSON 和 NaN，验证 global/profile
   两级预算及静态错误。
7. 把一个 read-only grant 改为 WRITE 并制造 timeout，说明为什么 output 使用
   `mcp_tool_completion_unknown`。
8. 阅读官方 MCP security best practices，写出 local stdio 与 remote HTTP/OAuth threat
   model 不能共用的五个边界。

## M5b Release Verification

- Python 3.12.13 与 3.13.14 本地全量开发套件均为 961 passed、10 skipped，branch-aware
  package coverage 均为 90.84%；10 个 skip 均因当前 Windows 会话缺少 symlink privilege。
- Ruff format/check、strict Pyright、Bandit 已通过；对 locked runtime export 执行
  pip-audit，结果为 `No known vulnerabilities found`。
- 真实官方 SDK stdio 集成覆盖 handshake、exact identity/schema、Agent ToolCall、
  structured output、shutdown、extension deny、独立 Tool approval、unexpected Tool、
  schema drift 和 cross-task close。
- 首次构建发现 `.coverage` 被带入 sdist，导致归档膨胀且 hash 漂移；显式 Hatch exclude、
  固定 `SOURCE_DATE_EPOCH` 和 `tests/artifact_test.py` 将该问题转化为 CI 发布契约。
- 首轮 Linux CI 发现 uv venv 的 `sys.executable` 是 symlink，与 MCP profile 的 unlinked
  executable 约束冲突；单元 fixture 改用真实解释器，POSIX stdio 集成改用宿主创建的普通
  launcher，保留 venv 依赖与生产安全约束。
- 最终 wheel `mini_code_agent-0.14.0a0-py3-none-any.whl` 为 158701 bytes，SHA-256
  `6e224b01fb69eafdc96019c7bd4c7544bce8e61314d52fd77184b78c9d4f4e22`；sdist
  `mini_code_agent-0.14.0a0.tar.gz` 为 466629 bytes，SHA-256
  `b9e36d3d1a828e3f6fd3ef39f23de7d4f669851bddf02c84b3793e14af2881d3`。两次构建逐字节
  一致，且两个制品在 Python 3.12/3.13 四组隔离环境中均通过 import、CLI 与真实 MCP
  connect/call/close/shutdown smoke。
- PR #4 <https://github.com/linjiayebat/mini-code-agent/pull/4> 的最终 CI run
  `28501386707` 与 merged-main run `28501782935` 均通过 quality、Ubuntu/Windows x
  Python 3.12/3.13 五个 job。main CI 的 Windows 两组各 971 passed；Ubuntu 两组各
  970 passed、1 个 Windows-specific path identity 条件跳过。
- Annotated tag `v0.14.0-alpha.0` 解引用到 merge commit
  `1af6a07632abe291ac4adc0ccb04aaa1be5c7d38`。非 draft GitHub prerelease
  <https://github.com/linjiayebat/mini-code-agent/releases/tag/v0.14.0-alpha.0> 已发布，
  远端 asset name、size 和 GitHub SHA-256 digest 与上述本地 smoke 制品完全一致。

## M6a Governed Analysis Subagent Notes

- M6a 只实现分析 delegation，不实现 child 写入。宿主 `SubagentProfile` 固定 parent local
  Tool、child system prompt、exact read-only Tool names、Agent limits、Task/timeout/
  summary/evidence/result budgets；模型只能提交 bounded unique tasks 和 reason。
- child 不是 parent context fork。每个 `AgentRuntime` 从一个 fresh user message 开始，
  不继承 parent/sibling transcript，因此 context attribution 更清晰，但不能据此宣称
  OS、内存、Provider 凭证或数据隔离。
- 所有 child ID 在工厂调用前一次性生成并验证唯一。Provider 和 governed Tool executor
  必须逐 child 独立，Tool definition 顺序必须与 profile 完全一致，全部为 `READ_ONLY`，
  `governance_enforced is True`，且 provenance 为 `TrustSource.SUBAGENT`。
- profile 拒绝任何 `delegate_` child Tool；`build_subagent_tools()` 再拒绝 duplicate
  profile ID/local name 和跨 profile parent-local/child-name 冲突，避免形成递归能力图。
- 一个 `asyncio.TaskGroup` 拥有全部 child Task；Semaphore 只限制 active concurrency，
  ordinal slots 单独保证 output order。代码没有 detached Task、daemon thread 或后台进程。
- child timeout/failure 只生成该 ordinal 的 typed result，sibling 继续；outer batch timeout
  取消未完成 Task 并填充 `BATCH_TIMED_OUT`。外部 `CancelledError` 在 child、Supervisor 和
  parent Tool 路径显式 re-raise。
- child 固定 `NON_INTERACTIVE`，因此 Policy `ASK` 不会弹出嵌套审批，而是 fail closed。
  parent delegation Policy 与 child Tool Policy 是两次独立判断。
- `untrusted_summary` 只做长度/NUL边界，不是证据。Evidence 只保留 ToolCall ID/name、
  error、content character count 与 ToolResult UTF-8 SHA-256，不复制参数或原始结果。
- Subagent events 只含 parent call ID、profile、child/ordinal、status、duration、counts、
  usage 和 result hash；task、prompt、messages、summary、arguments、ToolResult、repository
  content 和 exception text 均不进入 event。
- parent `SubagentAnalysisTool` 生成 profile-specific Draft 2020-12 Schema、medium-risk
  preview 和 canonical ASCII result；最终按 UTF-8 byte budget 拒绝 oversized batch。
- M6a 未做 token/latency/cost/quality benchmark。额外 child Provider/Tool 调用可能降低
  parent context pressure，也可能增加成本与尾延迟，简历不能写“节省 X% token”。

## M6a Review Lessons

- “最多并发 N 个”不能只靠创建 N 个 detached Task；并发度和生命周期所有权是两个问题。
  Semaphore 解决前者，TaskGroup 解决后者。
- `CancelledError` 是控制流，不是普通失败。把它转成 child failed 会让调用方无法区分用户
  取消与业务错误，并破坏父子任务终止语义。
- preflight 不只是 Schema。重复 child ID 如果直到 result model 才发现，Provider 已产生
  成本，且两个 child 会共享 run ID；因此 ID tuple 必须先完整验证。
- “只读 profile”必须验证 definition side effect、governance marker 和 provenance，不能靠
  Tool 名称或 prompt 约定。
- parent Tool 结果再次验证 `SubagentBatchResult`，并在最后一步按 serialized bytes 计预算；
  Python 字符数无法代表 escaped ASCII JSON 大小。
- Pytest 默认 import mode 会把非 package 测试目录中的同名文件当成同一顶层模块。新增
  `tests/unit/subagents/__init__.py` 后，完整 suite 才能与既有 `test_events.py`、
  `test_models.py`、`test_tools.py` 同时收集。
- Worktree 能隔离 checkout 路径冲突，但不是 OS sandbox，也不自动解决 candidate adoption、
  conflict、cleanup、rollback 或 concurrent host mutation；这些必须留给 M6b 单独设计。

## M6a Release Verification

- 完整 parent/child 集成使用真实 `AgentRuntime`、`GovernedToolExecutor`、
  `ReadFileTool`/`SearchTextTool` 和 `WorkspaceBoundary`，仅 Provider 采用确定性脚本。
- 集成证明 parent 只产生一个 delegation ToolCall、两个 child context 各只有一个 fresh
  task message、read/search evidence hash 存在、child provenance 为 SUBAGENT、parent
  batch ordinal 有序、event 无 task/prompt/content，且 Workspace 前后 bytes 完全一致。
- deny case 在任何 Provider/Tool factory 前停止；递归 ToolCall 得到 `unknown_tool`；一个
  child timeout 不影响 sibling/parent；parent cancellation 取消两个 blocked child 且 active
  count 回到 0。
- 安全审查增加 duplicate child ID 与 duplicate direct task 红灯回归，修复后完整
  Subagent + integration suite 为 100 passed。
- 最终本地 Python 3.12.13 与 3.13.14 均为 1062 passed、10 skipped、91.09% branch
  coverage；skip 均来自当前 Windows 会话缺少 symlink privilege。
- Ruff format/check、strict Pyright、Bandit 与 locked runtime pip-audit 已通过；
  pip-audit 为 `No known vulnerabilities found`。
- 固定 `SOURCE_DATE_EPOCH=1580601600` 的两次构建逐字节一致。wheel
  `mini_code_agent-0.15.0a0-py3-none-any.whl` 为 171435 bytes，SHA-256
  `397a2b1c0348ea801552f641048e9bbd2b60d67015889755037f56729ccf136a`；sdist
  `mini_code_agent-0.15.0a0.tar.gz` 为 522850 bytes，SHA-256
  `b8fc9f59276afcf1481dc2c4272565528e76c3692e8b18b2f04b761361360762`。
- 两个 artifact 在 Python 3.12/3.13 四组 isolated environment 中均通过 stable API/CLI
  smoke，以及 real successful delegation 和 parent cancellation integration。
- PR #5 <https://github.com/linjiayebat/mini-code-agent/pull/5> 的 CI run
  `28537460691` 和 merged-main run `28537586242` 均通过 quality、Ubuntu/Windows x
  Python 3.12/3.13 五个 job。main 的 Windows 两组各 1072 passed；Ubuntu 两组各
  1071 passed、1 个 Windows-path-identity 条件跳过，coverage 为 91.15%-91.26%。
- Annotated tag `v0.15.0-alpha.0` 解引用到 merge commit
  `bba51dd17fb0d0ba8852c7be86c10add7e07e3ad`。非 draft GitHub prerelease
  <https://github.com/linjiayebat/mini-code-agent/releases/tag/v0.15.0-alpha.0> 已发布，
  远端 asset name、size 与 GitHub SHA-256 digest 均与上述本地制品一致。

## M6b Governed Worktree Candidate Notes

- M6b 没有把 M6a analysis profile 改成可写，而是新增独立 implementation profile。Parent
  `delegate_implementation` 的模型输入只有 `task/reason`；repository/state root、Git、
  allowed path、child Tool、固定测试和预算全部由宿主 immutable `WorktreeProfile` 固定。
- lease admission 要求 exact non-bare repository top-level、完整 clean status 和稳定 `HEAD`。
  宿主读取 NUL-delimited index pointers 与 raw Git blobs，并在读取后再次验证 HEAD/status。
- Worktree 用 locked detached `--no-checkout` 创建。`materialize_index()` 只写
  `100644/100755` 普通文件，不复制 ignored/untracked 内容，也不经过 checkout filter；
  symlink/gitlink、大小写 alias、非法路径和所有 tree budget fail closed。
- child 使用 fresh context、`SessionMode.NON_INTERACTIVE` 和
  `TrustSource.SUBAGENT`。exact capability 是 Read/Search/Write/Edit 加可选 fixed
  `run_tests`；Git、arbitrary command、MCP/network、Skills/Hooks、delegation、delete、
  rename、mode change 和 nested approval 都不可用。
- `LedgerRecordingToolExecutor` 只从成功 structured `MutationResult` 生成 immutable ledger
  entry，绑定 ToolCall/path/before-after hash/bytes/lines 并形成 hash chain。模型 summary
  或直接绕过 Tool 的磁盘修改不会自动获得候选身份。
- `CandidateSnapshotter` 独立扫描完整树，与 immutable BaseManifest、ledger、allowed
  prefixes、mode、UTF-8、hash 和资源预算对账。ready candidate 只包含 sorted add/modify、
  bounded diff 与 content-addressed after blobs；未知改动、删除、alias/link、ledger mismatch
  或超限进入 rejected。
- snapshot 先于 cleanup。cleanup 重验 lease/admin dir/worktree registration/candidate
  persistence，再 unlock/remove/prune；删除失败会尝试 relock 并记录 `cleanup_required`。
  cancellation 继续上抛，但先运行带 deadline 的 shielded finalization。
- `adopt_subagent_candidate` 和 `discard_subagent_candidate` 是两个独立 high-risk WRITE
  Tool。preview 先验证 manifest/blob；adoption execute CAS claim `ready -> applying`，
  要求 parent 仍为原 clean HEAD，预检全部目标、stage 同目录 temp，并在首次 replace 前再次
  验证全部 path/hash。
- preflight conflict 产生零 candidate 写入并回到 `ready`。中途 I/O failure 逆序
  rollback：能证明 all-before 才记录 rolled-back，不能证明就进入 `uncertain`。中断后的
  applying recovery 只接受 all-before->ready、all-after->applied、mixed->uncertain。
- 成功 adoption 只把 exact candidate additions/modifications 留在 parent working tree，
  保持 unstaged/uncommitted；Harness 不执行 branch/commit/merge/push/reset/clean。
- Worktree 是 checkout path separation，不是 OS sandbox。Adoption 是 process-serialized、
  rollback-aware 文件协议，不是 power-loss atomic transaction、2PC 或 exactly-once。

## M6b Review Lessons

- 只调用 `git worktree add` 不等于安全 materialization。普通 checkout 的 filter/smudge 和
  repository config 是额外执行面；首版从 index/object bytes 显式构造受限树。
- mutation ledger 是审计线索，不是最终事实。只有把完整实际树、base manifest 和 ledger
  三方对账，才能发现 Tool 外改动、删除或漏记。
- bounded diff 只用于 preview；采用必须读取 content-addressed blob 并重新 hash，不能把
  截断展示文本当 source of truth。
- clean repository 是 point-in-time observation。adoption 必须在 preview 后重新验证
  repo/HEAD/status/path/hash，并在第一处替换前再次全量 revalidate。
- 单文件 `os.replace` 原子不等于多文件原子。公开状态必须区分 conflict、rolled-back 和
  uncertain，不能在 rollback 未证明时返回普通失败。
- cleanup failure 不是日志 warning。仍注册或 admin identity 不明的 Worktree 必须持久化
  `cleanup_required`，后续 operator 才有可诊断入口。
- `asyncio.shield` 不是“忽略取消”。正确语义是 caller 仍收到 `CancelledError`，内部
  finalization 在独立 deadline 内完成或记录 timeout。
- candidate ready、child success、tests passed 和 user approval 是四个不同事实，任何一个
  都不能替代另一个。

## M6b Local Implementation Verification

- 真实 Git 集成覆盖 no-checkout materialization、raw index blob、clean base race、parent
  checkout bytes unchanged、implementation child、candidate persistence、adoption/discard、
  stale conflict、rollback/recovery 和 cancellation finalization。
- adversarial suite 覆盖 hostile filename、Unicode/case alias、link/reparse point、path
  swap、parent HEAD/status race、stale CAS、duplicate ID、Git output truncation/termination、
  lease exhaustion、candidate/blob tampering、rollback failure 与 cleanup race。
- Python 3.13.14 完整质量门禁为 1184 passed、13 skipped，package branch coverage
  88.49%，超过 85% 门槛；Ruff format/check、strict Pyright 与 Bandit 通过，locked
  runtime dependency audit 为 `No known vulnerabilities found`。
- 独立 Python 3.12.13 环境同样为 1184 passed、13 skipped。13 个 Windows skip 包括既有
  symlink privilege 条件与仅在 POSIX 验证 mode/case/FIFO 的场景；Ubuntu CI 将执行对应
  POSIX 路径。
- 最终源码冻结后的 reproducible build、四组 artifact smoke、PR/main CI、tag、Release 与
  远端 digest 仍需完成后再记录，不能把中间构建写成发布成果。
