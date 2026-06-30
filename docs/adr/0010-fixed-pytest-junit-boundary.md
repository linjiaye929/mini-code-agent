# ADR 0010: Use a Fixed Pytest Profile and Bounded JUnit Boundary

- Status: Accepted
- Date: 2026-06-30
- Target: `v0.11.0-alpha.0`

## Context

The Agent needs machine-readable test feedback after editing files. Reusing `run_command` would let
the model construct arbitrary executables and options, while parsing Pytest terminal output would
couple the Agent to presentation text, verbosity, plugins, and localization.

Any test runner executes repository code. The design must preserve independent policy approval,
bound process resources, avoid ambient plugin expansion, and treat generated diagnostics as
untrusted because the test process can modify its own report.

## Decision

Add a dedicated `run_tests` Tool with `SideEffect.EXECUTE` and `RiskLevel.CRITICAL`.

- The host configures an absolute Python executable, timeout, failure cap, default targets, and
  trusted plugin modules.
- The model supplies only optional workspace-relative targets and a bounded reason.
- The harness builds one argv-only `python -I -B -m pytest` command.
- Entry-point plugin autoload, Python bytecode writes, and Pytest cache writes are disabled.
- Every model target is resolved through `WorkspaceBoundary`; `--` terminates options.
- Pytest's built-in JUnit XML is read with byte, case, diagnostic, and text limits.
- DTD/entity declarations, malformed outcomes, unsafe files, and invalid encoding fail closed.
- Process and report status remain separate so report corruption does not discard execution
  evidence.
- The report path is temporary, omitted as a result field, exact echoed forms are replaced with a
  stable marker, and the file is cleaned on every exit path.
- Execute remains denied by default and requires an explicit rule plus approval.

## Alternatives

### Parse Pytest terminal output

Rejected. Terminal text is not a stable protocol and changes with verbosity, traceback mode,
plugins, localization, and Pytest releases.

### Expose a constrained `run_command` preset

Rejected. It would duplicate validation inside a generic arbitrary-command schema and still expose
model-controlled argv fields that are irrelevant to test selection.

### Install a Mini Code Agent Pytest plugin

Deferred. A plugin could emit a custom event protocol, but import-path shadowing and plugin
compatibility would become additional execution boundaries. Built-in JUnit is sufficient for
bounded failure diagnostics.

### Depend on `pytest-json-report`

Rejected for the initial release. It would require a third-party plugin in every target Python
environment and expand the compatibility and supply-chain surface.

### Claim approval as a sandbox

Rejected. Authorization and isolation solve different problems. Repository tests retain the OS
permissions of the Agent process.

## Consequences

Positive:

- The model cannot turn test selection into arbitrary command construction.
- Typed statuses distinguish test failures, runner failures, and report failures.
- JUnit parsing is deterministic and independent of terminal presentation.
- Ambient plugins and `.pytest_cache` do not silently expand harness behavior.
- Existing Policy, process cleanup, Tool Registry, Agent Runtime, and Trace boundaries are reused.

Negative:

- Projects needing third-party plugins require explicit host profile configuration.
- Project `conftest.py` and tests still execute arbitrary code.
- JUnit details are less rich than a dedicated event protocol.
- Test code can tamper with the report; the parser guarantees bounded handling, not provenance.
- M4b provides diagnostics only; M4c composes this boundary under a separate host-controlled
  Repair runtime.
