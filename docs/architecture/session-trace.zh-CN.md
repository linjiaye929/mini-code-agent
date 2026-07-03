# 版本化 Session 与追加式 Trace

[English](session-trace.md) | [简体中文](session-trace.zh-CN.md)

## 目的

M3b 在进程退出后继续保存有界 Agent Lifecycle Metadata，提供：

- 版本化 Session Record；
- Run Lifecycle Projection；
- Append-only Typed Trace Event；
- Per-session Sequence 与 SHA-256 Chain Verification；
- 向 `AgentRuntime` 注入 Journal 后的 Required Persistence Semantics。

M3b 不保存 Prompt、Message、ToolCall Argument、ToolResult、Patch 或 Command Output，
也不能恢复 Run。Checkpoint/Resume 与 Replay Prevention 属于 M3c。

## 组合

```python
from pathlib import Path

from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.persistence import SqliteSessionTraceStore

with SqliteSessionTraceStore(Path("agent-state.db")) as store:
    session = store.create_session()
    runtime = AgentRuntime(
        provider,
        tools,
        journal=store.journal(session.session_id),
    )
    result = await runtime.run(user_prompt="Inspect the project.")
    verification = store.verify_trace(session.session_id)
```

现有 `events` Sink 仍是 Best-effort UI/Telemetry Output。`journal` 独立存在：一旦提供，
Append Failure 会使 Agent 以 `PERSISTENCE_ERROR` 停止。

## SQLite Schema

Schema Version 1 使用三张表：

| Table | 职责 |
|---|---|
| `sessions` | Status、Last Run、Event Count、Next Sequence、Trace Head |
| `runs` | Start/Stop、Stop Reason、Turn、ToolCall、Cumulative Token Usage |
| `trace_events` | Canonical Event JSON、Sequence、Previous/Current SHA-256 |

`PRAGMA user_version` 是 Database Format Version。Connection 启用 Foreign Key、
`journal_mode=WAL`、`synchronous=FULL` 和有界 Busy Timeout。

每次 Append 在 `BEGIN IMMEDIATE` 下执行：

1. 读取并校验 Session Counter/Head；
2. 检查精确重复 `event_id`；
3. 执行 Payload 与 Session Event Limit；
4. 校验 Run Transition 和 Event Timestamp Order；
5. 更新 Run/Session Projection；
6. 插入一条 Trace Row；
7. 推进 Session Counter/Head；
8. Commit。

任意失败都会同时回滚 Projection 和 Trace Insert。Store 使用 Parameterized SQL，并从
公开错误中移除 Database/Path/Payload Detail。

## Event 生命周期

一次成功的单 Tool Run 记录：

```text
RunStarted
ModelStarted
ModelCompleted(tool_call)
ToolStarted
ToolCompleted
ModelStarted
ModelCompleted(stop)
RunStopped
```

调用 Executor 前必须记录 `ToolStarted`。如果 Tool 执行成功但 `ToolCompleted` 无法持久化，
Durable State 会停留在 Started-only，Runtime 在后续工作前停止。M3c 必须将该调用视为
Indeterminate，不能自动 Replay。

`RunStopped` 保存 Cumulative Turn、ToolCall Count、Input Token 和 Output Token。
如果进程在 RunStopped 前 Crash，Run 与 Session 保持 Active；M3b 不猜测 Terminal State。

## 幂等性与完整性

每个 Event 都有生成的 `event_id`。在相同 Session 中重新追加相同 ID 和完全一致的
Canonical Payload 是 No-op；使用不同内容或 Session 复用 ID 返回 `event_conflict`。

Per-session Sequence 从 1 开始：

```text
current_hash = SHA256(canonical_json(
    schema_version,
    session_id,
    sequence,
    previous_hash,
    event
))
```

`verify_trace` 以有界 Page 读取，通过 Typed `AgentEvent` Union 解析每个 Payload，并检查
Row Metadata、连续 Sequence、Previous Hash、重新计算的 Current Hash、Event Count、
Next Sequence 和 Session Head。Projection 与 Event Page 共享一个 SQLite Read Transaction，
避免并发 Commit 将两个分别有效的 Snapshot 拼成错误 Corruption 结果。

Hash Chain 可以发现意外或低复杂度修改，但没有 Authentication 或 Signature，也不防能够
同时重写 Database 和所有 Hash 的攻击者。

## 限制与 Secret 边界

默认值：

- 每个 Event 最多 64 KiB Canonical JSON；
- 每个 Session 最多 100,000 Event；
- 每个 Query/Verification Page 最多 1,000 Row；
- SQLite Busy Timeout 250 ms。

这些都是有界、不可变 Pydantic Setting。非法 Query 会失败，而不是 Clamp。

Agent Event 只含 Lifecycle Metadata。Prompt、Argument、Result、Diff 和 Command Output
不会进入 M3b Trace。配置的 Secret Value 会在 Hash 和 SQL Binding 前从有界
`RunStopped.error` 中清除；未知 Secret 无法自动发现。

## 失败语义

- Required Journal Write 失败：使用静态 `PERSISTENCE_ERROR` 停止，不执行后续工作。
- Best-effort Observer 失败：不改变流程。
- 发生 Cancellation：尝试记录一次 RunStopped，然后无论持久化是否失败都重新抛出。
- Database Busy 超过配置 Timeout：失败关闭，不无限重试。
- Existing Schema 比当前支持版本新：拒绝，不迁移或降级。
- Trace Row/Projection/Hash 非法：返回静态 `trace_corrupt`。

## 验证证据

测试覆盖 Schema Reopen、WAL/Foreign Key、Exact Limit、Deterministic Ordering、Idempotency、
Conflicting ID、Invalid Transition、Cross-session Ownership、Lock Timeout、Transactional
Rollback、Typed Read、四种 Corruption、Configured Secret Scan、Required 与 Best-effort
Runtime Behavior、Cancellation 和真实受治理文件写入。

## 非承诺

- 没有 Checkpoint、Message Snapshot、Resume 或 Side-effect Replay。
- 没有用于大 Payload 的 JSONL/Object Storage。
- 没有 At-rest Encryption、Signed Audit Log、Remote Database、Replication 或 Distributed Writer。
- 不提供 External Side Effect 的 Exactly-once。
