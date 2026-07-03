# Provider 适配器

[English](provider-adapters.md) | [简体中文](provider-adapters.zh-CN.md)

## 边界

```text
AgentRuntime
    |
    | ModelRequest / ModelResponse / ProviderStreamEvent
    v
ModelProvider protocol
    |
    +-- AnthropicProvider ----------> POST /v1/messages
    |
    `-- OpenAICompatibleProvider ---> POST /v1/chat/completions
             |
             v
      ProviderHttpTransport
      HTTP timeout + body/SSE limits + safe errors
```

Agent Runtime 只导入 `ModelProvider`。具体 Adapter 在边缘导入 Domain Type 并翻译协议；
它们不执行 Tool、不重试模型请求，也不修改 Runtime State。

## 消息转换

| Domain 概念 | Anthropic Messages | OpenAI-compatible Chat Completions |
|---|---|---|
| System Prompt | 顶层 `system` | 第一条 `system` Message |
| User Text | user `text` Content Block | `user.content` String |
| Assistant Text | assistant `text` Block | `assistant.content` |
| Tool Definition | `name`、`description`、`input_schema` | 带 `parameters` 的 Function Tool |
| Tool Call | assistant `tool_use` Block | `assistant.tool_calls[]` |
| Tool Arguments | JSON Object | JSON 编码 String |
| Tool Result | user `tool_result` Block | 单独的 `tool` Role Message |
| Tool Correlation | `tool_use_id` | `tool_call_id` |

Anthropic 要求 Tool Result 位于下一条 User Content Array 的最前面；Chat Completions
要求每个 Result 使用一条 `tool` Message。Domain Model 允许混合 User Message，因此各
Adapter 会按自身 Wire Contract 展开或排序。

Chat Completions 没有专用 `is_error` 字段。成功内容原样发送；失败结果编码为紧凑的
`{"content": "...", "is_error": true}`，避免丢失 Domain Error Signal。

## 响应转换

| Domain Finish Reason | Anthropic | OpenAI-compatible |
|---|---|---|
| `stop` | `end_turn`、`stop_sequence` | `stop` |
| `tool_call` | `tool_use` | `tool_calls` |
| `max_tokens` | `max_tokens`、`model_context_window_exceeded` | `length` |
| `content_filter` | `refusal` | `content_filter` |

Usage 统一为 Input/Output Token。Anthropic 使用 `input_tokens/output_tokens`，
Chat Completions 使用 `prompt_tokens/completion_tokens`。兼容服务可能不返回 Usage，
此时 Adapter 返回 0，不虚构数值。

## Anthropic Stream

```text
message_start
    |
    +-- content_block_start(text)
    |       `-- text_delta* --> TextDelta*
    |               `-- content_block_stop
    |
    +-- content_block_start(tool_use: id + name)
    |       `-- input_json_delta* --> ToolCallDelta*
    |               `-- content_block_stop --> parse full JSON object
    |
    `-- message_delta(stop_reason + cumulative usage)
            `-- message_stop
                    `-- validate complete state --> ResponseCompleted
```

每个 Block Index 必须唯一且有界，Tool ID 也必须唯一。Delta 类型必须和已打开 Block
一致；所有 Block 必须先关闭，再出现 Message Delta；完成前 Index 必须连续。

## OpenAI-compatible Stream

```text
chat.completion.chunk*
    |
    +-- delta.content ----------------------------> TextDelta
    |
    +-- delta.tool_calls[index]
    |       first: id + name + arguments fragment
    |       later: index + arguments fragment ----> ToolCallDelta
    |
    +-- finish_reason
    |
    `-- optional choices=[] usage chunk
            `-- [DONE]
                    `-- parse all tool JSON --> ResponseCompleted
```

Parser 按 Index 缓存 Tool ID 与 Name，因为后续 Chunk 通常省略二者。如果后续 Chunk
修改 Metadata、Index 有缺口、Arguments 不是 JSON Object，或者缺少 `[DONE]`，
Stream 会失败且不产生 `ResponseCompleted`。

## 资源所有权

`ProviderHttpTransport` 拥有内部创建的 `httpx.AsyncClient`，并通过 `aclose()` 关闭。
注入的 Client 只是借用，仍由调用方管理。这样可以支持应用生命周期和确定性的
`MockTransport` 测试，并避免重复关闭。

## 公开失败语义

Provider Body 和 Transport Exception String 都是不可信输入，可能包含 Secret。公开错误
只包含归一化 Code、静态安全 Message 和 Retryability。Request ID 只接受已知 Header，
并截断到 128 个字符。

Provider URL 必须使用 HTTPS。只有 `localhost`、`127.0.0.1` 和 `::1` 可以使用 HTTP，
这样既支持本地模型服务，又不允许 API Key 通过远程明文链路传输。

Adapter 不自动重试。未来 Retry Policy 必须消费归一化错误，并在 Orchestration Layer
限制总 Attempt、Time 和 Cost Budget。

## 当前范围

已实现：

- Anthropic 与 OpenAI-compatible 非流式 Completion。
- Text 与并行 Client Tool Call。
- SSE Text/Tool Delta 和 Completed Response。
- Usage、Request ID、Finish Reason 和 Error Normalization。
- 有界 Transport 与 Secret-safe Failure。

M1b 未实现：

- OpenAI Responses API。
- Anthropic Thinking、Server Tool 和 `pause_turn`。
- Audio、Image、Citation、Refusal Detail 或 Reasoning Content Preservation。
- 自动 Retry 或带真实凭证的 CI。
