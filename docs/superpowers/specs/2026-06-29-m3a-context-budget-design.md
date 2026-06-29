# M3a Deterministic Context Budget Design

## Goal

Bound every provider request with a deterministic, provider-neutral context window while
preserving the original user goal, the newest complete tool exchange, correlation integrity, and
an auditable record of any omitted history.

## Scope Decomposition

M3 is split because context selection, durable storage, and crash recovery have different failure
and consistency models:

- M3a: request estimation, atomic history selection, context-limit stop, compaction events;
- M3b: versioned Session and append-only Trace persistence;
- M3c: Checkpoint/Resume, workspace compatibility, and side-effect replay prevention.

This specification covers M3a only. It does not claim durable memory or crash recovery.

## Approaches Considered

### 1. Deterministic recent-window compaction (selected)

Keep full in-memory transcript ownership in `AgentRuntime`, but construct each provider request
through a pure `ContextManager`. Preserve pinned content and newest complete interaction units;
drop older units newest-first until the configured estimate fits. This is predictable, testable,
and cannot invent facts.

### 2. LLM-generated rolling summary

An LLM summary can retain semantic facts with fewer tokens, but adds cost, recursive failure,
prompt-injection exposure, provenance questions, and hallucination risk. It becomes an optional
validated compactor only after Trace and artifact persistence exist.

### 3. Provider-specific tokenizer as a mandatory dependency

Exact vendor tokenizers improve estimates but add model/version coupling and may not exist for
OpenAI-compatible endpoints. M3a defines a `TokenEstimator` protocol and ships a conservative
UTF-8 estimator. Provider-specific estimators can be injected later.

## Architecture

```text
Full in-memory transcript
    |
    v
ContextManager.prepare
    |-- estimate system prompt + tool definitions
    |-- pin first user goal
    |-- group ToolCall + ToolResult atomically
    |-- require newest complete exchange
    |-- retain newer optional units within budget
    |-- append static compaction marker metadata
    v
ContextWindow
    |-- bounded system prompt
    |-- selected messages
    |-- before/after estimates
    |-- omitted counts + transcript fingerprint
    v
ModelRequest
```

`AgentRuntime` owns the full transcript and cumulative model usage. `ContextManager` owns only
request selection. Provider adapters remain unaware of compaction.

## Estimation Contract

`TokenEstimator` exposes one deterministic method over a complete request shape: system prompt,
messages, and immutable tool definitions. `Utf8TokenEstimator` estimates text from UTF-8 bytes,
JSON structural overhead, message/content tags, ToolCall arguments, and Tool schemas.

Default limits:

- maximum estimated context: 32,768 tokens;
- reserved output: 4,096 tokens;
- usable estimated request budget: 28,672 tokens;
- hard configurable maximum context: 1,000,000 tokens;
- compaction marker: at most 500 characters.

This is an estimate, not a vendor tokenizer guarantee. A provider can still reject a request.
Observed provider usage is accounting evidence for completed calls, not a predictor for the next
request.

## Atomic History Units

The runtime transcript follows:

```text
user goal
assistant ToolCall batch
user ToolResult batch
assistant ToolCall batch
user ToolResult batch
...
```

The first user message is always pinned. An assistant message containing ToolCalls and its
immediately following user ToolResults form one atomic unit. IDs must match as sets in original
order-independent correlation. A request never contains one side without the other.

Other standalone messages form one-message units. The newest completed unit is required. Older
units are optional and retained newest-first. Selected units are emitted in original order.

If transcript structure is malformed, fixed content exceeds the usable budget, the newest unit
cannot fit, or the marker itself prevents a valid window, preparation returns a typed
`ContextError`. The runtime stops with `StopReason.CONTEXT_LIMIT` before calling the provider.

## Compaction Marker and Evidence

When history is omitted, the manager appends a static bounded note to the system prompt containing:

- number of omitted messages and tool exchanges;
- SHA-256 fingerprint of the complete canonical transcript.

The marker explicitly says details are unavailable and must not be guessed. It does not contain
raw omitted content, file paths, tool arguments, errors, or secrets.

`ContextCompacted` event records run ID, turn, before/after estimates, omitted counts, and
fingerprint. Keeping estimates out of the marker avoids circular re-estimation. Event sink
failures remain best effort and cannot alter selection.

## Runtime Integration

Before each provider call:

1. `AgentRuntime` passes the full transcript, system prompt, and tool definitions to
   `ContextManager`.
2. On success, it builds `ModelRequest` from the returned window.
3. If content was omitted, it publishes one `ContextCompacted`.
4. On `ContextError`, it returns a normal `AgentResult` with `CONTEXT_LIMIT`, full transcript,
   no additional provider call, and a static public message.

The context manager is injected. The default runtime uses bounded deterministic behavior;
tests may inject a custom estimator or manager.

## Security and Correctness Invariants

1. User goal and system prompt are never silently truncated.
2. ToolCall and ToolResult messages are never split.
3. Compaction never executes tools or calls a model.
4. Omitted content is not copied into marker/event/error text.
5. Canonical hashing is deterministic and contains no absolute host metadata.
6. Full transcript remains available to runtime result and future persistence.
7. Every provider request passes through the manager.
8. Invalid transcript or fixed-content overflow fails before provider I/O.
9. Estimates, limits, omitted counts, and marker length are bounded Pydantic fields.
10. No claim of exact vendor token counting is made.

## Error Handling

Typed internal codes:

- `invalid_transcript`;
- `fixed_content_too_large`;
- `latest_exchange_too_large`;
- `window_build_failed`.

Runtime exposes one static context-limit message and does not leak transcript content or
estimator exceptions.

## Test Strategy

Unit tests cover estimator determinism, UTF-8/code/JSON/tool-schema accounting, limits,
ToolCall/ToolResult grouping, newest-first retention, stable ordering, marker bounds, fingerprint,
malformed correlation, fixed-content overflow, latest-unit overflow, and no raw-content leakage.

Runtime tests prove every request is prepared, compaction emits one event, full transcript remains
in `AgentResult`, provider sees only selected history, context failure makes zero additional
provider calls, and sink failure cannot change behavior.

Property-style parametrized tests vary message/unit sizes around exact budget boundaries and
assert estimated output never exceeds usable budget.

## Learning Mapping

- JVM heap admission control maps to preflight request budgeting.
- Kafka/Flink records with transaction boundaries map to atomic ToolCall/ToolResult units.
- Flink checkpoint metadata maps conceptually to a transcript fingerprint, but M3a is not durable.
- Backpressure means refusing an oversized request before provider I/O, not retrying blindly.
- A deterministic compaction marker is metadata, not semantic memory.
