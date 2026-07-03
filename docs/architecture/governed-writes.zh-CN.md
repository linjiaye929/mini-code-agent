# 受治理文件写入

[English](governed-writes.md) | [简体中文](governed-writes.zh-CN.md)

## 执行流程

```text
Model ToolCall
    |
    v
ToolRegistry.validate
    |-- known tool?
    `-- Draft 2020-12 arguments valid?
    |
    v
WriteFileTool / EditFileTool.preview
    |-- Workspace path and text policy
    |-- current SHA-256 precondition
    |-- unique edit match
    `-- bounded unified diff
    |
    v
PolicyEngine
    |-- allow: continue
    |-- ask: interactive approval only
    `-- deny: correlated permission error
    |
    v
ToolRegistry.execute
    |-- repeat argument and workspace validation
    |-- repeat SHA-256 precondition
    `-- same-directory atomic mutation
    |
    v
Correlated ToolResult with relative path and hashes
```

模型可以请求动作，但不能批准自己的动作。Policy Rule 和 Approval Handler 是 Prompt
之外的受信任 Application Dependency。

## Policy 模型

Rule 不可变，并按 Tuple 顺序评估；第一个完整匹配生效。Rule 可以匹配 Tool Name、
Side Effect、Risk、所有 Resource Path、Session Mode 和 Trust Source。

| Side Effect | 默认决策 |
|---|---|
| Read-only | `allow` |
| Write | `ask` |
| Execute | `deny` |
| Network | `deny` |

Non-interactive Mode 中的 `ask` 会直接拒绝，不调用 Approval Handler。非法参数和非法
Preview 会在审批前失败。

## 审批 Contract

Approval Request 包含：

- Tool Name、Side-effect Class 和 Risk Level；
- 静态有界 Summary 与模型提供的有界 Reason；
- Workspace-relative Resource Path；
- 最多 32,768 字符的 Unified Diff；
- 匹配的 Policy Rule ID 和静态 Rationale。

Approval 的含义是“在当前前置条件下允许这一项已校验动作”。它不代表生成代码正确或安全，
也不构成 OS 沙箱。

## 写入语义

`write_file` 有两种模式：

- 没有 `expected_sha256`：只允许创建新文件；
- 提供精确 `expected_sha256`：只有现有 Raw Bytes 仍匹配时才允许替换。

`edit_file` 必须携带 `read_file` 返回的 SHA-256。非空 `old_text` 必须只出现一次，
且 Replacement 必须真正改变文件。零匹配、多匹配、No-op Edit 或 Stale Snapshot
都不会产生 Mutation。

两个 Tool 都把所有 Filesystem Access 委托给 `WorkspaceBoundary`。成功结果包含 Relative
Path、Create/Replace Mode、Before/After Hash、Byte/Line Count 和 Bounded Diff。

## 原子性与并发

Boundary 在目标目录创建 Temporary File，Flush 并 `fsync` 内容，替换时保留已有 Permission
Bits，重新检查 Precondition，然后使用：

- `os.link` 发布 Create-only 文件；
- `os.replace` 执行 Replacement。

所有可观察失败路径都会删除 Temporary File。这可以避免目标文件出现部分内容，并检测普通
Stale Read，但它不是通用 Filesystem Compare-and-swap：其他进程仍可能在最后检查与
`os.replace` 之间替换文件。对抗恶意并发 Writer 需要 OS 级隔离或平台相关的
Descriptor-relative API。

## Java 与 Flink 类比

| 现有经验 | Mini CodeAgent 概念 |
|---|---|
| Spring Interceptor / Servlet Filter | 包裹 Tool Dispatch 的 `GovernedToolExecutor` |
| Spring Security Decision Manager | 有序 `PolicyEngine` Rule |
| Service 前 Bean Validation | JSON Schema + Pydantic Argument Validation |
| JPA `@Version` Optimistic Lock | Raw-byte SHA-256 Write Precondition |
| Transaction Commit Conflict | Stale Snapshot 返回 `conflict` 且零写入 |
| Temporary Table 后 Atomic Publish | 同目录 Temporary File + `os.replace` |
| Flink Checkpoint Barrier | Read Hash 标识 Edit 所依据的 State Snapshot |
| Exactly-once Sink Precondition | Create-only Publish + Correlated ToolCall Result |

这些只是概念类比。Filesystem Replacement 不是数据库事务，不提供 Rollback、Isolation
或分布式 Exactly-once 语义。
