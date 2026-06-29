# Agent Core

## State Machine

`AgentRuntime` owns a bounded sequence:

1. Build a provider-neutral request from validated messages and tool definitions.
2. Await one normalized provider response under a timeout.
3. Stop on a final response or provider limit.
4. Preflight the entire ToolCall batch for duplicate IDs and the total call budget before
   executing anything.
5. Execute each call through `ToolExecutor` under a timeout.
6. Append exactly one correlated ToolResult per executed ToolCall.
7. Repeat until completion or a deterministic limit.

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

## Boundaries

- Providers translate vendor protocols; they do not execute tools.
- Tools do not call providers or mutate Agent state.
- `AgentRuntime` never imports a vendor SDK.
- M1 snapshots tool definitions and rejects non-read-only tools before the first provider call.
- Each ToolCall name is checked against that snapshot before it can reach the executor.
- Tool arguments and schemas are recursively immutable but serialize back to standard JSON.
- Events contain lifecycle metadata, not prompts, arguments, or secret-bearing raw responses;
  event delivery is best-effort and sink failures cannot abort or replace a run outcome.
- Python task cancellation is recorded and re-raised to preserve structured concurrency.
- Provider and tool exceptions or malformed return values cross the boundary only as public,
  normalized errors.

## Hard Limits

| Limit | Default | Stop behavior |
|---|---:|---|
| Model turns | 8 | `max_turns` |
| Total ToolCalls | 32 | `max_tool_calls` |
| Provider request | 60 seconds | `provider_timeout` |
| Tool execution | 30 seconds | Correlated error ToolResult |

Every limit is validated at construction and has an upper bound.

ToolCall batches are atomic at the validation boundary: if any call duplicates an ID or would
exceed the remaining budget, no call in that batch executes.

## M1 Non-goals

- Real Anthropic/OpenAI adapters.
- Workspace file access.
- Permission approval.
- Persistence, checkpointing, retry scheduling, and context compression.
