from __future__ import annotations

import json

import pytest

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import GitStatusSnapshot, status_sha256
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.git_status import GitStatusTool


def empty_snapshot() -> GitStatusSnapshot:
    return GitStatusSnapshot(
        branch_oid="a" * 40,
        branch_head="main",
        entries=(),
        sha256=status_sha256(
            branch_oid="a" * 40,
            branch_head="main",
            branch_upstream=None,
            ahead=0,
            behind=0,
            entries=(),
        ),
    )


class FakeGit:
    def __init__(self, error: GitError | None = None) -> None:
        self.error = error
        self.status_calls = 0

    async def status(self) -> GitStatusSnapshot:
        self.status_calls += 1
        if self.error is not None:
            raise self.error
        return empty_snapshot()


@pytest.mark.asyncio
async def test_git_status_tool_returns_typed_snapshot() -> None:
    service = FakeGit()
    tool = GitStatusTool(service)

    result = await tool.execute(ToolCall(id="status-1", name="git_status", arguments={}))

    payload = json.loads(result.content)
    assert result.is_error is False
    assert payload["branch_head"] == "main"
    assert payload["entries"] == []
    assert service.status_calls == 1
    assert tool.definition.side_effect is SideEffect.READ_ONLY


@pytest.mark.asyncio
async def test_git_status_tool_rejects_arguments_and_normalizes_errors() -> None:
    service = FakeGit(GitError(GitErrorCode.NOT_REPOSITORY))
    tool = GitStatusTool(service)

    invalid = await tool.execute(
        ToolCall(
            id="status-1",
            name="git_status",
            arguments={"unexpected": True},
        )
    )
    failed = await tool.execute(ToolCall(id="status-2", name="git_status", arguments={}))

    assert invalid.is_error is True
    assert json.loads(invalid.content)["error"]["code"] == "invalid_arguments"
    assert failed.is_error is True
    assert json.loads(failed.content)["error"]["code"] == "not_repository"
    assert "secret" not in failed.content
