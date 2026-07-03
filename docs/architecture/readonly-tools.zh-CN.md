# 只读 Workspace Tools

[English](readonly-tools.md) | [简体中文](readonly-tools.zh-CN.md)

## 数据流

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

## 职责归属

| 组件 | 负责 | 不负责 |
|---|---|---|
| `AgentRuntime` | Turn、ToolCall Budget、Timeout、Correlation Flow | Filesystem 与 JSON Schema |
| `ToolRegistry` | Registration、Schema Validation、Dispatch、Result Contract | Workspace Path Policy |
| `WorkspaceBoundary` | Path、Link、Type、Size、Encoding、Traversal | Tool Description 与 Agent State |
| `ReadFileTool` | Line Window 与结构化 Read Output | 直接 File I/O |
| `SearchTextTool` | Literal Match、Glob Filter、Preview、Result Budget | Directory Walking 实现 |

## 默认限制

| 限制 | 默认值 | 硬上限 |
|---|---:|---:|
| File Bytes | 1 MiB | 16 MiB |
| Path Characters | 1,024 | 1,024 |
| Traversed Files | 10,000 | 100,000 |
| Traversed Bytes | 64 MiB | 256 MiB |
| Search Results | 200 | 10,000 |
| Search Depth | 32 | 64 |
| Search Line Characters | 20,000 | 100,000 |
| Preview Characters | 500 | 2,000 |
| Registry Result Characters | 8 MiB | 16 MiB |
| `read_file` Returned Lines | 200 | 2,000 |

单次调用的 `max_results` 只能缩小配置的 Search Result Limit。

## 错误边界

Workspace Error 使用稳定 Code：

```text
invalid_path       outside_workspace    link_traversal
not_found          wrong_file_type      too_large
binary_file        invalid_encoding     traversal_budget
```

Tool Registry 另外提供：

```text
unknown_tool       invalid_arguments    tool_failed
invalid_tool_result                    tool_result_too_large
```

Message 是静态且安全的，不包含 Absolute Root、File Content、Model Arguments、
Schema Internal、Executor Exception 或 Stack Trace。

## Read Result

`read_file` 返回紧凑 JSON：

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

即使只返回一个 Line Window，SHA-256 也覆盖完整原始 File Bytes。它是 `edit_file`
和替换已有文件的 `write_file` 所要求的 Optimistic Concurrency Token。Line Ending
不会标准化。越过 EOF 的读取返回空 Content 和真实 Total Line Count。

## Search Result

`search_text` 按确定性的 Path/Line/Column 顺序返回：

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

Binary、非法 UTF-8 和单文件超限会计入 Skipped。结构性 Workspace Error、Link、
Special File 和 Traversal Budget Failure 会直接返回错误，而不是静默削弱边界。

Read 与 Search 使用 `asyncio.to_thread` 执行有界 File I/O，避免本地磁盘操作阻塞
Provider Streaming、Timeout Delivery 或 Event Loop 中的其他 Task。取消 Await 不能强制
终止 Python Worker Thread；残留操作只读，并继续受 File/Traversal/Result Limit 约束。
