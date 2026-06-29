# Changelog

All notable changes follow Keep a Changelog. Versions follow Semantic Versioning.

## [Unreleased]

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
