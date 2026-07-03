# Provider Adapters

[English](provider-adapters.md) | [简体中文](provider-adapters.zh-CN.md)

## Boundary

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

Agent Runtime imports only `ModelProvider`. Concrete adapters import domain types and translate
at the edge; they never execute tools, retry model calls, or mutate runtime state.

## Message Conversion

| Domain concept | Anthropic Messages | OpenAI-compatible Chat Completions |
|---|---|---|
| System prompt | top-level `system` | first `system` message |
| User text | user `text` content block | `user.content` string |
| Assistant text | assistant `text` block | `assistant.content` |
| Tool definition | `name`, `description`, `input_schema` | function tool with `parameters` |
| Tool call | assistant `tool_use` block | `assistant.tool_calls[]` |
| Tool arguments | JSON object | JSON-encoded string |
| Tool result | user `tool_result` block | separate `tool` role message |
| Tool correlation | `tool_use_id` | `tool_call_id` |

Anthropic requires tool results to appear first in the next user content array. Chat Completions
requires one `tool` message per result. The domain model allows a mixed user message, so each
adapter expands or orders it to satisfy its wire contract.

Chat Completions has no dedicated `is_error` field. Successful tool content is sent unchanged;
failed results are encoded as a compact `{"content": "...", "is_error": true}` envelope so the
model does not lose the domain error signal.

## Response Conversion

| Domain finish reason | Anthropic | OpenAI-compatible |
|---|---|---|
| `stop` | `end_turn`, `stop_sequence` | `stop` |
| `tool_call` | `tool_use` | `tool_calls` |
| `max_tokens` | `max_tokens`, `model_context_window_exceeded` | `length` |
| `content_filter` | `refusal` | `content_filter` |

Usage is normalized to input and output tokens. Anthropic uses `input_tokens/output_tokens`;
Chat Completions uses `prompt_tokens/completion_tokens`. Compatible servers may omit usage, in
which case the adapter returns zero rather than inventing a value.

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

Every block index is unique and bounded. Tool IDs are unique. A delta must match the type of its
open block, all blocks must close before the message delta, and indexes must be contiguous before
completion.

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

The parser caches tool ID and name by index because later chunks normally omit both. If a later
chunk changes metadata, indexes have gaps, arguments are not a JSON object, or `[DONE]` is
missing, the stream fails without emitting `ResponseCompleted`.

## Resource Ownership

`ProviderHttpTransport` owns an internally created `httpx.AsyncClient` and closes it through
`aclose()`. An injected client is borrowed and remains caller-owned. This supports application
lifecycles and deterministic `MockTransport` tests without accidental double-close behavior.

## Public Failure Semantics

Provider bodies and transport exception strings are untrusted and may contain secrets. Public
errors therefore contain only a normalized code, a static safe message, and retryability.
Request IDs are accepted only from known headers and truncated to 128 characters.

Provider URLs require HTTPS. Plain HTTP is accepted only for `localhost`, `127.0.0.1`, and `::1`
so a local model server remains usable without allowing API keys over remote cleartext links.

The adapter does not retry. A future retry policy must consume normalized errors and enforce a
total attempt/time/cost budget at the orchestration layer.

## Current Scope

Implemented:

- Anthropic and OpenAI-compatible non-streaming completion.
- Text and parallel client-tool calls.
- SSE text/tool deltas and completed responses.
- Usage, request ID, finish reason, and error normalization.
- Bounded transport and secret-safe failures.

Not implemented in M1b:

- OpenAI Responses API.
- Anthropic thinking, server tools, and `pause_turn`.
- Audio, image, citation, refusal-detail, or reasoning content preservation.
- Automatic retry or live credentialed CI.
