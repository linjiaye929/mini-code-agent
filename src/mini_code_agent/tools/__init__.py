from typing import TYPE_CHECKING, Any

from mini_code_agent.tools.read_file import ReadFileTool
from mini_code_agent.tools.registry import RegisteredTool, ToolRegistry
from mini_code_agent.tools.search_text import SearchTextTool

if TYPE_CHECKING:
    from mini_code_agent.tools.edit_file import EditFileTool
    from mini_code_agent.tools.run_command import RunCommandTool
    from mini_code_agent.tools.write_file import WriteFileTool

__all__ = [
    "EditFileTool",
    "ReadFileTool",
    "RegisteredTool",
    "RunCommandTool",
    "SearchTextTool",
    "ToolRegistry",
    "WriteFileTool",
]


def __getattr__(name: str) -> Any:
    if name == "EditFileTool":
        from mini_code_agent.tools.edit_file import EditFileTool

        return EditFileTool
    if name == "WriteFileTool":
        from mini_code_agent.tools.write_file import WriteFileTool

        return WriteFileTool
    if name == "RunCommandTool":
        from mini_code_agent.tools.run_command import RunCommandTool

        return RunCommandTool
    raise AttributeError(name)
