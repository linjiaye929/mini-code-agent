from importlib.resources import files

from mini_code_agent import __version__


def test_package_exports_release_version() -> None:
    assert __version__ == "0.14.0a0"


def test_package_includes_pep561_marker() -> None:
    marker = files("mini_code_agent").joinpath("py.typed")

    assert marker.is_file()
