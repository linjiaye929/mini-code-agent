# M3a Deterministic Context Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound every provider request with deterministic estimation and atomic recent-history
selection while preserving the full runtime transcript and emitting auditable compaction evidence.

**Architecture:** A pure `ContextManager` groups full transcript messages into indivisible
interaction units, pins the original goal and latest completed unit, then retains a contiguous
recent suffix under a configurable estimated budget. `AgentRuntime` calls it before every
provider request and maps typed failures to `CONTEXT_LIMIT`.

**Tech Stack:** Python 3.12/3.13, Pydantic v2, SHA-256, canonical JSON, `Protocol`, Pytest,
strict Pyright.

---

## Invariants

1. Every `ModelRequest` is produced from a `ContextWindow`.
2. System prompt and original user goal are never truncated.
3. ToolCall and matching ToolResult batches are selected or omitted together.
4. Selected optional history is one contiguous newest suffix in original order.
5. Latest completed interaction is required after the pinned goal.
6. Estimated prepared request never exceeds `max_input - reserved_output`.
7. Full transcript remains in `AgentResult`.
8. Marker/event/error never contains omitted raw content.
9. Malformed transcript and fixed-content overflow call no provider.
10. Estimation is explicitly conservative/provider-neutral, not claimed exact.

## File Map

- Create `src/mini_code_agent/context/models.py`: limits and window DTOs.
- Create `src/mini_code_agent/context/errors.py`: stable internal context errors.
- Create `src/mini_code_agent/context/estimator.py`: protocol and UTF-8 estimator.
- Create `src/mini_code_agent/context/manager.py`: grouping and window selection.
- Create `src/mini_code_agent/context/__init__.py`: stable exports.
- Modify `src/mini_code_agent/agent/events.py`: `ContextCompacted`.
- Modify `src/mini_code_agent/agent/models.py`: `CONTEXT_LIMIT`.
- Modify `src/mini_code_agent/agent/runtime.py`: prepare before every provider call.
- Add `tests/unit/context` and runtime/integration regression tests.
- Update architecture, ADR, threat model, learning, resume, README, changelog, and release.

## Task 1: Context Limits and Deterministic Estimator

- [ ] Add failing tests for limits, immutability, canonical ordering, Unicode, ToolCall JSON, and
  tool-schema growth.
- [ ] Define:

```python
class ContextLimits(BaseModel):
    max_context_tokens: int = Field(default=32_768, ge=256, le=1_000_000)
    reserved_output_tokens: int = Field(default=4_096, ge=1, le=500_000)
    marker_max_chars: int = Field(default=500, ge=128, le=2_000)

    @model_validator(mode="after")
    def reserve_must_leave_input(self) -> Self:
        if self.reserved_output_tokens >= self.max_context_tokens:
            raise ValueError("reserved output must be below max input")
        return self
```

- [ ] Define `TokenEstimator`:

```python
class TokenEstimator(Protocol):
    def estimate(
        self,
        *,
        system_prompt: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> int: ...
```

- [ ] Implement `Utf8TokenEstimator` by serializing one canonical compact JSON object with sorted
  keys and returning UTF-8 byte length plus fixed request/message framing overhead. This is a
  conservative upper-bound heuristic, not vendor tokenization.
- [ ] Run focused tests, Ruff, Pyright; commit:

```powershell
git commit -m "feat: estimate bounded model context"
```

## Task 2: Transcript Grouping and Validation

- [ ] Add failing tests for first-user requirement, valid parallel ToolCall/ToolResult pairs,
  missing result, orphan result, mismatched IDs, duplicate IDs, standalone text, and stable unit
  ordering.
- [ ] Implement private immutable `_ContextUnit` with messages and `tool_exchange`.
- [ ] Group from message index 1:

```python
if message.tool_calls:
    result_message = messages[index + 1]
    call_ids = tuple(call.id for call in message.tool_calls)
    result_ids = tuple(result.tool_call_id for result in result_message.tool_results)
    if len(set(call_ids)) != len(call_ids) or set(call_ids) != set(result_ids):
        raise ContextError(ContextErrorCode.INVALID_TRANSCRIPT, ...)
```

- [ ] Reject ToolResult-only units and assistant ToolCalls without the immediately following
  result batch. Keep standalone non-tool messages as one-message units.
- [ ] Prove no error text contains message content or IDs; commit:

```powershell
git commit -m "feat: group atomic context exchanges"
```

## Task 3: Deterministic Context Window

- [ ] Add failing exact-boundary tests using a deterministic fake estimator.
- [ ] Define immutable `ContextWindow` fields: system prompt, selected messages, before/after
  estimate, omitted messages/exchanges, transcript fingerprint, and `compacted`.
- [ ] Canonically hash the complete transcript with sorted compact JSON and SHA-256.
- [ ] Implement selection:

```text
if full request fits -> return unchanged
pin first user goal
require newest unit
build bounded marker from omitted counts + fingerprint
walk older units newest-to-oldest
keep adding only while the complete candidate fits
emit pinned goal + retained contiguous suffix in original order
```

- [ ] Distinguish fixed prompt/tools/goal overflow from latest-unit overflow with typed codes.
- [ ] Re-estimate the final candidate and fail closed if it exceeds the usable budget.
- [ ] Parametrize budgets around `N-1/N/N+1`; prove marker bounds, no raw omitted content, stable
  fingerprint, and deterministic repeated results.
- [ ] Commit:

```powershell
git commit -m "feat: compact context deterministically"
```

## Task 4: Typed Compaction Event

- [ ] Add event-model tests for bounds, immutability, serialization, and `AgentEvent` union.
- [ ] Add:

```python
class ContextCompacted(EventBase):
    type: Literal["context_compacted"] = "context_compacted"
    turn: int = Field(ge=1)
    estimated_before: int = Field(ge=0)
    estimated_after: int = Field(ge=0)
    omitted_messages: int = Field(ge=1)
    omitted_tool_exchanges: int = Field(ge=0)
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
```

- [ ] Export it through `AgentEvent`; run tests and commit:

```powershell
git commit -m "feat: record context compaction events"
```

## Task 5: Agent Runtime Integration

- [ ] Add failing runtime tests proving manager invocation on every turn, provider request
  selection, one event per compacted request, full `AgentResult.messages`, and sink isolation.
- [ ] Add `StopReason.CONTEXT_LIMIT` and tests for typed error, unexpected manager exception, and
  invalid manager return. All stop before the next provider call and expose static text only.
- [ ] Inject `context: ContextPreparer | None`; default to bounded `ContextManager`.
- [ ] Before `ModelRequest`, prepare:

```python
try:
    window = self._context.prepare(
        system_prompt=system_prompt,
        messages=tuple(messages),
        tools=self._definitions,
    )
except ContextError:
    return self._stop(..., StopReason.CONTEXT_LIMIT, ..., "Model context limit exceeded.")
```

- [ ] Validate the candidate is a `ContextWindow`, publish `ContextCompacted` when compacted, and
  build the request from its prompt/messages.
- [ ] Add integration test with large deterministic ToolResults showing provider sees an atomic
  suffix while result retains full history.
- [ ] Run all Agent/Provider integration tests and commit:

```powershell
git commit -m "feat: enforce agent context budgets"
```

## Task 6: Documentation and `v0.7.0-alpha.0`

- [ ] Add `docs/architecture/context-budget.md` and ADR 0006.
- [ ] Document estimator limitations, atomic selection, marker semantics, full-transcript
  ownership, and why M3a is not durable memory.
- [ ] Update learning notes with JVM admission control, Flink record/checkpoint analogies, and
  exercises tracing exact boundary cases.
- [ ] Update resume rows with why, implementation, function, solved problem, measured evidence,
  and no unsupported token-savings claim.
- [ ] Update README/changelog and bump package/tests/lock to `0.7.0a0`.
- [ ] Run Python 3.12/3.13, coverage, Ruff, Pyright, Bandit, pip-audit, hashed build, and four
  isolated wheel/sdist smoke tests.
- [ ] Fast-forward merge, verify merged result, tag `v0.7.0-alpha.0`, and clean the owned worktree.
