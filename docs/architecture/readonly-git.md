# Hardened Read-only Git Evidence

[English](readonly-git.md) | [简体中文](readonly-git.zh-CN.md)

## Purpose

M4a lets the Agent inspect repository state without granting Git mutation. `git_status` returns a
typed porcelain-v2 snapshot; `git_diff` returns a bounded staged or unstaged patch.

## Repository Boundary

`GitClient` resolves the configured Workspace and verifies it against:

```text
git rev-parse --show-toplevel --is-bare-repository
```

The reported top-level must equal the Workspace root. A nested folder inside a parent repository
is rejected because status/diff would otherwise expose paths outside the configured boundary.
Bare repositories and non-repositories are rejected. Linked Git worktrees are supported when
their own top-level matches the Workspace.

## Process Hardening

Commands are argv-only and run through the existing bounded `CommandRunner`. Every invocation
uses:

```text
git --no-pager --no-optional-locks
    -c core.fsmonitor=false
    -c diff.external=
    -C <workspace>
```

Status ignores submodule recursion. Diff adds `--no-ext-diff`, `--no-textconv`, and a final `--`.
These controls prevent the inspected repository from enabling known Git execution extensions for
these operations. A real test configures malicious fsmonitor and external-diff commands and proves
neither executes.

`--no-optional-locks` also prevents refresh-only index writes. Tests compare `.git/index` bytes
and nanosecond modification time before and after status/diff.

## Status Protocol

The client requests:

```text
status --porcelain=v2 -z --branch --untracked-files=all --ignore-submodules=all
```

The parser consumes NUL records and supports:

- ordinary tracked entries;
- rename/copy entries with a second original-path field;
- unmerged entries;
- untracked entries.

Branch OID, head, optional upstream, ahead/behind counts, XY status, submodule state, current path,
and original path are validated. Spaces, tabs, newlines, and leading dashes in paths are data, not
options. Unknown records, malformed metadata, replacement-decoded text, and entry overflow fail
closed.

The result is canonical typed JSON with SHA-256. It is a point-in-time observation and can become
stale immediately.

## Diff Protocol

`git_diff` accepts one strict boolean, `staged`. It invokes:

```text
diff --no-ext-diff --no-textconv --ignore-submodules=all
     --unified=3 [--cached] --
```

The combined process output budget is 2 MiB by default. Character overflow also fails; partial
patches are never returned as complete evidence. Results include mode, patch, byte/character
counts, and SHA-256.

## Failure Contract

Git startup, timeout, overflow, repository mismatch, bare repository, non-zero exit, and malformed
output map to stable `GitErrorCode` values. Public errors contain no command stderr, absolute path,
repository content, or raw exception.

## Non-Claims

- This is not an OS sandbox.
- Git output can expose source code and secrets to the model.
- Status/diff do not lock the working tree against concurrent changes.
- SHA-256 identifies returned evidence; it is not a signature.
- M4a does not stage, commit, reset, clean, checkout, fetch, push, or repair.
- Disabling known extension points does not make arbitrary future Git commands safe by default.
