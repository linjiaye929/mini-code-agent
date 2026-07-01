# Security Policy

## Current Support

The project is pre-alpha. Only the latest commit on `main` is supported.

## Reporting

Do not open a public issue for a vulnerability. Use GitHub private vulnerability reporting
after the repository is published. Until then, contact the repository owner privately.

## Current Boundary

Model output, repository content, project Skills, Tool arguments, test reports, and MCP servers
are untrusted inputs. File, command, Git, test, Repair, and MCP Tool actions pass typed validation,
Policy, and approval where applicable.

M5a Skills are inert Markdown data. Discovery rejects links/reparse points, unsafe YAML, invalid
metadata, conflicts, drift, and resource-limit violations. Parsing or hashing a Skill does not
make its instructions safe, and source labels are not signatures.

M5a Hooks are executable in-process code supplied by the trusted application composition root.
The project does not load or execute Hook code from repository files, Skill metadata, model
output, or environment-selected modules. Pre-Hooks can deny but cannot grant Policy authority;
post-Hook failures cannot rewrite Tool results. A malicious host-registered Hook still has the
Agent process authority.

M5b MCP supports host-configured local stdio Tools only. Before launch, a dedicated approver sees
the exact absolute executable, argv, cwd, and environment variable names; values remain secret.
The executable and cwd reject links/reparse points and are revalidated before process creation.
Initialization pins protocol and server identity; the complete Tool set and canonical input/output
schema hashes must exactly match host grants. Server descriptions, instructions, annotations,
icons, and metadata do not grant authority.

Verified MCP aliases use `TrustSource.EXTENSION` and still pass the ordinary Tool Policy and
optional per-call approval. Connection approval does not approve future Tool calls. Results accept
only bounded text and object-shaped structured JSON; unsupported or oversized content fails
without partial output.

Local MCP processes run with the Agent user's OS privileges and may act during startup before a
Tool call. Stdio limits protocol access but is not a filesystem, network, process, or credential
sandbox. Schema hashes detect reviewed-contract drift, not executable provenance or behavior.
Timeout/cancellation cannot prove that a remote side effect did not complete. The project does not
support remote HTTP/OAuth MCP, package installation, executable signatures, dynamic Tool lists,
Resources, Prompts, Roots, Sampling, Elicitation, or Tasks.

The project does not claim OS-level sandboxing unless an explicit sandbox backend is enabled and
documented. It also does not claim that Hook timeout stops work delegated to another thread or
process, or that SHA-256 establishes extension authorship.
