# Changelog

All notable changes follow Keep a Changelog. Versions follow Semantic Versioning.

## [Unreleased]

## [0.16.0-alpha.0] - 2026-07-02

### Added

- Immutable host-owned `WorktreeProfile` and bounded lease/candidate/adoption state models with
  separate repository and state roots, exact implementation profiles, path prefixes, and hard
  tree/content/cleanup limits.
- Byte-safe fixed-argv Git boundary for exact clean repository admission, NUL-delimited index
  pointers, raw object reads, locked detached no-checkout Worktrees, administrative identity,
  bounded process lifecycle, and verified cleanup.
- Raw index materialization for regular `100644`/`100755` files plus immutable base manifests,
  successful structured-mutation hash chains, complete-tree reconciliation, bounded diffs,
  content-addressed candidate blobs, and canonical manifest hashes.
- `delegate_implementation` with model-visible `task/reason` only, fresh non-interactive
  implementation children, exact SUBAGENT-provenance Read/Search/Write/Edit Tools, optional
  host-fixed tests, independent snapshot, and cancellation-safe finalization.
- Separate high-risk `adopt_subagent_candidate` and `discard_subagent_candidate` Tools with
  verified previews, ready/applying claims, clean-base/path/hash revalidation, canonical apply,
  rollback, interrupted-state recovery, and uncertain-state evidence.
- Real-Git implementation/adoption integration and adversarial tests for hostile paths, aliases,
  links, races, tampering, output/process limits, lease exhaustion, cancellation, cleanup, and
  rollback failure.
- M6b architecture, threat-model, ADR, learning, and resume documentation.

### Changed

- Public package exports and installed-package smoke now include the governed Worktree profile,
  runner, candidate, adoption, and discard APIs.
- M6a analysis profiles remain read-only; implementation is a separate profile and parent Tool
  rather than a capability upgrade.

### Security

- Child completion cannot mutate or authorize mutation of the parent checkout. Candidate adoption
  requires a second Policy/approval decision and exact original clean `HEAD`.
- Ordinary checkout is avoided during lease population. Only bounded regular files from verified
  Git index/object bytes are materialized; ignored/untracked files, links, gitlinks, aliases,
  unsupported modes, and over-budget trees fail closed.
- Ready candidates come from independent full-tree/base/ledger reconciliation and verified stored
  blobs, not child summaries or bounded diff text.
- Adoption conflicts write no candidate files. Partial failures are either proven rolled back or
  persisted as uncertain; interrupted applying states are classified as all-before, all-after, or
  mixed before reuse.
- Worktree separation is not OS isolation, and multi-file adoption is not power-loss atomic,
  distributed, exactly-once, or a database two-phase commit.

### Verification

- Local Python 3.12.13 and 3.13.14 each passed 1184 tests with 13 platform/privilege skips.
  Python 3.13 package branch coverage was 88.49%, above the 85% gate. Ruff format/check, strict
  Pyright, Bandit, and locked runtime dependency audit passed.
- Final reproducible artifact hashes, isolated smoke evidence, PR/main CI run IDs, tag commit,
  release URL, and remote asset digests will be recorded after the remaining release gates
  complete.

## [0.15.0-alpha.0] - 2026-07-02

### Added

- Immutable host-owned `SubagentProfile` contracts with exact child Tools, independent Agent/
  batch limits, no recursion, and `TrustSource.SUBAGENT`.
- `SubagentSupervisor` with fresh child contexts, preflight composition, structured
  `asyncio.TaskGroup` concurrency, per-child and outer deadlines, ordered aggregation, and
  cancellation propagation.
- Bounded child/batch result models, canonical SHA-256 projections, ToolResult evidence hashes,
  static failures, and metadata-only lifecycle events.
- One dynamic governed parent analysis Tool per profile, with profile-specific JSON Schema,
  medium-risk preview, canonical ASCII result JSON, and UTF-8 byte budget.
- Real parent/child Agent integration using governed `ReadFileTool`/`SearchTextTool`, including
  Policy deny, non-recursion, sibling timeout isolation, event omission, byte-identical Workspace,
  and parent cancellation.
- M6a architecture, threat-model, ADR, learning, and resume documentation.

### Changed

- Added `TrustSource.SUBAGENT` so child Tool Policy is independently addressable from parent model
  and extension calls.
- The stable installed-package smoke imports the public Subagent profile, supervisor, Tool, and
  builder API.
- Subagent unit tests use a package namespace so Pytest's default import mode can collect the full
  suite alongside existing same-named test modules.

### Security

- Every child ID, Provider, and governed Tool executor is validated before child Provider I/O;
  malformed/duplicate IDs, reused objects, capability drift, non-read-only definitions, and
  non-SUBAGENT provenance fail the complete batch.
- Duplicate, empty, NUL-containing, oversized, or excessive tasks fail before child composition.
- Child sessions are non-interactive, cannot receive a delegation Tool, and cannot convert `ASK`
  into authority.
- Child/batch timeout is isolated, external cancellation is re-raised, and no detached asyncio
  task survives the parent ToolCall.
- Events exclude task/prompt/message/summary/argument/result/exception content; evidence retains
  only bounded metadata and SHA-256.
- In-process children are not an OS sandbox. M6a does not claim Tool-implementation isolation,
  durable parent-child audit, semantic proof from hashes, rollback, or exactly-once behavior.

### Verification

- Local Python 3.12.13 and 3.13.14 each passed 1062 tests with 10 Windows
  symlink-privilege skips and 91.09% branch coverage. Ruff format/check, strict Pyright, Bandit,
  and locked runtime pip-audit passed.
- PR #5 CI run `28537460691` and merged-main CI run `28537586242` passed quality plus
  Ubuntu/Windows on Python 3.12/3.13. On merged main, Windows passed 1072 tests; Ubuntu passed
  1071 with one intentional Windows-path-identity skip. Coverage ranged from 91.15% to 91.26%.
- Repeated fixed-epoch builds produced byte-identical artifacts. The wheel is 171435 bytes with
  SHA-256 `397a2b1c0348ea801552f641048e9bbd2b60d67015889755037f56729ccf136a`;
  the sdist is 522850 bytes with SHA-256
  `b8fc9f59276afcf1481dc2c4272565528e76c3692e8b18b2f04b761361360762`.
- Both artifacts passed isolated stable-API/CLI smoke plus successful delegation and
  parent-cancellation integration on Python 3.12/3.13.
- Annotated tag `v0.15.0-alpha.0` dereferences to merge commit
  `bba51dd17fb0d0ba8852c7be86c10add7e07e3ad`. The non-draft GitHub prerelease at
  <https://github.com/linjiayebat/mini-code-agent/releases/tag/v0.15.0-alpha.0> reports asset
  names, sizes, and SHA-256 digests identical to the locally verified artifacts.

## [0.14.0-alpha.0] - 2026-07-01

### Added

- Host-pinned local MCP stdio profiles with absolute executable/cwd validation, SecretStr
  environment values, independent connection approval, fixed identity, exact Tool grants, and
  lifecycle/content budgets.
- Official stable MCP Python SDK v1 adapter for protocol `2025-11-25`, with dedicated owner-worker
  lifecycle management, bounded snapshots, process-tree shutdown, and discarded stderr.
- Canonical JSON Schema SHA-256 verification for complete non-paginated Tool sets; host-owned
  local aliases, descriptions, side-effect classes, and risk levels.
- MCP `RegisteredTool` adapters with governed ActionPreview, deterministic text/structured JSON
  results, output-schema validation, and static public errors.
- Per-Tool trust provenance in `GovernedToolExecutor`, allowing MCP aliases to reach Hooks and
  Policy as `TrustSource.EXTENSION` while preserving the constructor default for native Tools.
- Real official-SDK stdio/Agent integration proving handshake, call, structured output, shutdown,
  extension deny, independent Tool approval, schema drift rejection, and cross-task close.

### Changed

- Added `mcp>=1.28.1,<2` as a bounded runtime dependency. SDK v2 remains pre-release and is not
  selected.
- `GovernedToolExecutor` accepts an optional copied mapping from registered Tool names to
  `TrustSource`; unknown names and invalid values fail construction.
- MCP unit test filenames are globally unique so Pytest's default import mode can collect the
  complete suite with existing command/skill tests.
- Release builds fix `SOURCE_DATE_EPOCH`, explicitly exclude local workspace/test state from
  sdists, and run an archive-member contract test before artifact smoke tests.

### Security

- MCP commands must be absolute existing executable regular files; command and cwd reject
  symlink/reparse paths and are revalidated immediately before process launch.
- Process approval shows complete argv/cwd and environment names before any server code runs.
  Environment values remain secret, and connection approval never replaces per-Tool Policy or
  approval.
- Protocol/server identity, Tools capability, static Tool list, exact grant set, schema hashes,
  and task mode all fail closed before any alias is published.
- Server instructions, descriptions, titles, annotations, icons, `_meta`, and stderr do not enter
  model-facing definitions or results.
- Results reject image/audio/resource content, non-finite or excessive JSON, oversized text/bytes,
  and successful output-schema mismatches without returning partial success.
- Local stdio processes retain the Agent user's OS authority. M5b does not claim sandboxing,
  executable provenance, package safety, remote MCP/OAuth security, rollback, or exactly-once
  side effects.

### Verification

- Local Python 3.12.13 and 3.13.14 each passed 961 tests with 10 Windows symlink-privilege skips
  and 90.84% branch coverage. Ruff, strict Pyright, Bandit, and locked runtime dependency audit
  passed.
- PR #4 CI run `28501386707` and merged-main CI run `28501782935` passed quality plus
  Ubuntu/Windows on Python 3.12/3.13. On merged main, Windows passed 971 tests; Ubuntu passed 970
  with one intentional Windows-path-identity skip.
- Repeated builds produced byte-identical artifacts. The wheel is 158701 bytes with SHA-256
  `6e224b01fb69eafdc96019c7bd4c7544bce8e61314d52fd77184b78c9d4f4e22`; the sdist is
  466629 bytes with SHA-256
  `b9e36d3d1a828e3f6fd3ef39f23de7d4f669851bddf02c84b3793e14af2881d3`.
- Both artifacts passed isolated install, import, CLI, real MCP connect/call/close, and shutdown
  smoke tests on Python 3.12/3.13. Annotated tag `v0.14.0-alpha.0` dereferences to merge commit
  `1af6a07632abe291ac4adc0ccb04aaa1be5c7d38`; the non-draft GitHub prerelease asset sizes and
  digests match the local artifacts.

## [0.13.0-alpha.0] - 2026-07-01

### Added

- Source-qualified `managed`/`user`/`project` Skill catalog with bounded direct-child discovery,
  conflict quarantine, disabled IDs, derived trust labels, and deterministic descriptors.
- Restricted `SKILL.md` parser for strict UTF-8, bounded YAML frontmatter, duplicate key/alias/
  custom tag rejection, exact Pydantic metadata, non-empty Markdown, and SemVer.
- Fingerprint-required lazy loading that revalidates the root/directory/file hierarchy, open file
  identity, metadata, byte count, and SHA-256 before returning `untrusted_markdown`.
- Read-only `list_skills` and `load_skill` Agent tools with metadata-only discovery and static
  public errors.
- Typed host-registered pre/post Tool Hooks with stable priority/ID ordering, per-Hook timeout,
  cancellation propagation, bounded audit records, and source labels.
- Real Agent integration proving malicious Skill instructions cannot bypass Policy, pre-Hooks
  block before mutation, and post-Hook failures preserve results and later observers.

### Changed

- `GovernedToolExecutor` now runs optional pre-Hooks after ActionGuard and before Policy, then
  post-Hooks after Tool execution.
- `mini_code_agent.policy` lazily exports `GovernedToolExecutor` to keep Hook models and Policy
  contracts free of circular imports.
- Added PyYAML as a runtime dependency and type stubs for strict development checks.

### Security

- Skills remain inert data and cannot register Tools, Hooks, Providers, Policy, or Python modules.
- Cross-source names never shadow each other; duplicate qualified IDs are all quarantined.
- Root, directory-entry, candidate, disabled-ID, file, frontmatter, body, and issue/result sizes
  are bounded.
- A pre-Hook can only continue to ordinary Policy or reduce authority by blocking. Timeout,
  exception, malformed result, and audit failure fail closed.
- Post-Hook failures are isolated after execution and cannot replace or hide the original
  `ToolResult`.
- In-process Hooks are trusted host code, not a sandbox. Repository command/HTTP/prompt/MCP Hooks,
  dynamic imports, supporting Skill files, and durable Hook audit are not implemented.

## [0.12.0-alpha.0] - 2026-07-01

### Added

- Host-controlled `RepairRuntime` with explicit approval, baseline verification, bounded Agent
  attempts, Git evidence validation, fixed Pytest verification, and typed stop reasons.
- Exact existing tracked-file admission through literal top-level Git pathspecs and a
  `RepairActionGuard` that permits reads and exact scoped writes while denying execute/network.
- Canonical failure fingerprints plus independent attempt, elapsed-time, patch-size,
  same-failure, and Worker-prompt budgets.
- `AgentRepairWorker` adapter that gives one governed Agent run one repair attempt without letting
  the model own verification or termination.
- SQLite schema v3 `repair_runs`/`repair_events` lifecycle journal with transactional projections,
  exact idempotency, bounded queries, canonical SHA-256 chains, and trace verification.
- Real Git/Pytest/Agent/SQLite integration coverage for successful repair, pre-policy scope denial,
  dirty-repository rejection, and test-induced mutation detection.

### Changed

- Fixed Pytest execution now uses `python -I -B -m pytest`; `-B` prevents harness test runs from
  creating bytecode cache files in the repository.
- Persistence schema advances from v2 to v3 through sequential transactional migration.
- Sequential v1-to-v2-to-v3 failure preserves the last completed schema version so migration can
  be retried after the v3 fault is removed.
- Session and Repair trace verification read projections and event chains from one SQLite
  transaction snapshot; Resume write contention invalidates the analyzed plan instead of
  surfacing a false corruption/storage failure.
- M4c completes a library-level bounded Repair workflow; CLI composition remains a later surface.

### Security

- Repair starts only from a clean repository and an exact set of existing Git-tracked files.
- Every accepted attempt must leave only ordinary unstaged modifications inside the approved
  scope; staged, untracked, renamed, conflicted, submodule, branch, no-progress, and oversized
  changes stop the session.
- Only complete passing host-owned Pytest evidence establishes success. Model text cannot.
- Public Repair models bound each editable path and reject success reasons without matching
  baseline, attempt, and complete passing test evidence.
- Required persistence precedes Repair work; an interrupted Repair is evidence for inspection and
  is never automatically resumed.
- Repair orchestration is not an OS sandbox. Approved tests and hostile concurrent host processes
  retain the Agent user's authority, and transient change-and-restore cannot be excluded.

## [0.11.0-alpha.0] - 2026-06-30

### Added

- Immutable host-owned Pytest profiles with fixed executable, timeout, failure cap, defaults, and
  trusted plugins.
- Critical-risk `run_tests` Tool for bounded workspace-relative files and directories.
- Typed Pytest process/report statuses, computed case counts, and bounded failure/error
  diagnostics.
- Bounded strict-UTF-8 JUnit parser with explicit missing/invalid/unsafe/too-large outcomes.
- `defusedxml` parsing for the untrusted JUnit boundary.
- Real policy, Pytest subprocess, AgentRuntime, and SQLite Trace integration coverage.

### Changed

- `CommandRunner` accepts constructor-only validated environment overrides for dedicated host
  runners; model command requests still cannot provide environment values.
- Test execution disables ambient Pytest plugin autoload and `.pytest_cache`; required plugins are
  explicit host configuration.
- M4b completes structured test execution and diagnostics. Automatic Repair remains M4c.

### Security

- Execute remains denied by default; interactive test runs require an explicit policy rule and
  independent approval, while non-interactive `ASK` fails closed.
- Model input cannot select the Python executable, arbitrary argv, cwd, environment, timeout,
  plugin, failure cap, or JUnit path.
- Test targets pass WorkspaceBoundary and `--`; node IDs, option-shaped components, duplicates,
  links, and escape paths are rejected.
- Time, combined output, report bytes, test cases, diagnostics, and diagnostic text are bounded.
- JUnit rejects non-regular files, invalid UTF-8, DTD/entities, malformed XML, and contradictory
  outcomes before returning typed data.
- Temporary reports are omitted as result fields, direct path/name echoes are replaced, and files
  are cleaned after success, errors, and cancellation.
- Approval and fixed argv are explicitly not an OS sandbox; approved tests execute repository code
  with the Agent user's authority.

## [0.10.0-alpha.0] - 2026-06-30

### Added

- Typed Git porcelain-v2 status models and NUL-delimited parser.
- Hardened bounded Git client with exact Workspace top-level verification.
- Read-only `git_status` and staged/unstaged `git_diff` Agent tools.

### Security

- Git commands disable paging, optional locks, fsmonitor, external diff, textconv, and submodule
  recursion.
- Output/time/entry/patch limits fail closed without returning partial evidence or raw stderr.
- Real tests prove configured execution extensions do not run and `.git/index` is not modified.
- M4a explicitly excludes all Git mutation and automatic commit operations.

## [0.9.0-alpha.0] - 2026-06-30

### Added

- Stable full-transcript Checkpoints with bounded canonical payloads and integrity hashes.
- SQLite database schema v2 plus transactional v1-to-v2 migration.
- Deterministic Tool contract and bounded Workspace fingerprints.
- Read-only Resume analysis, explicit replay policy, atomic claim, and Runtime restore.

### Changed

- Runtime saves required state before the first Provider request and after complete ToolResult
  batches.
- Resume creates a new Run while preserving transcript, counters, usage, and seen ToolCall IDs.
- `claim_resume` reanalyzes eligibility instead of trusting a caller-constructed plan.

### Security

- Any uncheckpointed write, execute, or network Tool blocks automatic Resume.
- Tool/Workspace drift, stale plans, concurrent claims, corrupt payloads, and failed claim writes
  fail closed without partial lifecycle mutation.
- Checkpoint payloads are bounded plaintext and are explicitly not claimed as encrypted.
- Provider/read-only replay is opt-in and is not an exactly-once guarantee.

## [0.8.0-alpha.0] - 2026-06-30

### Added

- SQLite schema v1 for versioned Session metadata, Run projections, and append-only Trace events.
- UUID event IDs, per-Session sequence allocation, canonical JSON, and SHA-256 hash chains.
- Bounded Session/Run/Trace queries plus complete paged integrity verification.
- `ModelStarted` and `ToolStarted` lifecycle events with cumulative terminal Run metrics.
- Optional required `EventJournal` alongside the existing best-effort `EventSink`.

### Changed

- Runtime stops with `PERSISTENCE_ERROR` when a configured journal cannot append.
- `ToolStarted` is persisted before execution and `ToolCompleted` after execution.
- Cancellation attempts one terminal journal event but still re-raises `CancelledError`.
- SQLite test connections now commit or roll back before deterministic closure.

### Security

- Trace and projections commit or roll back in one `BEGIN IMMEDIATE` transaction.
- SQLite uses WAL, foreign keys, full synchronous writes, parameterized SQL, and bounded busy
  timeout.
- Typed Trace excludes prompts, Tool arguments/results, patches, and command output.
- Configured Secret values are scrubbed from free-form stop errors before hashing and storage.
- Hash-chain integrity is explicitly not claimed as signed, authenticated, or tamper-proof.

## [0.7.0-alpha.0] - 2026-06-29

### Added

- Deterministic provider-neutral context estimator with configurable output reserve.
- Atomic ToolCall/ToolResult history selection and bounded omission markers.
- Typed `ContextCompacted` events with before/after estimates, omitted counts, and transcript
  fingerprints.
- `CONTEXT_LIMIT` runtime stop before provider I/O when required context cannot fit.

### Security

- Completed side-effecting, mixed, and unknown-tool exchanges stay pinned during compaction.
- Pinned-history overflow fails closed instead of erasing action evidence.
- Omission markers, events, and public errors do not copy raw omitted transcript content.
- Transcript fingerprints are documented as evidence identifiers, not secret protection or
  durable replay prevention.

## [0.6.0-alpha.0] - 2026-06-29

### Added

- Critical-risk `run_command` tool with exact argv/cwd/reason approval previews.
- Argv-only async command runner with minimal environment and structured exit results.
- Combined stdout/stderr byte budget, timeout, cancellation, and process-tree cleanup.
- Policy `executable_glob` for narrowing explicit execute rules.

### Security

- Execute remains denied by default and non-interactive `ask` fails closed.
- No command path uses `shell=True`, shell strings, model environment overrides, or stdin.
- Command output, runtime, argv, and cleanup waits have hard limits.
- Pipe-reader failures terminate the process and return static errors without raw exceptions.

## [0.5.0-alpha.0] - 2026-06-29

### Added

- Deterministic allow/ask/deny Policy Engine with risk, resource, session, and trust matching.
- Governed executor with bounded action previews and explicit interactive write approval.
- Conflict-aware `write_file` and unique-match `edit_file` with raw-byte SHA-256 preconditions.
- Same-directory atomic create/replace primitives with bounded unified diff evidence.
- SHA-256 snapshots in `read_file` results for read-modify-write workflows.

### Security

- Agent Runtime rejects side-effecting tools that are not composed behind governed execution.
- Non-interactive `ask` decisions deny without invoking an approval handler.
- Existing writes reject missing or stale hashes; new writes use create-only publication.
- Workspace mutation failures preserve target content, clean temporary files, and expose only
  static errors and workspace-relative paths.

## [0.4.0-alpha.0] - 2026-06-29

### Added

- Cross-platform read-only WorkspaceBoundary with path, link, file type, size, binary, encoding,
  and deterministic traversal policies.
- Draft 2020-12 schema-validating Tool Registry with definition snapshots, correlated failures,
  and global ToolResult limits.
- Bounded `read_file` line windows and deterministic literal `search_text` with Unicode-aware
  columns.
- End-to-end Read/Search ToolCall integration through the unchanged Agent Runtime.

### Security

- Rejected traversal, absolute/drive/UNC, ADS, Windows device, trailing-dot/space, `.git`,
  symlink/junction, and special-file paths.
- Bounded file bytes, traversal files/bytes/depth, search results/line/preview, and registry
  output.
- Normalized workspace and executor failures without absolute paths, content, arguments, or raw
  exceptions.

## [0.3.0-alpha.0] - 2026-06-29

### Added

- Anthropic Messages and OpenAI-compatible Chat Completions adapters.
- Non-streaming and SSE text, parallel ToolCall, usage, finish-reason, and request-ID conversion.
- Bounded HTTP/SSE transport with normalized secret-safe provider errors.
- Credential-free provider wire contracts and unchanged Agent Runtime ToolCall integration tests.

### Security

- Enforced timeout and redirect policy for owned and injected HTTP clients.
- Bounded provider response data and validated base URLs, endpoint paths, and extra headers.
- Rejected malformed/lossy provider responses without exposing raw bodies or exception details.

## [0.2.0-alpha.0] - 2026-06-29

### Added

- M1 provider-neutral message, ToolCall, provider, event, and bounded Agent Runtime contracts.

## [0.1.0-alpha.0] - 2026-06-29

### Added

- Product design, learning map, and resume evidence plan.
- M0 typed package, configuration, structured logging, and diagnostic CLI.
