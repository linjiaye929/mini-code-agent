from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from mini_code_agent.domain.content import ToolCall
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.tools.registry import ToolRegistry
from mini_code_agent.tools.search_text import SearchTextTool
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.models import SearchLimits, WorkspaceLimits


def payload(content: str) -> dict[str, object]:
    return json.loads(content)  # type: ignore[no-any-return]


def tool_for(
    root: Path,
    *,
    search_limits: SearchLimits | None = None,
    max_file_bytes: int = 1024 * 1024,
) -> SearchTextTool:
    return SearchTextTool(
        WorkspaceBoundary(
            root,
            limits=WorkspaceLimits(max_file_bytes=max_file_bytes),
        ),
        limits=search_limits,
    )


def test_search_definition_is_read_only_literal_search(tmp_path: Path) -> None:
    tool = tool_for(tmp_path)

    assert tool.definition.name == "search_text"
    assert tool.definition.side_effect is SideEffect.READ_ONLY
    schema = tool.definition.model_dump(mode="json")["input_schema"]
    assert "regex" not in schema["properties"]
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_search_returns_deterministic_literal_matches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_bytes(b"needle once\n")
    (tmp_path / "src" / "a.py").write_bytes(b"needle x needle\n")
    registry = ToolRegistry([tool_for(tmp_path)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "needle"},
        )
    )

    assert result.is_error is False
    assert payload(result.content) == {
        "files_scanned": 2,
        "matches": [
            {
                "column": 1,
                "line": 1,
                "path": "src/a.py",
                "preview": "needle x needle",
            },
            {
                "column": 10,
                "line": 1,
                "path": "src/a.py",
                "preview": "needle x needle",
            },
            {
                "column": 1,
                "line": 1,
                "path": "src/b.py",
                "preview": "needle once",
            },
        ],
        "query": "needle",
        "skipped_files": 0,
        "truncated": False,
    }
    assert str(tmp_path.resolve()) not in result.content


@pytest.mark.asyncio
async def test_search_supports_subdirectory_glob_and_case_insensitive_mode(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_bytes(b"Needle\n")
    (tmp_path / "src" / "a.txt").write_bytes(b"needle\n")
    (tmp_path / "outside.py").write_bytes(b"needle\n")
    registry = ToolRegistry([tool_for(tmp_path)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={
                "query": "needle",
                "path": "src",
                "glob": "*.py",
                "case_sensitive": False,
            },
        )
    )

    data = payload(result.content)
    matches = cast(list[dict[str, object]], data["matches"])
    assert [match["path"] for match in matches] == ["src/a.py"]
    assert data["files_scanned"] == 1


@pytest.mark.asyncio
async def test_search_reports_no_matches(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_bytes(b"content\n")
    registry = ToolRegistry([tool_for(tmp_path)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "absent"},
        )
    )

    assert payload(result.content)["matches"] == []
    assert payload(result.content)["truncated"] is False


@pytest.mark.asyncio
async def test_search_enforces_result_budget(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_bytes(b"x x x x\n")
    registry = ToolRegistry(
        [
            tool_for(
                tmp_path,
                search_limits=SearchLimits(max_results=2),
            )
        ]
    )

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "x", "max_results": 10},
        )
    )

    data = payload(result.content)
    assert len(cast(list[object], data["matches"])) == 2
    assert data["truncated"] is True


@pytest.mark.asyncio
async def test_search_skips_non_text_and_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "good.txt").write_bytes(b"needle")
    (tmp_path / "binary.dat").write_bytes(b"needle\0")
    (tmp_path / "invalid.txt").write_bytes(b"\xff")
    (tmp_path / "large.txt").write_bytes(b"needle too large")
    registry = ToolRegistry([tool_for(tmp_path, max_file_bytes=10)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "needle"},
        )
    )

    data = payload(result.content)
    assert data["files_scanned"] == 1
    assert data["skipped_files"] == 3
    matches = cast(list[dict[str, object]], data["matches"])
    assert [match["path"] for match in matches] == ["good.txt"]


@pytest.mark.asyncio
async def test_search_bounds_long_line_preview(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_bytes(b"a" * 30 + b"needle" + b"z" * 30)
    registry = ToolRegistry(
        [
            tool_for(
                tmp_path,
                search_limits=SearchLimits(
                    max_line_chars=40,
                    max_preview_chars=20,
                ),
            )
        ]
    )

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "needle"},
        )
    )

    data = payload(result.content)
    match = cast(list[dict[str, object]], data["matches"])[0]
    assert len(cast(str, match["preview"])) <= 20
    assert "needle" in cast(str, match["preview"])
    assert data["truncated"] is True


@pytest.mark.asyncio
async def test_search_returns_safe_workspace_error(tmp_path: Path) -> None:
    registry = ToolRegistry([tool_for(tmp_path)])

    result = await registry.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "needle", "path": "../outside"},
        )
    )

    assert result.is_error is True
    error = cast(dict[str, object], payload(result.content)["error"])
    assert error["code"] == "invalid_path"
    assert str(tmp_path.resolve()) not in result.content


@pytest.mark.asyncio
async def test_search_direct_call_rejects_invalid_arguments(tmp_path: Path) -> None:
    tool = tool_for(tmp_path)

    result = await tool.execute(
        ToolCall(
            id="call-1",
            name="search_text",
            arguments={"query": "", "max_results": 0},
        )
    )

    error = cast(dict[str, object], payload(result.content)["error"])
    assert error["code"] == "invalid_arguments"
