from typer.testing import CliRunner

from mini_code_agent import __version__
from mini_code_agent.cli import app


def verify_installed_package() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_installed_package_starts() -> None:
    verify_installed_package()


if __name__ == "__main__":
    verify_installed_package()
