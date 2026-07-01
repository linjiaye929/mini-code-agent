import shutil
import subprocess

from mini_code_agent import __version__
from mini_code_agent.hooks import ToolHookRunner
from mini_code_agent.mcp import (
    MCP_PROTOCOL_VERSION,
    McpLimits,
    McpStdioClient,
    schema_sha256,
)
from mini_code_agent.repair import (
    AgentRepairWorker,
    RepairActionGuard,
    RepairRuntime,
)
from mini_code_agent.skills import SkillCatalog
from mini_code_agent.testing import PytestRunner
from mini_code_agent.tools import RunTestsTool


def verify_installed_package() -> None:
    assert AgentRepairWorker.__name__ == "AgentRepairWorker"
    assert RepairActionGuard.__name__ == "RepairActionGuard"
    assert RepairRuntime.__name__ == "RepairRuntime"
    assert SkillCatalog.__name__ == "SkillCatalog"
    assert ToolHookRunner.__name__ == "ToolHookRunner"
    assert McpStdioClient.__name__ == "McpStdioClient"
    assert McpLimits().max_tools == 32
    assert MCP_PROTOCOL_VERSION == "2025-11-25"
    assert len(schema_sha256({"type": "object"})) == 64
    assert PytestRunner.__name__ == "PytestRunner"
    assert RunTestsTool.__name__ == "RunTestsTool"
    executable = shutil.which("mini-code-agent")
    assert executable is not None
    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__


def test_installed_package_starts() -> None:
    verify_installed_package()


if __name__ == "__main__":
    verify_installed_package()
