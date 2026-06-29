from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.read_file import ReadFileTool
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.models import WorkspaceLimits


def tool_for(root: Path, *, max_file_bytes: int = 1024 * 1024) -> ReadFileTool:
    return ReadFileTool(
        WorkspaceBoundary(
            root,
            limits=WorkspaceLimits(max_file_bytes=max_file_bytes),
        )
    )


def payload(content: str) -> dict[str, object]:
    return json.loads(content)  # type: ignore[no-any-return]


def test_read_file_definition_is_read_only_and_closed_schema(tmp_path: Path) -> None:
    tool = tool_for(tmp_path)

    assert tool.definition.name == "read_file"
    assert tool.definition.side_effect is SideEffect.READ_ONLY
    schema = tool.definition.model_dump(mode="json")["input_schema"]
    assert schema["required"] == ["path"]
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_read_file_returns_complete_structured_content(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_bytes("first\n中文\n".encode())
    registry = ToolRegistry([tool_for(tmp_path)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="read_file",
            arguments={"path": "hello.txt"},
        )
    )

    assert result.is_error is False
    assert payload(result.content) == {
        "content": "first\n中文\n",
        "end_line": 2,
        "path": "hello.txt",
        "start_line": 1,
        "total_lines": 2,
        "truncated": False,
    }
    assert str(tmp_path.resolve()) not in result.content


@pytest.mark.asyncio
async def test_read_file_returns_line_window_without_normalizing_endings(
    tmp_path: Path,
) -> None:
    (tmp_path / "lines.txt").write_bytes(b"one\r\ntwo\r\nthree\r\nfour\r\n")
    registry = ToolRegistry([tool_for(tmp_path)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="read_file",
            arguments={
                "path": "lines.txt",
                "start_line": 2,
                "max_lines": 2,
            },
        )
    )

    assert payload(result.content) == {
        "content": "two\r\nthree\r\n",
        "end_line": 3,
        "path": "lines.txt",
        "start_line": 2,
        "total_lines": 4,
        "truncated": True,
    }


@pytest.mark.asyncio
async def test_read_file_handles_empty_and_past_eof(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / "short.txt").write_bytes(b"only\n")
    registry = ToolRegistry([tool_for(tmp_path)])

    empty = await registry.execute(
        ToolCall(id="empty", name="read_file", arguments={"path": "empty.txt"})
    )
    past_eof = await registry.execute(
        ToolCall(
            id="eof",
            name="read_file",
            arguments={"path": "short.txt", "start_line": 10},
        )
    )

    assert payload(empty.content)["content"] == ""
    assert payload(empty.content)["end_line"] == 0
    assert payload(past_eof.content) == {
        "content": "",
        "end_line": 1,
        "path": "short.txt",
        "start_line": 10,
        "total_lines": 1,
        "truncated": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content", "max_bytes", "code"),
    [
        ("binary.dat", b"before\0after", 1024, "binary_file"),
        ("invalid.txt", b"\xff", 1024, "invalid_encoding"),
        ("large.txt", b"123456", 5, "too_large"),
    ],
)
async def test_read_file_returns_safe_workspace_errors(
    tmp_path: Path,
    path: str,
    content: bytes,
    max_bytes: int,
    code: str,
) -> None:
    (tmp_path / path).write_bytes(content)
    registry = ToolRegistry([tool_for(tmp_path, max_file_bytes=max_bytes)])

    result = await registry.execute(
        ToolCall(id="call-1", name="read_file", arguments={"path": path})
    )

    assert result.tool_call_id == "call-1"
    assert result.is_error is True
    error = cast(dict[str, object], payload(result.content)["error"])
    assert error["code"] == code
    assert str(tmp_path.resolve()) not in result.content


@pytest.mark.asyncio
async def test_read_file_direct_call_rejects_invalid_arguments(tmp_path: Path) -> None:
    tool = tool_for(tmp_path)

    result = await tool.execute(
        ToolCall(
            id="call-1",
            name="read_file",
            arguments={"path": "file.txt", "max_lines": 0},
        )
    )

    assert result.is_error is True
    error = cast(dict[str, object], payload(result.content)["error"])
    assert error["code"] == "invalid_arguments"
