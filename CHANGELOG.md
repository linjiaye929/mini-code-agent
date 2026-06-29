# Changelog

All notable changes follow Keep a Changelog. Versions follow Semantic Versioning.

## [Unreleased]

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
