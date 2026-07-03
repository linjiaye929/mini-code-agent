# Agent Core

[English](agent-core.md) | [简体中文](agent-core.zh-CN.md)

## 状态机

`AgentRuntime` 管理一段有界执行序列：

1. 从已校验消息和 Tool Definition 构造 Provider-neutral 请求。
2. 在 Timeout 内等待一个已归一化的 Provider Response。
3. 收到最终回答或达到 Provider Limit 时停止。
4. 执行任何 Tool 前，先检查整批 ToolCall 是否有重复 ID，并检查总调用预算。
5. 每个调用都通过 `ToolExecutor` 在 Timeout 内执行。
6. 每个已执行 ToolCall 只追加一个与其 ID 关联的 ToolResult。
7. 重复以上过程，直到完成或触发确定性上限。

```text
USER_MESSAGE
    |
    v
MODEL_REQUEST -> PROVIDER_ERROR / PROVIDER_TIMEOUT
    |
    v
MODEL_RESPONSE
    |-- stop/max_tokens ----------------------> STOPPED
    |
    `-- tool_call
            |
            v
       ID + BUDGET CHECK ---------------------> STOPPED
            |
            v
       TOOL EXECUTION -> CORRELATED RESULT
            |
            `---------------------------------> MODEL_REQUEST
```

## 边界

- Provider 只负责翻译厂商协议，不执行 Tool。
- Tool 不调用 Provider，也不修改 Agent State。
- `AgentRuntime` 不导入任何厂商 SDK。
- M1 在第一次 Provider 调用前固定 Tool Definition Snapshot，并拒绝非只读 Tool。
- 每个 ToolCall 名称必须存在于 Snapshot 中，才能到达 Executor。
- Tool 参数和 Schema 递归不可变，但可以重新序列化为标准 JSON。
- Event 只包含生命周期 Metadata，不包含 Prompt、参数或可能带 Secret 的原始 Response。
  Event Sink 是 best-effort，Sink 失败不能中止或替换 Run Outcome。
- Python Task Cancellation 会被记录并重新抛出，以保留 Structured Concurrency 语义。
- Provider/Tool Exception 或非法返回值只能以公开、归一化错误跨越边界。

## 硬限制

| 限制 | 默认值 | 停止行为 |
|---|---:|---|
| Model Turn | 8 | `max_turns` |
| ToolCall 总数 | 32 | `max_tool_calls` |
| Provider Request | 60 秒 | `provider_timeout` |
| Tool Execution | 30 秒 | 关联的 Error ToolResult |

所有限制在构造时校验，并且自身也有硬上限。

ToolCall Batch 在校验边界具有原子性：只要存在重复 ID，或者整批会超过剩余预算，
该批次中的所有调用都不会执行。

## M1 非目标

- 真实 Anthropic/OpenAI Adapter。
- Workspace 文件访问。
- 权限审批。
- Persistence、Checkpoint、Retry Scheduling 和 Context Compression。
