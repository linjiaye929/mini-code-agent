import json

import pytest

from mini_code_agent.agent.events import (
    ModelCompleted,
    ModelStarted,
    RecordingEventSink,
    RunStarted,
    RunStopped,
    ToolCompleted,
    ToolStarted,
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
                    content=(ToolCall(id="call-1", name="runtime_info", arguments={}),),
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
    assert payload["package_version"] == "0.16.0a0"
    assert [type(event) for event in events.events] == [
        RunStarted,
        ModelStarted,
        ModelCompleted,
        ToolStarted,
        ToolCompleted,
        ModelStarted,
        ModelCompleted,
        RunStopped,
    ]
