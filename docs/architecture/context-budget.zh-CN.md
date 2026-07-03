# 确定性 Context Budget

[English](context-budget.md) | [简体中文](context-budget.zh-CN.md)

## 目的

每个 Provider Request 都经过 `ContextManager`。它在 Network I/O 前限制请求，但不修改
Runtime Transcript、不调用模型，也不虚构 Summary。

M3a 是 Request Admission Control，不是 Durable Memory、Checkpoint，也不是精确的厂商
Token Counting。

## 请求流程

```text
AgentRuntime full transcript
    |
    v
ContextManager.prepare(system, messages, tools)
    |-- validate transcript correlation
    |-- estimate complete request
    |-- pin original goal
    |-- group ToolCall + ToolResult atomically
    |-- pin side-effecting and unknown-tool exchanges
    |-- require newest completed unit
    |-- add recent optional read-only units while they fit
    |-- add a bounded omission marker when needed
    v
ContextWindow
    |-- selected provider messages
    |-- before/after estimates
    |-- omitted counts
    |-- full-transcript SHA-256
    v
ModelRequest
```

`AgentRuntime` 保留完整 In-memory Transcript，并在 `AgentResult` 中返回。Provider Adapter
只接收选中的 `ContextWindow`。

## 估算

`Utf8TokenEstimator` 把完整 Request Shape 序列化为 Canonical Compact JSON，计算 UTF-8
Byte，并加上固定 Request、Message 和 Tool Framing。默认限制：

- 32,768 个 Estimated Context Unit；
- 为 Output 预留 4,096 Unit；
- Request 可使用 28,672 Unit；
- Omission Marker 最多 500 字符。

Estimator 会故意提高 Multi-byte Text 权重，以保证在支持的 Provider 间保持确定性。
它不是厂商 Tokenizer，不能保证 Provider 一定接受请求。Provider-specific Estimator
可以实现 `TokenEstimator` 并注入，不需要修改 Runtime。

## 原子单元与保留规则

第一条 User Message 是固定 Goal。每个 Assistant ToolCall Batch 与紧随其后的 User
ToolResult Batch 组成不可拆分单元。Call/Result ID 必须唯一且集合相等。非法或不完整
Exchange 会在 Provider I/O 前失败。

保留规则是确定性的：

1. 完整 Transcript 能放入预算时原样返回。
2. 永远保留原始 Goal 和最新 Completed Unit。
3. 保留包含 Write、Execute、Network、Mixed 或 Unknown Tool 的全部 Completed Exchange。
4. Standalone Message 和全部只读 Exchange 可选。
5. 从最新的连续 Optional Suffix 开始添加，直到完整 Candidate 无法容纳。
6. Selected Unit 按原始 Transcript 顺序输出。
7. Goal、Newest Unit 或 Required Pinned History 无法容纳时失败关闭。

固定已完成 Side Effect 很重要：删除其证据可能让模型使用新 ToolCall ID 重复动作。
Unknown Tool 也需要固定，因为 Registry 变化后无法证明旧调用只读。

M3a 只降低 Replay Risk，不提供 Durable Exactly-once。M3c 负责跨进程故障的
Checkpoint/Resume 与 Replay Prevention。

## 省略证据

省略 Optional History 时，静态 System Prompt Marker 记录：

- Omitted Message Count；
- Omitted Tool Exchange Count；
- Canonical Full Transcript SHA-256；
- 明确说明省略细节不可用且不得猜测。

同一组有界 Metadata 通过 `ContextCompacted` 发出。Marker 和 Event 不复制原始省略内容、
Arguments、Path 或 Error。

SHA-256 只是 Identity/Equality Fingerprint，不是 Encryption、Authentication、Secret
Redaction、Persisted Transcript，也不能证明省略事实可恢复。

## 失败 Contract

内部错误区分：

- `invalid_transcript`；
- `fixed_content_too_large`；
- `latest_exchange_too_large`；
- `pinned_history_too_large`；
- `window_build_failed`。

Runtime 把它们统一映射为带静态公开文本的 `StopReason.CONTEXT_LIMIT`，不再调用 Provider，
也不暴露 Transcript Content 或 Estimator Exception。

## 验证边界

测试覆盖精确 Budget Boundary、Unicode 与 Tool Schema Estimation、并行 ToolCall Correlation、
Atomic Retention、Side-effect/Unknown-tool Pinning、Marker Bound、Deterministic Fingerprint、
Provider 调用前失败、完整 Result Transcript Ownership 和 Event Sink Isolation。

M3a 不承诺：

- 精确厂商 Token 节省量；
- Semantic Summary，或保证保留被省略只读历史中的事实；
- Process Exit 后持久化；
- Authenticated Trace Integrity；
- Crash/Resume 后阻止 Side-effect Replay。
