# Changelog

All notable changes follow Keep a Changelog. Versions follow Semantic Versioning.

## [Unreleased]

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
