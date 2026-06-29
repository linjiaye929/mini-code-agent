# M1 Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-neutral, deterministic Agent Runtime that can complete a response or execute native ToolCalls through a read-only tool boundary with explicit limits, normalized failures, and typed lifecycle events.

**Architecture:** M1 introduces immutable domain models, ports for providers and tools, a scripted provider, a read-only runtime information tool, and a framework-light Agent Runtime. The runtime depends only on protocols and domain types; concrete Anthropic and OpenAI-compatible adapters follow in M1b after these contracts are proven.

**Tech Stack:** Python 3.12/3.13, asyncio, Pydantic v2, typing.Protocol, Pytest, Ruff, strict Pyright.

---

## File Map

| Path | Responsibility |
|---|---|
| `src/mini_code_agent/domain/content.py` | Text, ToolCall, and ToolResult content blocks |
| `src/mini_code_agent/domain/messages.py` | Role-safe conversation messages |
| `src/mini_code_agent/tools/base.py` | Tool definition and executor port |
| `src/mini_code_agent/tools/runtime_info.py` | Side-effect-free built-in tool |
| `src/mini_code_agent/providers/base.py` | Provider request, response, stream, error, and protocol contracts |
| `src/mini_code_agent/providers/fake.py` | Deterministic scripted provider |
| `src/mini_code_agent/agent/models.py` | Agent limits, stop reasons, and result |
| `src/mini_code_agent/agent/events.py` | Typed lifecycle events and event sink port |
| `src/mini_code_agent/agent/runtime.py` | Bounded Agent Loop |
| `tests/unit/domain/test_messages.py` | Domain invariants |
| `tests/unit/tools/test_runtime_info.py` | Read-only tool behavior |
| `tests/unit/providers/test_fake_provider.py` | Provider contract and streaming |
| `tests/unit/agent/test_events.py` | Event recording contract |
| `tests/unit/agent/test_runtime.py` | Stop, limit, timeout, and failure behavior |
| `tests/integration/test_agent_loop.py` | Complete ToolCall round trip |
| `docs/architecture/agent-core.md` | M1 architecture and state transitions |
| `docs/learning/progress.md` | L1/L2 evidence and Java/Flink mapping |
| `docs/resume/project-profile.md` | Verified M1 highlights only |

## Task 1: Define Role-safe Message Models

**Files:**
- Create: `src/mini_code_agent/domain/__init__.py`
- Create: `src/mini_code_agent/domain/content.py`
- Create: `src/mini_code_agent/domain/messages.py`
- Create: `tests/unit/domain/test_messages.py`

- [ ] **Step 1: Write the message contract tests**

Create `tests/unit/domain/test_messages.py`:

```python
import pytest
from pydantic import ValidationError

from mini_code_agent.domain.content import TextBlock, ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole


def test_user_and_assistant_text_helpers_create_immutable_messages() -> None:
    user = Message.user_text("inspect the project")
    assistant = Message.assistant_text("ready")

    assert user.role is MessageRole.USER
    assert user.content == (TextBlock(text="inspect the project"),)
    assert assistant.role is MessageRole.ASSISTANT
    assert assistant.content == (TextBlock(text="ready"),)

    with pytest.raises(ValidationError):
        user.__setattr__("role", MessageRole.ASSISTANT)


def test_assistant_message_can_request_a_native_tool_call() -> None:
    call = ToolCall(id="call-1", name="runtime_info", arguments={})

    message = Message(role=MessageRole.ASSISTANT, content=(call,))

    assert message.tool_calls == (call,)


def test_user_message_can_carry_a_correlated_tool_result() -> None:
    result = ToolResult(
        tool_call_id="call-1",
        content='{"python_version":"3.13.14"}',
    )

    message = Message(role=MessageRole.USER, content=(result,))

    assert message.tool_results == (result,)


def test_role_invariants_reject_tool_calls_from_user() -> None:
    with pytest.raises(ValidationError, match="user message cannot contain ToolCall"):
        Message(
            role=MessageRole.USER,
            content=(ToolCall(id="call-1", name="runtime_info", arguments={}),),
        )


def test_role_invariants_reject_tool_results_from_assistant() -> None:
    with pytest.raises(ValidationError, match="assistant message cannot contain ToolResult"):
        Message(
            role=MessageRole.ASSISTANT,
            content=(ToolResult(tool_call_id="call-1", content="ok"),),
        )
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m uv run --no-sync pytest tests/unit/domain/test_messages.py -v
```

Expected: collection fails because `mini_code_agent.domain` does not exist.

- [ ] **Step 3: Implement content blocks**

Create `src/mini_code_agent/domain/content.py`:

```python
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ToolCall | ToolResult,
    Field(discriminator="type"),
]
```

- [ ] **Step 4: Implement role-safe messages**

Create `src/mini_code_agent/domain/messages.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.domain.content import (
    ContentBlock,
    TextBlock,
    ToolCall,
    ToolResult,
)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: tuple[ContentBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_role_content(self) -> Self:
        if self.role is MessageRole.USER and any(
            isinstance(block, ToolCall) for block in self.content
        ):
            raise ValueError("user message cannot contain ToolCall")
        if self.role is MessageRole.ASSISTANT and any(
            isinstance(block, ToolResult) for block in self.content
        ):
            raise ValueError("assistant message cannot contain ToolResult")
        return self

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(block for block in self.content if isinstance(block, ToolCall))

    @property
    def tool_results(self) -> tuple[ToolResult, ...]:
        return tuple(block for block in self.content if isinstance(block, ToolResult))

    @property
    def text(self) -> str:
        return "".join(
            block.text for block in self.content if isinstance(block, TextBlock)
        )

    @classmethod
    def user_text(cls, text: str) -> Self:
        return cls(role=MessageRole.USER, content=(TextBlock(text=text),))

    @classmethod
    def assistant_text(cls, text: str) -> Self:
        return cls(role=MessageRole.ASSISTANT, content=(TextBlock(text=text),))
```

Create `src/mini_code_agent/domain/__init__.py` as an empty package marker.

- [ ] **Step 5: Verify GREEN and static types**

```powershell
python -m uv run --no-sync pytest tests/unit/domain/test_messages.py -v
python -m uv run --no-sync ruff check src/mini_code_agent/domain tests/unit/domain
python -m uv run --no-sync pyright src/mini_code_agent/domain tests/unit/domain
```

Expected: 5 tests pass and both static checks exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/mini_code_agent/domain tests/unit/domain
git commit -m "feat: add agent message domain models"
```

## Task 2: Add the Tool Port and a Read-only Tool

**Files:**
- Create: `src/mini_code_agent/tools/__init__.py`
- Create: `src/mini_code_agent/tools/base.py`
- Create: `src/mini_code_agent/tools/runtime_info.py`
- Create: `tests/unit/tools/test_runtime_info.py`

- [ ] **Step 1: Write failing tool tests**

Create `tests/unit/tools/test_runtime_info.py`:

```python
import json

import pytest

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.runtime_info import RuntimeInfoTool


@pytest.mark.asyncio
async def test_runtime_info_is_declared_read_only() -> None:
    tool = RuntimeInfoTool()

    assert len(tool.definitions) == 1
    assert tool.definitions[0].name == "runtime_info"
    assert tool.definitions[0].side_effect is SideEffect.READ_ONLY


@pytest.mark.asyncio
async def test_runtime_info_returns_safe_structured_data() -> None:
    tool = RuntimeInfoTool()

    result = await tool.execute(
        ToolCall(id="call-1", name="runtime_info", arguments={})
    )

    payload = json.loads(result.content)
    assert result.tool_call_id == "call-1"
    assert result.is_error is False
    assert payload["package_version"] == "0.1.0a0"
    assert payload["python_version"]
    assert payload["platform"]


@pytest.mark.asyncio
async def test_runtime_info_rejects_unknown_tool_without_raising() -> None:
    tool = RuntimeInfoTool()

    result = await tool.execute(
        ToolCall(id="call-2", name="unknown_tool", arguments={})
    )

    assert result.is_error is True
    assert json.loads(result.content)["error"]["code"] == "unknown_tool"


@pytest.mark.asyncio
async def test_runtime_info_rejects_unexpected_arguments() -> None:
    tool = RuntimeInfoTool()

    result = await tool.execute(
        ToolCall(id="call-3", name="runtime_info", arguments={"secret": "value"})
    )

    assert result.is_error is True
    assert "value" not in result.content
```

Add `pytest-asyncio>=0.25,<2` to the `dev` dependency group and set:

```toml
[tool.pytest.ini_options]
addopts = ["-ra", "--strict-config", "--strict-markers"]
asyncio_mode = "strict"
testpaths = ["tests"]
```

- [ ] **Step 2: Verify RED**

```powershell
python -m uv lock
python -m uv sync --locked --all-groups
python -m uv run --no-sync pytest tests/unit/tools/test_runtime_info.py -v
```

Expected: collection fails because `mini_code_agent.tools` does not exist.

- [ ] **Step 3: Implement the tool contract**

Create `src/mini_code_agent/tools/base.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mini_code_agent.domain.content import ToolCall, ToolResult


class SideEffect(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, JsonValue]
    side_effect: SideEffect


class ToolExecutor(Protocol):
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...
```

- [ ] **Step 4: Implement the side-effect-free runtime tool**

Create `src/mini_code_agent/tools/runtime_info.py`:

```python
from __future__ import annotations

import json
import platform
from typing import ClassVar

from mini_code_agent import __version__
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.tools.base import SideEffect, ToolDefinition


class RuntimeInfoTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="runtime_info",
        description="Return package, Python, and operating-system version information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
    )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (self._definition,)

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name != self._definition.name:
            return self._error(call.id, "unknown_tool", "The requested tool is not registered.")
        if call.arguments:
            return self._error(
                call.id,
                "invalid_arguments",
                "runtime_info does not accept arguments.",
            )
        payload = {
            "package_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        }
        return ToolResult(
            tool_call_id=call.id,
            content=json.dumps(payload, ensure_ascii=True, sort_keys=True),
        )

    @staticmethod
    def _error(call_id: str, code: str, message: str) -> ToolResult:
        payload = {"error": {"code": code, "message": message}}
        return ToolResult(
            tool_call_id=call_id,
            content=json.dumps(payload, ensure_ascii=True, sort_keys=True),
            is_error=True,
        )
```

Create `src/mini_code_agent/tools/__init__.py` as an empty package marker.

- [ ] **Step 5: Verify and commit**

```powershell
python -m uv run --no-sync pytest tests/unit/tools/test_runtime_info.py -v
python -m uv run --no-sync ruff check src/mini_code_agent/tools tests/unit/tools
python -m uv run --no-sync pyright src/mini_code_agent/tools tests/unit/tools
git add pyproject.toml uv.lock src/mini_code_agent/tools tests/unit/tools
git commit -m "feat: add read-only tool execution port"
```

Expected: 4 tests pass.

## Task 3: Define Provider Contracts and Scripted Provider

**Files:**
- Create: `src/mini_code_agent/providers/__init__.py`
- Create: `src/mini_code_agent/providers/base.py`
- Create: `src/mini_code_agent/providers/fake.py`
- Create: `tests/unit/providers/test_fake_provider.py`

- [ ] **Step 1: Write failing provider tests**

Create `tests/unit/providers/test_fake_provider.py`:

```python
import pytest
from pydantic import ValidationError

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
    ResponseCompleted,
    TextDelta,
    TokenUsage,
)
from mini_code_agent.providers.fake import ScriptedProvider


def response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=4, output_tokens=2),
        provider_request_id="provider-1",
    )


@pytest.mark.asyncio
async def test_scripted_provider_records_requests_and_returns_response() -> None:
    provider = ScriptedProvider([response("done")])
    request = ModelRequest(
        request_id="request-1",
        system_prompt="Be precise.",
        messages=(Message.user_text("work"),),
    )

    result = await provider.complete(request)

    assert result.message.text == "done"
    assert provider.requests == [request]
    assert provider.capabilities.tool_calling is True


@pytest.mark.asyncio
async def test_scripted_provider_exhaustion_is_normalized() -> None:
    provider = ScriptedProvider([])
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("work"),),
    )

    with pytest.raises(ProviderError) as captured:
        await provider.complete(request)

    assert captured.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_scripted_provider_can_raise_a_normalized_error() -> None:
    provider = ScriptedProvider(
        [
            ProviderError(
                ProviderErrorCode.RATE_LIMIT,
                "Provider is temporarily rate limited.",
                retryable=True,
            )
        ]
    )
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("work"),),
    )

    with pytest.raises(ProviderError) as captured:
        await provider.complete(request)

    assert captured.value.code is ProviderErrorCode.RATE_LIMIT
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_stream_emits_text_and_completed_response() -> None:
    provider = ScriptedProvider([response("done")])
    request = ModelRequest(
        request_id="request-1",
        system_prompt="",
        messages=(Message.user_text("work"),),
    )

    events = [event async for event in provider.stream(request)]

    assert events[0] == TextDelta(text="done")
    assert events[1] == ResponseCompleted(response=response("done"))


def test_response_rejects_tool_call_with_stop_reason() -> None:
    with pytest.raises(ValidationError, match="ToolCall requires tool_call finish reason"):
        ModelResponse(
            message=Message(
                role=MessageRole.ASSISTANT,
                content=(
                    ToolCall(id="call-1", name="runtime_info", arguments={}),
                ),
            ),
            finish_reason=FinishReason.STOP,
        )
```

- [ ] **Step 2: Verify RED**

```powershell
python -m uv run --no-sync pytest tests/unit/providers/test_fake_provider.py -v
```

Expected: collection fails because `mini_code_agent.providers` does not exist.

- [ ] **Step 3: Implement provider contracts**

Create `src/mini_code_agent/providers/base.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.tools.base import ToolDefinition


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALL = "tool_call"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_calling: bool = True
    streaming: bool = True
    parallel_tool_calls: bool = False
    usage: bool = True


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    system_prompt: str
    messages: tuple[Message, ...] = Field(min_length=1)
    tools: tuple[ToolDefinition, ...] = ()


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: Message
    finish_reason: FinishReason
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider_request_id: str | None = None

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if self.message.role is not MessageRole.ASSISTANT:
            raise ValueError("provider response message must have assistant role")
        if self.finish_reason is FinishReason.TOOL_CALL and not self.message.tool_calls:
            raise ValueError("tool_call finish reason requires at least one ToolCall")
        if self.message.tool_calls and self.finish_reason is not FinishReason.TOOL_CALL:
            raise ValueError("ToolCall requires tool_call finish reason")
        return self


class TextDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call_delta"] = "tool_call_delta"
    index: int = Field(ge=0)
    partial_json: str


class ResponseCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["response_completed"] = "response_completed"
    response: ModelResponse


ProviderStreamEvent = Annotated[
    TextDelta | ToolCallDelta | ResponseCompleted,
    Field(discriminator="type"),
]


class ProviderErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVER = "server"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: ProviderErrorCode,
        public_message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


class ModelProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderStreamEvent]: ...
```

- [ ] **Step 4: Implement the scripted provider**

Create `src/mini_code_agent/providers/fake.py`:

```python
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable

from mini_code_agent.domain.content import TextBlock
from mini_code_agent.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
    ProviderStreamEvent,
    ResponseCompleted,
    TextDelta,
)


class ScriptedProvider:
    def __init__(
        self,
        steps: Iterable[ModelResponse | ProviderError],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._steps = deque(steps)
        self._delay_seconds = delay_seconds
        self.requests: list[ModelRequest] = []
        self._capabilities = ProviderCapabilities()

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if not self._steps:
            raise ProviderError(
                ProviderErrorCode.INVALID_RESPONSE,
                "The scripted provider has no remaining response.",
                retryable=False,
            )
        step = self._steps.popleft()
        if isinstance(step, ProviderError):
            raise step
        return step

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ProviderStreamEvent]:
        response = await self.complete(request)
        for block in response.message.content:
            if isinstance(block, TextBlock):
                yield TextDelta(text=block.text)
        yield ResponseCompleted(response=response)
```

Create `src/mini_code_agent/providers/__init__.py` as an empty package marker.

- [ ] **Step 5: Verify and commit**

```powershell
python -m uv run --no-sync pytest tests/unit/providers/test_fake_provider.py -v
python -m uv run --no-sync ruff check src/mini_code_agent/providers tests/unit/providers
python -m uv run --no-sync pyright src/mini_code_agent/providers tests/unit/providers
git add src/mini_code_agent/providers tests/unit/providers
git commit -m "feat: add provider contracts and scripted provider"
```

Expected: 5 tests pass.

## Task 4: Add Typed Agent Events and Result Models

**Files:**
- Create: `src/mini_code_agent/agent/__init__.py`
- Create: `src/mini_code_agent/agent/models.py`
- Create: `src/mini_code_agent/agent/events.py`
- Create: `tests/unit/agent/test_events.py`

- [ ] **Step 1: Write failing event tests**

Create `tests/unit/agent/test_events.py`:

```python
from mini_code_agent.agent.events import RecordingEventSink, RunStarted


def test_recording_sink_preserves_typed_event_order() -> None:
    sink = RecordingEventSink()
    event = RunStarted(run_id="run-1", max_turns=4)

    sink.publish(event)

    assert sink.events == [event]
    assert sink.events[0].run_id == "run-1"
```

- [ ] **Step 2: Verify RED**

```powershell
python -m uv run --no-sync pytest tests/unit/agent/test_events.py -v
```

Expected: collection fails because `mini_code_agent.agent` does not exist.

- [ ] **Step 3: Implement agent result models**

Create `src/mini_code_agent/agent/models.py`:

```python
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.domain.messages import Message
from mini_code_agent.providers.base import TokenUsage


class StopReason(StrEnum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    MAX_TOOL_CALLS = "max_tool_calls"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_LIMIT = "provider_limit"
    DUPLICATE_TOOL_CALL = "duplicate_tool_call"
    INVALID_RESPONSE = "invalid_response"
    CANCELLED = "cancelled"


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: int = Field(default=8, ge=1, le=100)
    max_tool_calls: int = Field(default=32, ge=0, le=1000)
    provider_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=600)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    messages: tuple[Message, ...]
    stop_reason: StopReason
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    usage: TokenUsage
    final_text: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.stop_reason is StopReason.COMPLETED
```

- [ ] **Step 4: Implement typed event sinks**

Create `src/mini_code_agent/agent/events.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.agent.models import StopReason
from mini_code_agent.providers.base import FinishReason, TokenUsage


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunStarted(EventBase):
    type: Literal["run_started"] = "run_started"
    max_turns: int


class ModelCompleted(EventBase):
    type: Literal["model_completed"] = "model_completed"
    turn: int
    finish_reason: FinishReason
    usage: TokenUsage


class ToolCompleted(EventBase):
    type: Literal["tool_completed"] = "tool_completed"
    turn: int
    tool_call_id: str
    tool_name: str
    is_error: bool


class RunStopped(EventBase):
    type: Literal["run_stopped"] = "run_stopped"
    turns: int
    reason: StopReason
    error: str | None = None


AgentEvent = RunStarted | ModelCompleted | ToolCompleted | RunStopped


class EventSink(Protocol):
    def publish(self, event: AgentEvent) -> None: ...


class NullEventSink:
    def publish(self, event: AgentEvent) -> None:
        del event


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def publish(self, event: AgentEvent) -> None:
        self.events.append(event)
```

Create `src/mini_code_agent/agent/__init__.py` as an empty package marker.

- [ ] **Step 5: Verify and commit**

```powershell
python -m uv run --no-sync pytest tests/unit/agent/test_events.py -v
python -m uv run --no-sync ruff check src/mini_code_agent/agent tests/unit/agent
python -m uv run --no-sync pyright src/mini_code_agent/agent tests/unit/agent
git add src/mini_code_agent/agent tests/unit/agent
git commit -m "feat: add typed agent lifecycle models"
```

Expected: 1 test passes.

## Task 5: Implement the Bounded Agent Runtime

**Files:**
- Create: `src/mini_code_agent/agent/runtime.py`
- Create: `tests/unit/agent/test_runtime.py`
- Create: `tests/integration/test_agent_loop.py`

- [ ] **Step 1: Write the failing runtime tests**

Create `tests/unit/agent/test_runtime.py` with these concrete cases:

```python
import asyncio

import pytest

from mini_code_agent.agent.events import RecordingEventSink, RunStopped
from mini_code_agent.agent.models import AgentLimits, StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
)
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.runtime_info import RuntimeInfoTool


def final_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant_text(text),
        finish_reason=FinishReason.STOP,
    )


def tool_response(call_id: str) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=(
                ToolCall(id=call_id, name="runtime_info", arguments={}),
            ),
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


@pytest.mark.asyncio
async def test_runtime_completes_with_final_text() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([final_response("done")]),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_text == "done"
    assert result.turns == 1
    assert result.tool_calls == 0


@pytest.mark.asyncio
async def test_runtime_stops_at_max_turns() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1"), tool_response("call-2")]),
        RuntimeInfoTool(),
        limits=AgentLimits(max_turns=2),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.MAX_TURNS
    assert result.turns == 2
    assert result.tool_calls == 2


@pytest.mark.asyncio
async def test_runtime_rejects_duplicate_tool_call_ids() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1"), tool_response("call-1")]),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.DUPLICATE_TOOL_CALL
    assert result.tool_calls == 1


@pytest.mark.asyncio
async def test_runtime_stops_on_normalized_provider_error() -> None:
    runtime = AgentRuntime(
        ScriptedProvider(
            [
                ProviderError(
                    ProviderErrorCode.AUTHENTICATION,
                    "Provider authentication failed.",
                    retryable=False,
                )
            ]
        ),
        RuntimeInfoTool(),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_ERROR
    assert result.error == "Provider authentication failed."


@pytest.mark.asyncio
async def test_runtime_stops_on_provider_timeout() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([final_response("late")], delay_seconds=0.05),
        RuntimeInfoTool(),
        limits=AgentLimits(provider_timeout_seconds=0.01),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_TIMEOUT


@pytest.mark.asyncio
async def test_runtime_re_raises_task_cancellation_after_event() -> None:
    sink = RecordingEventSink()
    runtime = AgentRuntime(
        ScriptedProvider([final_response("late")], delay_seconds=10),
        RuntimeInfoTool(),
        events=sink,
    )
    task = asyncio.create_task(runtime.run(user_prompt="inspect"))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    stopped = [event for event in sink.events if isinstance(event, RunStopped)]
    assert stopped[-1].reason is StopReason.CANCELLED
```

Create `tests/integration/test_agent_loop.py`:

```python
import json

import pytest

from mini_code_agent.agent.events import (
    ModelCompleted,
    RecordingEventSink,
    RunStarted,
    RunStopped,
    ToolCompleted,
)
from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import FinishReason, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.runtime_info import RuntimeInfoTool


@pytest.mark.asyncio
async def test_fake_provider_drives_native_tool_call_round_trip() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(id="call-1", name="runtime_info", arguments={}),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            ModelResponse(
                message=Message.assistant_text("Runtime inspected."),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    events = RecordingEventSink()
    runtime = AgentRuntime(provider, RuntimeInfoTool(), events=events)

    result = await runtime.run(
        user_prompt="Inspect the runtime.",
        system_prompt="Use tools when needed.",
        run_id="run-1",
    )

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_text == "Runtime inspected."
    assert len(provider.requests) == 2
    tool_result_message = provider.requests[1].messages[-1]
    assert tool_result_message.role is MessageRole.USER
    assert tool_result_message.tool_results[0].tool_call_id == "call-1"
    payload = json.loads(tool_result_message.tool_results[0].content)
    assert payload["package_version"] == "0.1.0a0"
    assert [type(event) for event in events.events] == [
        RunStarted,
        ModelCompleted,
        ToolCompleted,
        ModelCompleted,
        RunStopped,
    ]
```

- [ ] **Step 2: Verify RED**

```powershell
python -m uv run --no-sync pytest tests/unit/agent/test_runtime.py tests/integration/test_agent_loop.py -v
```

Expected: collection fails because `mini_code_agent.agent.runtime` does not exist.

- [ ] **Step 3: Implement the runtime**

Create `src/mini_code_agent/agent/runtime.py`:

```python
from __future__ import annotations

import asyncio
from uuid import uuid4

from mini_code_agent.agent.events import (
    EventSink,
    ModelCompleted,
    NullEventSink,
    RunStarted,
    RunStopped,
    ToolCompleted,
)
from mini_code_agent.agent.models import AgentLimits, AgentResult, StopReason
from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.providers.base import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ProviderError,
    TokenUsage,
)
from mini_code_agent.tools.base import ToolExecutor


class AgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolExecutor,
        *,
        limits: AgentLimits | None = None,
        events: EventSink | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._limits = limits or AgentLimits()
        self._events = events or NullEventSink()

    async def run(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        run_id: str | None = None,
    ) -> AgentResult:
        active_run_id = run_id or str(uuid4())
        messages = [Message.user_text(user_prompt)]
        usage = TokenUsage()
        seen_call_ids: set[str] = set()
        tool_call_count = 0
        self._events.publish(
            RunStarted(run_id=active_run_id, max_turns=self._limits.max_turns)
        )

        for turn in range(1, self._limits.max_turns + 1):
            request = ModelRequest(
                request_id=f"{active_run_id}:{turn}",
                system_prompt=system_prompt,
                messages=tuple(messages),
                tools=self._tools.definitions,
            )
            try:
                async with asyncio.timeout(self._limits.provider_timeout_seconds):
                    response = await self._provider.complete(request)
            except asyncio.CancelledError:
                self._events.publish(
                    RunStopped(
                        run_id=active_run_id,
                        turns=turn - 1,
                        reason=StopReason.CANCELLED,
                    )
                )
                raise
            except TimeoutError:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_TIMEOUT,
                    turn - 1,
                    tool_call_count,
                    usage,
                    "Provider request timed out.",
                )
            except ProviderError as exc:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_ERROR,
                    turn - 1,
                    tool_call_count,
                    usage,
                    exc.public_message,
                )
            except Exception:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_ERROR,
                    turn - 1,
                    tool_call_count,
                    usage,
                    "Provider request failed unexpectedly.",
                )

            messages.append(response.message)
            usage = TokenUsage(
                input_tokens=usage.input_tokens + response.usage.input_tokens,
                output_tokens=usage.output_tokens + response.usage.output_tokens,
            )
            self._events.publish(
                ModelCompleted(
                    run_id=active_run_id,
                    turn=turn,
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                )
            )

            if response.finish_reason is FinishReason.STOP:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.COMPLETED,
                    turn,
                    tool_call_count,
                    usage,
                    final_text=response.message.text,
                )

            if response.finish_reason is not FinishReason.TOOL_CALL:
                return self._stop(
                    active_run_id,
                    messages,
                    StopReason.PROVIDER_LIMIT,
                    turn,
                    tool_call_count,
                    usage,
                    "Provider stopped before completing the response.",
                )

            tool_results: list[ToolResult] = []
            for call in response.message.tool_calls:
                if call.id in seen_call_ids:
                    return self._stop(
                        active_run_id,
                        messages,
                        StopReason.DUPLICATE_TOOL_CALL,
                        turn,
                        tool_call_count,
                        usage,
                        "Provider repeated a ToolCall identifier.",
                    )
                if tool_call_count >= self._limits.max_tool_calls:
                    return self._stop(
                        active_run_id,
                        messages,
                        StopReason.MAX_TOOL_CALLS,
                        turn,
                        tool_call_count,
                        usage,
                        "Agent reached the ToolCall limit.",
                    )
                seen_call_ids.add(call.id)
                tool_call_count += 1
                result = await self._execute_tool(call)
                tool_results.append(result)
                self._events.publish(
                    ToolCompleted(
                        run_id=active_run_id,
                        turn=turn,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        is_error=result.is_error,
                    )
                )
            messages.append(
                Message(role=MessageRole.USER, content=tuple(tool_results))
            )

        return self._stop(
            active_run_id,
            messages,
            StopReason.MAX_TURNS,
            self._limits.max_turns,
            tool_call_count,
            usage,
            "Agent reached the turn limit.",
        )

    async def _execute_tool(self, call: ToolCall) -> ToolResult:
        try:
            async with asyncio.timeout(self._limits.tool_timeout_seconds):
                result = await self._tools.execute(call)
        except TimeoutError:
            return ToolResult(
                tool_call_id=call.id,
                content='{"error":{"code":"tool_timeout","message":"Tool execution timed out."}}',
                is_error=True,
            )
        except Exception:
            return ToolResult(
                tool_call_id=call.id,
                content='{"error":{"code":"tool_failed","message":"Tool execution failed."}}',
                is_error=True,
            )
        if result.tool_call_id != call.id:
            return ToolResult(
                tool_call_id=call.id,
                content='{"error":{"code":"invalid_tool_result","message":"Tool result ID mismatch."}}',
                is_error=True,
            )
        return result

    def _stop(
        self,
        run_id: str,
        messages: list[Message],
        reason: StopReason,
        turns: int,
        tool_calls: int,
        usage: TokenUsage,
        error: str | None = None,
        *,
        final_text: str | None = None,
    ) -> AgentResult:
        self._events.publish(
            RunStopped(
                run_id=run_id,
                turns=turns,
                reason=reason,
                error=error,
            )
        )
        return AgentResult(
            run_id=run_id,
            messages=tuple(messages),
            stop_reason=reason,
            turns=turns,
            tool_calls=tool_calls,
            usage=usage,
            final_text=final_text,
            error=error,
        )
```

- [ ] **Step 4: Verify GREEN**

```powershell
python -m uv run --no-sync pytest tests/unit/agent/test_runtime.py tests/integration/test_agent_loop.py -v
python -m uv run --no-sync ruff check src/mini_code_agent/agent tests/unit/agent tests/integration
python -m uv run --no-sync pyright
```

Expected: 7 runtime/integration tests pass and static checks exit 0.

- [ ] **Step 5: Add negative tests before refactoring**

Append to `tests/unit/agent/test_runtime.py`:

```python
class SlowTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        await asyncio.sleep(10)
        return await super().execute(call)


class RaisingTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        del call
        raise RuntimeError("internal-tool-secret")


class MismatchedTool(RuntimeInfoTool):
    async def execute(self, call: ToolCall) -> ToolResult:
        del call
        return ToolResult(tool_call_id="wrong-id", content="incorrect")


@pytest.mark.asyncio
async def test_runtime_stops_before_exceeding_tool_call_limit() -> None:
    runtime = AgentRuntime(
        ScriptedProvider([tool_response("call-1")]),
        RuntimeInfoTool(),
        limits=AgentLimits(max_tool_calls=0),
    )

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert result.tool_calls == 0


@pytest.mark.asyncio
async def test_tool_timeout_becomes_correlated_error_result() -> None:
    provider = ScriptedProvider(
        [tool_response("call-1"), final_response("recovered")]
    )
    runtime = AgentRuntime(
        provider,
        SlowTool(),
        limits=AgentLimits(tool_timeout_seconds=0.01),
    )

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True
    assert "tool_timeout" in tool_result.content


@pytest.mark.asyncio
async def test_unexpected_tool_exception_is_not_exposed() -> None:
    provider = ScriptedProvider(
        [tool_response("call-1"), final_response("recovered")]
    )
    runtime = AgentRuntime(provider, RaisingTool())

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.is_error is True
    assert "tool_failed" in tool_result.content
    assert "internal-tool-secret" not in tool_result.content


@pytest.mark.asyncio
async def test_mismatched_tool_result_id_is_recorrelated() -> None:
    provider = ScriptedProvider(
        [tool_response("call-1"), final_response("recovered")]
    )
    runtime = AgentRuntime(provider, MismatchedTool())

    result = await runtime.run(user_prompt="inspect")

    tool_result = provider.requests[1].messages[-1].tool_results[0]
    assert result.stop_reason is StopReason.COMPLETED
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True
    assert "invalid_tool_result" in tool_result.content


@pytest.mark.asyncio
async def test_max_tokens_maps_to_provider_limit() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message.assistant_text("partial"),
                finish_reason=FinishReason.MAX_TOKENS,
            )
        ]
    )
    runtime = AgentRuntime(provider, RuntimeInfoTool())

    result = await runtime.run(user_prompt="inspect")

    assert result.stop_reason is StopReason.PROVIDER_LIMIT
    assert result.succeeded is False


@pytest.mark.asyncio
async def test_every_executed_tool_call_has_exactly_one_result() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="call-1",
                            name="runtime_info",
                            arguments={},
                        ),
                        ToolCall(
                            id="call-2",
                            name="runtime_info",
                            arguments={},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            final_response("done"),
        ]
    )
    runtime = AgentRuntime(provider, RuntimeInfoTool())

    result = await runtime.run(user_prompt="inspect")

    results = provider.requests[1].messages[-1].tool_results
    assert result.stop_reason is StopReason.COMPLETED
    assert [item.tool_call_id for item in results] == ["call-1", "call-2"]
    assert len(results) == 2
```

Run each new test before changing production code and confirm the failure is caused by the
target behavior. Do not add broad exception suppression or expose exception text.

- [ ] **Step 6: Run all M1 tests and commit**

```powershell
python -m uv run --no-sync pytest tests/unit/domain tests/unit/tools tests/unit/providers tests/unit/agent tests/integration -v
python -m uv run --no-sync ruff format --check src tests
python -m uv run --no-sync ruff check src tests
python -m uv run --no-sync pyright
git add src/mini_code_agent/agent tests/unit/agent tests/integration
git commit -m "feat: add bounded native tool-calling agent loop"
```

## Task 6: Document, Review, and Verify M1

**Files:**
- Create: `docs/architecture/agent-core.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document state transitions**

Create `docs/architecture/agent-core.md` with:

```markdown
# Agent Core

## State Machine

`AgentRuntime` owns a bounded sequence:

1. Build a provider-neutral request.
2. Await one normalized provider response under a timeout.
3. Stop on a final response or provider limit.
4. For ToolCalls, reject duplicate IDs and enforce the total call budget.
5. Execute each call through `ToolExecutor` under a timeout.
6. Append exactly one correlated ToolResult per executed ToolCall.
7. Repeat until completion or a deterministic limit.

## Boundaries

- Providers translate vendor protocols; they do not execute tools.
- Tools do not call providers or mutate Agent state.
- AgentRuntime never imports a vendor SDK.
- Events contain lifecycle metadata, not prompts, arguments, or secret-bearing raw responses.
- Python task cancellation is recorded and re-raised to preserve structured concurrency.

## M1 Non-goals

- Real Anthropic/OpenAI adapters.
- Workspace file access.
- Permission approval.
- Persistence, checkpointing, retry scheduling, and context compression.
```

- [ ] **Step 2: Update learning and resume evidence**

In `docs/learning/progress.md`:

- mark L1 complete locally after tests pass;
- mark L2 in progress because contracts and Fake Provider exist but real adapters remain;
- record exact test/coverage output;
- map `Protocol` to a Java interface, Pydantic models to validated immutable DTOs, and the
  bounded Agent Loop to a state machine with explicit backpressure/limits.

In `docs/resume/project-profile.md`:

- add M1 only as locally verified;
- state why explicit limits, ToolCall correlation, Fake Provider, and typed events exist;
- do not claim Anthropic/OpenAI support until M1b contract tests pass.

- [ ] **Step 3: Run the full repository gate**

```powershell
python -m uv lock --check
python -m uv sync --locked --all-groups
python -m uv run --no-sync ruff format --check .
python -m uv run --no-sync ruff check .
python -m uv run --no-sync pyright
python -m uv run --no-sync pytest --cov
python -m uv build --build-constraint build-constraints.txt --require-hashes
```

Expected:

- all tests pass on local Python 3.13;
- total branch-aware package coverage remains at least 85%;
- strict Pyright and Ruff report no issues;
- constrained wheel and sdist builds succeed.

- [ ] **Step 4: Request independent review**

Review the M1 Git range against:

- native ToolCall correlation;
- deterministic stop conditions;
- cancellation and timeout semantics;
- no raw exception or secret propagation;
- provider/tool dependency inversion;
- test coverage of every stop reason.

Fix all Critical and Important findings with RED-GREEN regression tests.

- [ ] **Step 5: Commit verified M1 evidence**

```powershell
git add docs/architecture/agent-core.md docs/learning/progress.md docs/resume/project-profile.md CHANGELOG.md
git commit -m "docs: record verified M1 agent core evidence"
```

- [ ] **Step 6: Merge and tag**

After the merged result passes the same gate:

```powershell
git tag -a v0.2.0-alpha.0 -m "M1 provider-neutral agent core"
git show --stat --oneline v0.2.0-alpha.0
```

## M1 Completion Gate

M1 is complete only when:

- message role/content invariants are enforced by Pydantic;
- Provider and Tool ports contain no vendor SDK imports;
- Fake Provider covers complete, error, exhaustion, delay, and stream behavior;
- one deterministic integration test performs provider -> ToolCall -> ToolResult -> provider;
- ToolCall IDs are unique and every executed call has exactly one correlated result;
- max turns, max tool calls, provider timeout, tool timeout, provider failure, cancellation, and
  provider-limit stops are tested;
- typed lifecycle events record start, model, tool, and stop without raw prompt/tool payloads;
- Ruff, strict Pyright, full tests, coverage, constrained build, and independent review pass;
- learning and resume documents contain only measured M1 evidence;
- the merged `main` worktree is clean and tagged `v0.2.0-alpha.0`.

## Post-review Hardening

Independent review expanded the original M1 contracts with required regression coverage:

- ToolCall batches are fully preflighted before execution, so duplicate IDs or exhausted budget
  cannot create partial unrecorded side effects.
- Provider and Tool return values are checked at runtime even when their implementations claim to
  satisfy the static Protocol.
- Provider and Tool cancellation both publish a best-effort stopped event and re-raise the
  original `CancelledError`.
- Event sink failures are isolated at every lifecycle phase and cannot replace run outcomes.
- ToolCall arguments and ToolDefinition schemas are recursively immutable while preserving JSON
  serialization.
- M1 enforces read-only tool definitions and prevents unregistered calls from reaching the
  executor.
- Run IDs are bounded before the first event, and stream ToolCall deltas include call ID and name.
