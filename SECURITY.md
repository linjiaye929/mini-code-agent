# Security Policy

## Current Support

The project is pre-alpha. Only the latest commit on `main` is supported.

## Reporting

Do not open a public issue for a vulnerability. Use GitHub private vulnerability reporting
after the repository is published. Until then, contact the repository owner privately.

## Current Boundary

Model output, repository content, project Skills, Tool arguments, test reports, MCP servers, child
Agent output, and child summaries are untrusted inputs. File, command, Git, test, Repair, MCP, and
Subagent Tool actions pass typed validation, Policy, and approval where applicable.

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

M6a analysis Subagents use immutable host profiles, fresh child contexts, exact read-only Tool
sets, independent Agent/result budgets, and `TrustSource.SUBAGENT`. Every child Tool executor must
prove governance and SUBAGENT provenance before any child Provider request. Child sessions are
non-interactive, so `ASK` fails closed instead of opening nested approval. Parent Policy deny
prevents child factories and Provider I/O.

All child tasks belong to one `asyncio.TaskGroup`; child and batch deadlines are separate, and
external cancellation is re-raised after children are cancelled and joined. Results expose only
bounded untrusted summaries and ToolResult metadata/SHA-256 evidence. Subagent events exclude
tasks, prompts, messages, summaries, Tool arguments/results, repository content, and exception
text.

M6a children are in-process and are not an OS, process, memory, credential, or network sandbox.
Read-only Tool admission does not constrain malicious host-supplied Provider or Tool code.
Evidence hashes are not signatures, semantic validation, confidentiality, or durable audit.
M6a remains read-only and does not support child writes, command/network Tools, recursive
delegation, Worktrees, candidate adoption, merge, rollback, or exactly-once execution.

M6b implementation delegation uses a separate immutable host profile. It pins an exact clean
repository and `HEAD`, an external non-overlapping state root, absolute Git executable, allowed
path prefixes, implementation Tool set, and hard tree/candidate/cleanup limits. The host creates
a locked detached no-checkout Worktree and materializes only regular `100644`/`100755` index
entries from raw Git object bytes. Ignored/untracked files, links, gitlinks, unsupported modes,
case aliases, and over-budget trees do not enter the lease.

Implementation children are fresh, non-interactive, and limited to SUBAGENT-provenance
Read/Search/Write/Edit plus optional host-fixed tests. They receive no Git, arbitrary command,
network, MCP, recursive delegation, deletion, rename, or parent approval authority. Successful
structured mutations form a hash-chained ledger, but candidate readiness is decided by an
independent complete-tree reconciliation against the immutable base manifest, ledger, path
allowlist, modes, content hashes, and resource limits.

Ready candidates persist canonical manifests and content-addressed blobs outside the repository.
Child completion never mutates the parent checkout. Adoption requires a separate high-risk WRITE
Tool, Policy decision, and approval. It revalidates the original clean `HEAD`, every path and
before-hash, applies only the verified candidate set, verifies after-hashes, and leaves changes
unstaged and uncommitted. Conflicts write no candidate files. Partial failure is either proven
rolled back or recorded as uncertain; interrupted applying state is classified before reuse.

Worktree path separation is not an OS, process, memory, credential, filesystem, or network
sandbox. In-process trusted Provider/Tool code retains the Agent process authority. Clean/hash
checks narrow but do not eliminate races with another process. Multi-file adoption is
process-serialized and rollback-aware, not power-loss atomic, distributed, exactly-once, or a
database two-phase commit. M6b does not delete/rename files, automatically adopt, stage, commit,
merge, push, reset, clean, or durably resume a child.

The project does not claim OS-level sandboxing unless an explicit sandbox backend is enabled and
documented. It also does not claim that Hook timeout stops work delegated to another thread or
process, or that SHA-256 establishes extension authorship.
