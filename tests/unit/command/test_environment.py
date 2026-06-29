from mini_code_agent.command.environment import build_minimal_environment


def test_posix_environment_keeps_launch_keys_and_drops_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "LANG": "en_US.UTF-8",
        "TMPDIR": "/tmp",
        "ANTHROPIC_API_KEY": "secret-a",
        "OPENAI_API_KEY": "secret-b",
        "AWS_SECRET_ACCESS_KEY": "secret-c",
        "PROJECT_TOKEN": "secret-d",
    }

    result = build_minimal_environment(source, platform="posix")

    assert result == {
        "HOME": "/home/user",
        "LANG": "en_US.UTF-8",
        "PATH": "/usr/bin",
        "TMPDIR": "/tmp",
    }


def test_windows_environment_lookup_is_case_insensitive_and_canonical() -> None:
    source = {
        "Path": r"C:\Windows\System32",
        "pathext": ".EXE;.CMD",
        "systemroot": r"C:\Windows",
        "TEMP": r"C:\Temp",
        "OPENAI_API_KEY": "secret",
        "RANDOM_VALUE": "drop",
    }

    result = build_minimal_environment(source, platform="windows")

    assert result == {
        "PATH": r"C:\Windows\System32",
        "PATHEXT": ".EXE;.CMD",
        "SYSTEMROOT": r"C:\Windows",
        "TEMP": r"C:\Temp",
    }


def test_empty_environment_stays_empty() -> None:
    assert build_minimal_environment({}, platform="posix") == {}
