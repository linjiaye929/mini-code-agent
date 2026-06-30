from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mini_code_agent.agent.models import StopReason
from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.domain.content import ToolCall
from mini_code_agent.domain.messages import Message, MessageRole
from mini_code_agent.git.client import GitClient
from mini_code_agent.providers.base import FinishReason, ModelResponse
from mini_code_agent.providers.fake import ScriptedProvider
from mini_code_agent.tools.git_diff import GitDiffTool
from mini_code_agent.tools.git_status import GitStatusTool
from mini_code_agent.tools.registry import ToolRegistry


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_agent_reads_real_git_status_and_diff_without_mutation(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    tracked = tmp_path / "app.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "--", "app.py")
    git(tmp_path, "commit", "-qm", "initial")
    tracked.write_text("value = 2\n", encoding="utf-8")
    index = tmp_path / ".git" / "index"
    before_index = (index.read_bytes(), index.stat().st_mtime_ns)

    service = GitClient(tmp_path)
    registry = ToolRegistry([GitStatusTool(service), GitDiffTool(service)])
    provider = ScriptedProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="status-1",
                            name="git_status",
                            arguments={},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content=(
                        ToolCall(
                            id="diff-1",
                            name="git_diff",
                            arguments={"staged": False},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            ModelResponse(
                message=Message.assistant_text("Git evidence inspected."),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )

    result = await AgentRuntime(provider, registry).run(
        user_prompt="Inspect repository changes.",
        run_id="git-read-run",
    )

    status_payload = json.loads(provider.requests[1].messages[-1].tool_results[0].content)
    diff_payload = json.loads(provider.requests[2].messages[-1].tool_results[0].content)
    assert result.stop_reason is StopReason.COMPLETED
    assert status_payload["entries"][0]["path"] == "app.py"
    assert "+value = 2" in diff_payload["patch"]
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before_index
