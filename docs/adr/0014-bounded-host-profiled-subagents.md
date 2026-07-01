# ADR 0014: Use Bounded Host-Profiled Analysis Subagents

- Status: Accepted
- Date: 2026-07-02

## Context

Independent code-reading tasks can be parallelized, and a child Agent can keep exploratory
messages out of the parent transcript. A naive Subagent feature, however, can duplicate parent
authority, inherit unrelated context, recursively create more Agents, start background approval
prompts, swallow cancellation, leak raw Tool content into aggregation, or leave orphan tasks.

The project already has a typed `AgentRuntime`, Tool Registry, Workspace boundary, Policy,
provenance, and deterministic limits. A Subagent design should reuse these contracts rather than
introduce a second execution and authorization system.

Write-capable children introduce a different problem: concurrent mutation, repository identity,
candidate persistence, merge/adoption authority, and cleanup uncertainty. Combining that problem
with initial analysis delegation would make the first boundary too broad.

## Decision

M6a implements host-profiled, in-process, non-recursive analysis children.

The trusted host creates one immutable `SubagentProfile` per parent Tool. It fixes the child
system prompt, exact ordered Tool names, Agent limits, concurrency, deadlines, evidence, summary,
and result budgets. The model supplies only one to four unique bounded tasks plus a reason.

Before any child Provider request, `SubagentSupervisor` creates and validates every child:

- unique host child ID;
- distinct Provider and governed Tool executor;
- exact `READ_ONLY` definitions;
- `governance_enforced is True`;
- `TrustSource.SUBAGENT` for every child Tool;
- no delegation Tool.

Every child gets a fresh one-message context and an independent `AgentRuntime`. All child tasks
belong to one `asyncio.TaskGroup`; a semaphore bounds concurrency, child and batch timeouts are
separate, results are stored by input ordinal, and external `CancelledError` is re-raised.

Children run in `SessionMode.NON_INTERACTIVE`, so a child Policy `ASK` cannot open a nested prompt
and fails closed.

The parent receives only bounded typed projections: untrusted final summaries, usage/counts,
static failure metadata, and SHA-256 evidence for correlated ToolResult content. Lifecycle events
contain metadata and hashes but never task text, prompts, messages, summaries, Tool arguments,
ToolResult content, repository content, or exception text.

M6a is read-only. Worktree-backed implementation children and candidate adoption are deferred to
M6b.

## Consequences

Positive:

- child authority is an exact host capability profile, not copied parent authority;
- parent and sibling transcripts are not implicitly shared;
- Policy can distinguish parent-model calls from delegated calls;
- recursive delegation is structurally unavailable;
- one child timeout/failure does not erase sibling results;
- TaskGroup gives one lexical owner for cancellation and joining;
- input order remains stable under out-of-order completion;
- aggregation does not copy raw repository or ToolResult content;
- parent Policy deny prevents child factories and Provider I/O;
- the existing Agent/Tool/Workspace contracts remain the execution path.

Negative:

- each task adds a Provider session and may increase cost, latency, and rate-limit pressure;
- fresh children repeat context that a forked child might otherwise inherit;
- in-process Provider and Tool implementations share memory and OS authority;
- read-only admission cannot sandbox malicious host code;
- evidence hashes do not validate semantic correctness;
- child events are best-effort and lack durable parent run/turn linkage;
- `NON_INTERACTIVE` means child work requiring approval cannot proceed;
- M6a cannot implement or merge code changes.

## Alternatives Rejected

- **Give children the parent transcript:** leaks unrelated context, weakens attribution, and makes
  context growth implicit.
- **Let the model define child prompts and Tools:** allows model output to create authority.
- **Copy the complete parent Tool Registry:** can expose writes, commands, network access, MCP, or
  delegation without a separate decision.
- **Recursive delegation with a depth counter:** a depth limit does not solve capability
  amplification, cost fan-out, or audit complexity; M6a admits no delegation Tool.
- **Detached `asyncio.create_task`:** permits orphan work and ambiguous cancellation ownership.
- **`asyncio.gather(return_exceptions=True)`:** makes cancellation and task-lifetime invariants
  less explicit than TaskGroup plus typed child outcomes.
- **One timeout only:** cannot distinguish a slow child from a whole-batch deadline.
- **Interactive child approval:** background children must not compete for user prompts or reuse
  parent approval.
- **Return complete child transcripts:** consumes parent context and leaks Tool arguments/results
  rather than a bounded projection.
- **Run every child in a subprocess now:** stronger interpreter isolation also requires
  credential transport, authenticated IPC, Provider lifecycle, process cleanup, and durable
  result protocols; deferred until the in-process contract is stable.
- **Enable writes in the parent checkout:** concurrent children can collide with user work and
  one another. M6b requires host-managed Worktrees and explicit candidate adoption.
- **Adopt a multi-agent framework:** would duplicate or obscure the project's existing
  Provider/Tool/Policy semantics and cancellation evidence.

## Follow-up

M6b may add one write-capable implementation profile only through host-created, locked,
no-checkout Git Worktrees. A child result will produce a bounded candidate snapshot; a separate
parent-side approval will control adoption. No M6b change may weaken M6a's exact profiles,
SUBAGENT provenance, cancellation propagation, or no-recursion rule.
