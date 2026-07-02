# Threat Model

## Protected Assets

- User source code and uncommitted changes.
- Files outside the selected workspace.
- API keys and environment secrets.
- Git history and repository integrity.
- Session, checkpoint, and trace integrity.

## Untrusted Inputs

- Model output and ToolCall arguments.
- Repository files and instructions.
- Skills, hooks, and project configuration.
- MCP servers and their tool results.
- Child Agent output, summaries, and delegated task results.
- Command output and generated patches.

## Initial Controls

- Secret-safe settings and recursive log redaction.
- Explicit configuration precedence.
- No tool execution in M0.
- Side-effecting tools must pass Schema, Workspace, Policy, preview, and approval checks.
- Trace data must be size-limited and redacted.
- M2a read-only tools pass Draft 2020-12 Schema validation and one WorkspaceBoundary.
- Model paths reject cross-platform traversal, link/junction, `.git`, ADS/device, type, size,
  binary, and encoding hazards.
- Workspace traversal and ToolResult content have explicit resource limits.
- M2b write tools use bounded previews, interactive-only approval for `ask`, SHA-256
  preconditions, create-only publication, and same-directory atomic replacement.
- Non-interactive `ask` decisions fail closed without invoking an approval handler.
- M2c commands use argv-only execution, workspace cwd validation, a minimal inherited
  environment, bounded output/time, and process-tree cleanup.
- Execute remains denied by default and requires an explicit tool/executable policy rule.
- M3a applies deterministic request admission before every provider call, keeps ToolCall and
  ToolResult batches atomic, and fails before provider I/O when required context cannot fit.
- Completed write/execute/network and unknown-tool exchanges remain pinned during compaction so
  the model does not lose in-memory evidence of an action and repeat it under a new call ID.
- Compaction markers/events contain bounded counts and a transcript fingerprint, not raw omitted
  content.
- M3b stores only bounded typed lifecycle metadata in SQLite schema version 1; prompts, ToolCall
  arguments, ToolResults, patches, and command output are excluded.
- Required Journal writes precede Provider/Tool work. `ToolStarted` is durable before execution,
  and persistence failure stops all later work with static errors.
- SQLite append and Session/Run projection updates share one `BEGIN IMMEDIATE` transaction with
  WAL, foreign keys, full synchronous writes, bounded busy timeout, and parameterized SQL.
- Event IDs provide exact-payload idempotency. Per-Session sequence and SHA-256 chains detect
  inconsistent rows and projections.
- Explicitly configured Secret values are scrubbed from bounded free-form stop errors before
  hashing and storage.
- M4b test execution uses a host-owned Pytest profile; the model cannot select executables,
  arbitrary options, cwd, environment, timeout, plugins, or report paths.
- Test targets pass WorkspaceBoundary, execute policy, critical preview, and independent approval.
  Ambient plugin autoload and Pytest cache writes are disabled.
- Test time/output and JUnit bytes/cases/diagnostics/text are independently bounded. Reports reject
  unsafe file types, invalid UTF-8, DTD/entities, malformed XML, and contradictory outcomes.
- Pytest process status remains available when its report is missing or invalid; temporary report
  cleanup runs on every exit path.
- M4c requires explicit Repair approval, a clean repository, an exact set of existing regular
  Git-tracked files, a matching Worker scope fingerprint, and a durable `RepairStarted` event
  before Provider or baseline-test work.
- `RepairActionGuard` denies execute/network and out-of-scope writes before ordinary Policy,
  approval, and Tool execution. The coordinator alone invokes the fixed Pytest boundary.
- Every attempt rejects staged, untracked, renamed, conflicted, submodule, branch-changing,
  out-of-scope, empty, repeated, or oversized Git evidence. Workspace file identities are
  revalidated around test execution.
- Repair attempts, elapsed time, patch bytes, Worker prompt characters, and repeated canonical
  failure fingerprints have independent hard limits. Only complete passing host test evidence
  establishes success.
- SQLite schema v3 stores a separate bounded hash-chained Repair lifecycle; interrupted Repair
  rows are not automatically resumed.
- M5a Skills are inert bounded Markdown from explicit roots; source-qualified identity, restricted
  YAML, regular-file checks, fingerprint-required load, and TOCTOU revalidation prevent executable
  registration and silent source shadowing.
- M5a pre-Tool Hooks are host code that may continue to Policy or veto; they cannot grant
  authority. Post-Hook failures cannot replace an actual ToolResult.
- M5b local MCP requires an absolute executable, exact argv/cwd/environment names, explicit
  connection approval, protocol/server identity, a static complete Tool list, host-owned
  side-effect/risk, and canonical input/output-schema hashes.
- Verified MCP aliases use `TrustSource.EXTENSION` and still pass Tool Schema, ActionPreview,
  Hooks, Policy, and optional Tool approval. Result text/JSON, lifecycle deadlines, and SDK
  snapshots have independent limits.
- Server instructions, descriptions, annotations, icons, metadata, stderr, `_meta`, image, audio,
  and resource content do not enter MCP model-facing Tool contracts or successful results.
- M6a Subagents are created only from immutable host profiles. The complete batch validates unique
  bounded tasks and child IDs before child Provider I/O; Providers and Tool executors must be
  distinct objects.
- Every M6a child receives a fresh one-message context and exact read-only definitions. Tool
  executors must prove governance and `TrustSource.SUBAGENT`; recursive delegation is rejected.
- One `asyncio.TaskGroup` owns all child tasks. Per-child and outer batch deadlines produce typed
  ordered results, while external `CancelledError` cancels/joins children and is re-raised.
- Background child `ASK` decisions fail closed in `NON_INTERACTIVE` mode. Parent Policy deny
  prevents every child factory and Provider call.
- Child summaries are explicitly untrusted and bounded. Evidence stores ToolCall identity,
  error/count metadata, and ToolResult SHA-256 only; Subagent events exclude task, prompt,
  message, summary, argument, ToolResult content, repository content, and exception text.
- M6b implementation delegation requires a host-owned immutable profile, an exact clean
  repository/HEAD, a separate non-overlapping state root, a fixed Git executable, path prefixes,
  and hard tree/candidate/cleanup limits.
- The host creates locked detached no-checkout Worktrees and materializes only regular
  `100644`/`100755` index entries from raw Git object bytes. Base identity is persisted as an
  immutable manifest and exact Worktree administrative directory.
- Implementation children use fresh non-interactive contexts and exact SUBAGENT-provenance
  Read/Search/Write/Edit plus optional host-fixed tests. Git, arbitrary commands, network, MCP,
  delegation, nested approval, deletion, rename, and mode changes are unavailable.
- Successful structured mutations form a hash-chained ledger. Candidate readiness is decided by
  independent complete-tree reconciliation against the base manifest, ledger, path allowlist,
  modes, UTF-8/content hashes, and resource limits.
- Ready candidates persist canonical manifests and content-addressed blobs outside the repository.
  Snapshot/cleanup is bounded and shielded during cancellation; identity or removal uncertainty is
  recorded as `cleanup_required`.
- Parent adoption is a separate high-risk WRITE Tool and approval. It atomically claims candidate
  state, requires original clean HEAD, preflights and immediately revalidates every path/hash,
  applies in canonical order, verifies exact final changes, and leaves files unstaged/uncommitted.
- Adoption conflicts perform zero candidate writes. Partial failure triggers reverse rollback and
  records either proven rolled-back or uncertain state; interrupted applying state is classified
  as all-before, all-after, or mixed before any retry.

## Non-claims

- Regex command filtering is not a sandbox.
- Workspace path checks are not process isolation.
- Workspace checks do not eliminate TOCTOU when another process can mutate the tree.
- Hash revalidation narrows but does not eliminate the race between final check and replacement.
- Human approval does not make malicious code safe.
- An approved local process can access host files, network, credentials in files, and other
  resources available to the current OS identity.
- Process groups and tree termination are lifecycle controls, not security isolation; hostile
  detached descendants require an OS sandbox.
- Context fingerprints are not encryption, authentication, redaction, or durable storage.
- M3a pinning reduces in-process repeat-action risk but does not provide crash-safe replay
  prevention or exactly-once side effects; those require M3b/M3c persistence and recovery.
- A provider-neutral UTF-8 estimator is not an exact vendor tokenizer and cannot guarantee
  provider acceptance.
- SQLite WAL/`synchronous=FULL` improve local durability but do not provide replication,
  distributed consistency, or protection from storage failure.
- The Trace hash chain is not signed or authenticated; a writer with database access can rewrite
  payloads and hashes.
- M3c Checkpoints persist full prompts, responses, Tool arguments/results, patches, and command
  output as bounded plaintext. Event Secret scrubbing does not protect Checkpoint payloads.
- Resume rejects Tool-contract or Workspace drift and scans all post-Checkpoint Trace events.
  Any uncheckpointed write, execute, or network Tool blocks automatic replay.
- Provider/read-only replay requires explicit policy and can still duplicate Provider cost or
  observations. No exactly-once or external-system reconciliation is claimed.
- SQLite serializes local Checkpoint claims; it is not a multi-host lease service.
- Read-only Git commands can execute repository-configured fsmonitor, external diff, textconv, or
  submodule behavior unless explicitly disabled. M4a fixes command templates and tests that these
  extension points do not run.
- Git status/diff can contain credentials or proprietary source and are sent to the model as Tool
  results. Their hashes provide identity, not confidentiality or authenticity.
- `--no-optional-locks` prevents optional index refresh, but Git evidence remains a stale-able
  observation under concurrent filesystem mutation.
- An approved Pytest run executes repository tests, `conftest.py`, and host-trusted plugins with
  the Agent OS identity. Fixed argv, minimal environment, approval, and resource limits are not a
  filesystem, process, credential, or network sandbox.
- JUnit is untrusted because test code can tamper with its report. Bounded parsing does not prove
  report provenance or prevent tests from exfiltrating data through stdout/stderr.
- Exact managed-report path/name echoes are replaced before ToolResult serialization, but hostile
  tests can encode or transform the value; output replacement is not a data-loss-prevention
  boundary.
- Disabling `.pytest_cache` prevents a harness-created cache only; project tests may still modify
  the Workspace or host.
- Lifecycle Trace excludes test payloads, but stable Checkpoints contain complete bounded
  ToolResults as plaintext.
- Repair clean/tracked/scope checks are final-state observations, not isolation. Malicious tests
  or concurrent host processes can transiently change and restore files, read host resources, or
  bypass Tool governance.
- `-B` prevents ordinary Python bytecode cache writes but does not make test execution read-only.
- Repair failure leaves accepted working-tree changes for inspection. M4c does not reset, clean,
  checkout, stash, stage, commit, or isolate work in a Worktree.
- Repair lifecycle events exclude prompts, patches, diagnostics, stdout, stderr, and ToolResults,
  but the separate Agent Checkpoint can contain that bounded plaintext.
- An incomplete Repair trace is not proof that no side effect occurred, and M4c provides no
  automatic crash Resume, rollback, or external exactly-once guarantee.
- Configured-value scrubbing cannot detect unknown secrets, and SQLite is not encrypted at rest.
- MCP connection and schema equality do not establish executable provenance, implementation
  safety, read-only behavior, or sandboxing. A local server can act with user privileges during
  startup before any Tool Policy decision.
- Stdio restricts protocol access to child pipes but not filesystem, network, process, or
  credential authority. Timeout/termination cannot prove a remote side effect did not complete.
- In-process Subagents isolate Agent message context, not Python memory, operating-system identity,
  credentials, Provider access, or malicious host-supplied code.
- M6a read-only admission governs calls through the child executor; it is not an OS sandbox and
  cannot prove a host Tool implementation is actually side-effect free.
- Child result/evidence hashes are deterministic equality fingerprints, not signatures,
  encryption, provenance, semantic correctness, or durable parent-child audit.
- M6a child deadlines depend on cooperative asyncio cancellation and do not stop arbitrary threads
  or prove that an external Provider request incurred no cost.
- M6a remains read-only; M6b does not weaken its exact capability, no-recursion, or cancellation
  boundary.
- A Git Worktree separates checkout paths. It does not isolate Python memory, OS identity,
  credentials, filesystem, processes, network, or malicious host-supplied Provider/Tool code.
- No-checkout materialization avoids working-tree conversion during population but does not prove
  repository content is safe or trustworthy.
- Candidate manifests, ledgers, Git IDs, and SHA-256 values are equality fingerprints, not
  signatures, provenance, confidentiality, semantic correctness, or proof of test success.
- Git clean/hash checks and immediate path revalidation narrow but cannot eliminate races with a
  concurrent process that has the same filesystem authority.
- Adoption is process-serialized and rollback-aware, not power-loss atomic, distributed, or
  exactly-once. A mixed or unverifiable state intentionally becomes `uncertain`.
- M6b supports bounded additions/modifications only. It does not delete, rename, stage, commit,
  merge, push, reset, clean, automatically adopt, durably resume a child, or recursively delegate.
- M6b does not claim lower token use, latency, cost, higher quality, or throughput without a
  separate reproducible benchmark.
