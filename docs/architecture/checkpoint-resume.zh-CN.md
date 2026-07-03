# Durable Checkpoint 与安全 Resume

[English](checkpoint-resume.md) | [简体中文](checkpoint-resume.zh-CN.md)

## 目的

M3c 持久化稳定的 Agent Input State，并恢复 Interrupted Session，同时避免静默 Replay
可能已经改变外部世界的操作。Trace 回答“发生过什么”，Checkpoint 保存继续运行所需的
Mutable State。

## 稳定 Checkpoint

Runtime 在以下时机保存 Checkpoint：

- `RunStarted` 之后、第一次 Provider Request 之前；
- 每个完整 ToolCall/ToolResult Batch 之后；
- ToolCall Pending 时绝不保存；
- 绝不替代 Terminal `RunStopped`。

每个 Immutable Snapshot 包含 System Prompt、完整 Typed Transcript、Counter、Token Usage、
Seen ToolCall ID、Tool Contract Hash 和 Workspace Hash。稳定 Transcript 要求每个 Assistant
ToolCall Batch 后都有一个 User ToolResult Batch，并且 Ordered ID 完全一致。

## 原子存储

SQLite Schema v2 新增 `checkpoints`。已有 Trace Row 保持 Envelope Version 1，因此
v1-to-v2 Migration 不重写历史 Hash。

`SessionCheckpointJournal.save` 使用一个 `BEGIN IMMEDIATE` Transaction：

1. 校验 Active Source Run 和 Limit；
2. 追加 `CheckpointSaved`；
3. 推进 Session Sequence 与 Hash Head；
4. 将 Canonical Checkpoint JSON 绑定到新 Sequence/Head；
5. 插入 Checkpoint Row。

Event、Projection、Head 和 Payload 一起 Commit 或 Rollback。精确 ID/Payload Retry
是 Idempotent；冲突复用会失败。

## 兼容性

Tool Contract Hash 覆盖排序后的 Name、Description、Input Schema 和 Side-effect Class。
Workspace Hash 覆盖有界、确定性的 Relative Path 和 Regular-file Byte Manifest，Scan
Configuration 也进入 Hash。Symlink、Special File、Replacement Race 和配置的 Count/Byte
Overflow 都会失败关闭。

默认排除 `.git`、`.venv`、`.worktrees`、`__pycache__` 和 `node_modules`。被排除内容
明确不在 Compatibility Claim 内。

## Resume 分析

分析首先验证完整 Trace 和 Checkpoint Payload Hash，然后要求：

- 存在最新可用 Checkpoint；
- Source Run 仍为 Active；
- Tool 与 Workspace Fingerprint 精确一致；
- 明确允许可能的 Provider 或只读 Tool Retry。

Checkpoint Sequence 之后的全部 Event 按有界 Page 读取。任何未进入 Checkpoint 的
`ToolStarted`，只要被归类为 Write、Execute 或 Network，就阻止 Resume，即使后面存在
`ToolCompleted`。Completion 只证明 Runtime 观察到结果，不证明结果已进入 Durable Snapshot。

## Claim

`claim_resume` 不信任调用方提供的 Plan。它使用当前 Compatibility 与 Policy 重新分析，
然后在 `BEGIN IMMEDIATE` 内比较分析时的 Trace Head。

如果未变化，同一 Transaction 会：

- 为 Source Run 追加 `RunStopped(INTERRUPTED)`；
- 为 Resumed Run 追加 `RunStarted`；
- 标记 Checkpoint 已被该 Run Consume。

并发 Claim 通过 SQLite 串行化，只有一个可以 Consume Snapshot。Runtime 恢复 Message
和 Cumulative Counter，为新 Run 写入第一个 Stable Checkpoint，并从下一个 Logical Turn
开始，不重复发出 `RunStarted`。

## 失败行为

| 条件 | 结果 |
|---|---|
| Checkpoint Save 失败 | `PERSISTENCE_ERROR`；不再请求 Provider |
| Tool/Workspace 不同 | `RESUME_INCOMPATIBLE`；零 Mutation |
| Checkpoint 后有 Side Effect | `INDETERMINATE_SIDE_EFFECT`；零 Mutation |
| 不允许 Model/Read Replay | `REPLAY_REQUIRES_APPROVAL`；零 Mutation |
| 分析后 Trace 改变 | `CHECKPOINT_STALE`；零 Mutation |
| Claim Write 失败 | Source/New Run、Trace 和 Consumption 全部回滚 |
| Payload/Trace 损坏 | 静态 Integrity Error，不暴露 Stored Content |

## 保密性与非承诺

Checkpoint JSON 包含 Prompt、Model Text、Tool Argument/Result、Patch 和 Command Output。
M3c 以有界明文保存。数据库应放在模型控制的 Workspace 外，并使用 OS Access Control；
不能把 Event Secret Scrubbing 当成 Checkpoint Encryption。

M3c 不提供：

- Encrypted State 或 Key Management；
- Signed/Authenticated Audit Record；
- Distributed 或 Multi-host Coordination；
- 被阻止 Side Effect 的自动 Reconciliation；
- Provider Billing 或 External Tool Execution 的 Exactly-once；
- OS 沙箱。
