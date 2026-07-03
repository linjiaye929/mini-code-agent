# Read-only Workspace Tools

[English](readonly-tools.md) | [简体中文](readonly-tools.zh-CN.md)

## Data Flow

```text
Model ToolCall
    |
    v
ToolRegistry
    |-- known definition?
    |-- Draft 2020-12 arguments valid?
    |-- result ID/type/size valid?
    v
ReadFileTool / SearchTextTool
    |
    v
WorkspaceBoundary
    |-- lexical path policy
    |-- link/junction rejection
    |-- strict resolve + relative_to(root)
    |-- regular file + byte/encoding policy
    `-- traversal budgets
    |
    v
Correlated ToolResult with relative paths only
```

## Ownership

| Component | Owns | Does not own |
|---|---|---|
| `AgentRuntime` | turns, ToolCall budget, timeout, correlation flow | filesystem and JSON Schema |
| `ToolRegistry` | registration, schema validation, dispatch, result contract | workspace path policy |
| `WorkspaceBoundary` | path, link, type, size, encoding, traversal | tool descriptions and Agent state |
| `ReadFileTool` | line windows and structured read output | direct file I/O |
| `SearchTextTool` | literal matching, glob filter, previews, result budget | directory walking implementation |

## Default Limits

| Limit | Default | Hard maximum |
|---|---:|---:|
| File bytes | 1 MiB | 16 MiB |
| Path characters | 1,024 | 1,024 |
| Traversed files | 10,000 | 100,000 |
| Traversed bytes | 64 MiB | 256 MiB |
| Search results | 200 | 10,000 |
| Search depth | 32 | 64 |
| Search line characters | 20,000 | 100,000 |
| Preview characters | 500 | 2,000 |
| Registry result characters | 8 MiB | 16 MiB |
| `read_file` returned lines | 200 | 2,000 |

Call-level `max_results` can only reduce the configured search result limit.

## Error Boundary

Workspace errors use stable codes:

```text
invalid_path       outside_workspace    link_traversal
not_found          wrong_file_type      too_large
binary_file        invalid_encoding     traversal_budget
```

Tool Registry adds:

```text
unknown_tool       invalid_arguments    tool_failed
invalid_tool_result                    tool_result_too_large
```

Messages are static and safe. They do not include absolute roots, file content, model arguments,
schema internals, executor exceptions, or stack traces.

## Read Result

`read_file` returns compact JSON:

```json
{
  "path": "src/app.py",
  "start_line": 1,
  "end_line": 120,
  "total_lines": 240,
  "sha256": "64-lowercase-hex-characters",
  "content": "...",
  "truncated": true
}
```

The SHA-256 covers the complete raw file bytes, even when a line window is returned. It is the
optimistic-concurrency token required by `edit_file` and existing-file `write_file`. Line endings
are not normalized. Reading past EOF returns empty content and the real total line count.

## Search Result

`search_text` returns deterministic path/line/column order:

```json
{
  "query": "needle",
  "matches": [
    {"path": "src/app.py", "line": 3, "column": 8, "preview": "..."}
  ],
  "files_scanned": 4,
  "skipped_files": 1,
  "truncated": false
}
```

Binary, malformed UTF-8, and individually oversized files are counted as skipped. Structural
workspace errors, links, special files, and traversal-budget failures return an error instead of
silently weakening the boundary.

Read and Search offload bounded filesystem work with `asyncio.to_thread`, so local disk I/O does
not block provider streaming, timeout delivery, or other event-loop tasks. Cancelling the await
cannot forcibly terminate a Python worker thread; the remaining work is read-only and still
bounded by file/traversal/result limits.
