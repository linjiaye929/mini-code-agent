# Mini CodeAgent

A framework-light, provider-neutral coding agent built from first principles.

> Status: pre-alpha. M1 provides a provider-neutral, bounded Agent Core with a scripted
> provider and read-only runtime tool. It does not yet connect to live models or file tools.

## Requirements

- Python 3.12 or 3.13
- uv 0.11.25

## Development

```powershell
uv sync --all-groups
uv run mini-code-agent --version
uv run mini-code-agent doctor
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv build --build-constraint build-constraints.txt --require-hashes
```

If `uv` was installed by `pip` but is not available on the Windows `PATH`, use
`python -m uv` in the commands above.

## Configuration

Precedence is:

```text
defaults < TOML file < MINI_CODE_AGENT_* environment variables < CLI overrides
```

Default config paths follow the operating system conventions provided by Platformdirs.
Secrets are accepted from environment variables but are never printed by `doctor`.
See `config.example.toml` and `.env.example` for supported inputs.

## Documentation

- Product design: `docs/superpowers/specs/2026-06-29-mini-code-agent-design.md`
- Learning map: `docs/learning/knowledge-map.md`
- Learning evidence: `docs/learning/progress.md`
- Resume evidence: `docs/resume/project-profile.md`
- Agent Core: `docs/architecture/agent-core.md`
- Threat model: `docs/architecture/threat-model.md`

## License

Apache-2.0
