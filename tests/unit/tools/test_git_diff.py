from __future__ import annotations

import hashlib
import json

import pytest

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.git.errors import GitError, GitErrorCode
from mini_code_agent.git.models import GitDiffMode, GitDiffResult
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.git_diff import GitDiffTool


class FakeGit:
    def __init__(self, error: GitError | None = None) -> None:
        self.error = error
        self.staged: list[bool] = []

    async def diff(self, *, staged: bool = False) -> GitDiffResult:
        self.staged.append(staged)
        if self.error is not None:
            raise self.error
        patch = "+changed\n"
        encoded = patch.encode()
        return GitDiffResult(
            mode=GitDiffMode.STAGED if staged else GitDiffMode.UNSTAGED,
            patch=patch,
            byte_count=len(encoded),
            char_count=len(patch),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )


@pytest.mark.asyncio
async def test_git_diff_tool_selects_staged_mode() -> None:
    service = FakeGit()
    tool = GitDiffTool(service)

    result = await tool.execute(
        ToolCall(
            id="diff-1",
            name="git_diff",
            arguments={"staged": True},
        )
    )

    payload = json.loads(result.content)
    assert result.is_error is False
    assert payload["mode"] == "staged"
    assert payload["patch"] == "+changed\n"
    assert service.staged == [True]
    assert tool.definition.side_effect is SideEffect.READ_ONLY


@pytest.mark.asyncio
async def test_git_diff_tool_rejects_invalid_arguments_and_errors() -> None:
    tool = GitDiffTool(FakeGit(GitError(GitErrorCode.LIMIT_EXCEEDED)))

    invalid = await tool.execute(
        ToolCall(
            id="diff-1",
            name="git_diff",
            arguments={"staged": "yes"},
        )
    )
    failed = await tool.execute(ToolCall(id="diff-2", name="git_diff", arguments={}))

    assert json.loads(invalid.content)["error"]["code"] == "invalid_arguments"
    assert json.loads(failed.content)["error"]["code"] == "limit_exceeded"
