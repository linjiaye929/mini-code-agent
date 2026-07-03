# Deterministic Context Budget

[English](context-budget.md) | [简体中文](context-budget.zh-CN.md)

## Purpose

Every provider request passes through `ContextManager`. It bounds the request before network I/O
without mutating the runtime transcript, calling a model, or inventing a summary.

M3a is request admission control. It is not durable memory, checkpointing, or exact provider token
counting.

## Request Flow

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

`AgentRuntime` keeps the complete in-memory transcript and returns it in `AgentResult`. Provider
adapters receive only the selected `ContextWindow`.

## Estimation

`Utf8TokenEstimator` serializes the full request shape as canonical compact JSON and counts UTF-8
bytes plus fixed request, message, and tool framing. The default limits are:

- 32,768 estimated context units;
- 4,096 units reserved for output;
- 28,672 units available to the request;
- 500 characters maximum for the omission marker.

The estimator deliberately overweights multibyte text and is deterministic across supported
providers. It is not a vendor tokenizer and cannot guarantee that a provider accepts a request.
A provider-specific estimator can implement `TokenEstimator` and be injected without changing the
runtime.

## Atomic Units and Retention

The first user message is the fixed goal. Each assistant ToolCall batch and immediately following
user ToolResult batch forms one indivisible unit. Call/result IDs must be unique and equal as
sets. A malformed or partial exchange fails before provider I/O.

Retention rules are deterministic:

1. Return the complete transcript unchanged when it fits.
2. Always keep the original goal and newest completed unit.
3. Keep every completed exchange containing write, execute, network, mixed, or unknown tools.
4. Treat standalone messages and all-read-only exchanges as optional.
5. Add the newest contiguous optional suffix while the complete candidate fits.
6. Emit selected units in original transcript order.
7. Fail closed if the goal, newest unit, or required pinned history cannot fit.

Pinning completed side effects matters because dropping their evidence can lead a model to repeat
the action under a fresh ToolCall ID. Unknown tools are pinned because a changed registry cannot
prove that an old call was read-only.

M3a reduces this replay risk but does not provide durable exactly-once execution. M3c will add
checkpoint/resume and replay prevention across process failures.

## Omission Evidence

When optional history is omitted, a static system-prompt marker records:

- omitted message count;
- omitted tool-exchange count;
- SHA-256 of the canonical full transcript;
- an instruction that omitted details are unavailable and must not be guessed.

The same bounded metadata is emitted in `ContextCompacted`. Raw omitted content, arguments, paths,
and errors are not copied into the marker or event.

The SHA-256 is an identity/equality fingerprint. It is not encryption, authentication, secret
redaction, a persisted transcript, or proof that the omitted facts can be recovered.

## Failure Contract

Internal errors distinguish:

- `invalid_transcript`;
- `fixed_content_too_large`;
- `latest_exchange_too_large`;
- `pinned_history_too_large`;
- `window_build_failed`.

The runtime maps all of them to `StopReason.CONTEXT_LIMIT` with static public text. It makes no
additional provider call and does not expose transcript content or estimator exceptions.

## Verification Boundaries

Tests cover exact budget boundaries, Unicode and tool-schema estimation, parallel ToolCall
correlation, atomic retention, side-effect and unknown-tool pinning, marker bounds, deterministic
fingerprints, failure-before-provider behavior, full-result transcript ownership, and event-sink
isolation.

Not claimed by M3a:

- exact vendor token savings;
- semantic summaries or guaranteed fact preservation for omitted read-only history;
- persistence after process exit;
- authenticated trace integrity;
- side-effect replay prevention after crash/resume.
