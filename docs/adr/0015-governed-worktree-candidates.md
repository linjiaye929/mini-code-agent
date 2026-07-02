# ADR 0015: Separate Implementation Candidates from Parent Adoption

- Status: Accepted
- Date: 2026-07-02

## Context

M6a proved that an analysis child can use a fresh context, exact host-owned read-only Tools,
structured concurrency, and bounded evidence. Allowing that child to write directly into the
parent checkout would introduce a different authority boundary: repository identity, dirty user
work, concurrent mutations, partial filesystem failure, durable candidate state, cleanup, and
approval to publish a change.

A Git Worktree gives a separate checkout path but does not by itself provide a safe candidate
protocol. Ordinary checkout may execute conversion configuration. Child self-reported diffs
cannot be trusted. Automatically copying the result back would combine implementation and
publication authority and could overwrite user work.

## Decision

M6b uses a two-phase host-governed design.

First, `delegate_implementation` creates one host-managed locked detached no-checkout Worktree
lease from an exact clean `HEAD`. The host reads index pointers and raw Git objects with fixed,
byte-safe argv, materializes only regular files, and records an immutable base manifest.

The implementation child runs with a fresh context, exact SUBAGENT-provenance Read/Search/
Write/Edit Tools, optional host-fixed tests, non-interactive policy, and no Git, arbitrary command,
network, MCP, delegation, or parent approval. Successful structured mutations form a hash-chained
ledger.

After child completion, the host independently reconciles the complete lease tree, base manifest,
ledger, path allowlist, modes, content, and resource budgets. A verified candidate stores a
canonical manifest plus content-addressed blobs outside the repository. The Worktree is then
verified and removed. Rejected/no-change/cleanup-required outcomes remain distinct.

Second, `adopt_subagent_candidate` is a separate high-risk WRITE Tool and approval. It atomically
claims a ready candidate, revalidates exact repository/base/clean state and every destination,
stages same-directory temporary files, applies canonical replacements, and verifies the final
changed set and hashes. Conflicts write nothing. Partial failures roll back in reverse order and
become either proven rolled-back or uncertain. Interrupted applying state is classified as
all-before, all-after, or mixed. Discard is separately governed and only accepts ready candidates.

The initial release supports additions and modifications only. It never stages, commits, merges,
pushes, resets, or cleans the parent checkout.

## Consequences

Positive:

- child implementation cannot directly mutate the user's checkout;
- child completion and parent publication require separate Policy/approval decisions;
- materialization avoids working-tree checkout filters and uses an exact index snapshot;
- candidate authority comes from independent tree reconciliation and stored blobs, not model text;
- stale base, dirty parent, path drift, and hash drift fail before the first candidate write;
- process-local adoption has deterministic conflict, rollback, uncertain, and recovery states;
- failed child work can be cleaned without granting merge or Git authority;
- M6a's read-only profile and no-recursion guarantees remain unchanged.

Negative:

- Git object-format support is initially limited to SHA-1;
- only regular UTF-8 additions/modifications with supported modes can become ready;
- repository-sized materialization is bounded but can still cost time and disk space;
- Worktrees isolate paths, not process memory, credentials, filesystem, or network;
- cleanup and adoption are not crash-atomic or distributed transactions;
- an uncertain candidate requires operator inspection rather than automatic retry;
- the first release runs one implementation child per ToolCall and provides no automatic merge.

## Alternatives Rejected

- **Write directly in the parent checkout:** can collide with user changes and combines child
  implementation with publication authority.
- **Create a branch and auto-merge:** grants Git mutation and merge authority without a separate
  review/adoption decision.
- **Use ordinary `git worktree add` checkout:** may invoke configured working-tree conversions and
  makes exact byte provenance harder to constrain.
- **Trust `git diff` or child summary as the candidate:** bounded presentation text is not an
  adoption source of truth and can omit or misstate files.
- **Copy the complete Worktree back:** cannot enforce exact path, mode, content, or conflict
  preconditions.
- **Adopt immediately after child success:** conflates model completion with verified readiness
  and user approval.
- **Use only filesystem backups:** lacks a durable candidate state machine and clear recovery
  classification.
- **Call adoption atomic:** multiple filesystem replacements cannot provide power-loss atomicity
  without a stronger transactional storage design.
- **Give the child Git or arbitrary shell:** expands authority beyond bounded source changes and
  host-fixed tests.
- **Run multiple implementation children into one candidate:** introduces ordering and conflict
  semantics that are intentionally deferred.

## Follow-up

Future work may add deletion/rename, stronger process isolation, non-SHA-1 repositories, durable
operator recovery commands, candidate review UI, or multi-candidate composition. Each requires a
new threat analysis and must not weaken exact base/path/hash validation or separate adoption
approval.
