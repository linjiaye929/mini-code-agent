# ADR 0013: Use Host-Pinned Stdio MCP Grants

- Status: Accepted
- Date: 2026-07-01

## Context

MCP can expose external Tools through a common protocol, but protocol compatibility does not
establish trust. A local MCP command runs with the Agent user's privileges and can perform work
during startup, before local Tool Policy evaluates a call. A server can also change Tool names,
schemas, descriptions, annotations, and behavior between versions.

The public MCP surface includes local stdio, remote HTTP, OAuth, Resources, Prompts, Roots,
Sampling, Elicitation, Tasks, notifications, pagination, and dynamic Tool lists. Adding all of
these at once would combine process execution, network authorization, prompt injection, delegated
model access, credential handling, and changing capability sets in one boundary.

The official Python SDK v1 is the stable production line. SDK v2 is pre-release and has different
architecture and future protocol targets.

## Decision

M5b supports only direct local stdio Tools through `mcp>=1.28.1,<2`.

The trusted host supplies an immutable profile with:

- an absolute existing executable and exact argv/cwd/environment names;
- explicit connection approval before process creation;
- exact protocol and server identity;
- an exact Tool grant set;
- host descriptions, side-effect classes, and risk levels;
- canonical input/output-schema hashes;
- hard lifecycle and content limits.

The complete observed Tool set must equal the grants. Dynamic lists and pagination are rejected.
Server instructions, annotations, titles, descriptions, and metadata are ignored. Verified MCP
Tools use local aliases and flow through the ordinary Registry, ActionPreview, Hooks, Policy, Tool
approval, and result bounds with `TrustSource.EXTENSION`.

The production adapter owns SDK context managers in a dedicated task so context exit and process
cleanup obey AnyIO task affinity while callers may use or close the proxy from another task.

Remote transports, OAuth, other server features, automatic retries, package installation, and OS
sandbox claims are deferred.

## Consequences

Positive:

- MCP discovery cannot silently add Agent authority;
- package/server/schema drift fails before Tool publication;
- server prompt-like metadata cannot rewrite model-facing Tool definitions;
- process approval and per-call approval remain distinct and understandable;
- the existing Policy/Hook/Trace Tool path remains the single call authority;
- official SDK lifecycle and process-tree behavior are reused instead of hand-rolled JSON-RPC;
- SDK task-affinity details remain inside the adapter.

Negative:

- each approved server upgrade requires reviewing identity and schema hashes;
- local commands must be resolved to absolute executable paths;
- dynamic and paginated Tool servers are unsupported;
- stderr is discarded, reducing production diagnostics;
- calls are serialized and not retried;
- a local process still has the user's OS permissions;
- executable signatures, package provenance, and OS sandboxing are not provided.

## Alternatives Rejected

- **Hand-written JSON-RPC:** duplicates version negotiation, cancellation, protocol types, and
  process shutdown without improving the product boundary.
- **Trust every discovered Tool:** lets server/package replacement create unreviewed authority.
- **Trust server annotations for side effects:** annotations are untrusted hints, not Policy.
- **One approval for connection and all calls:** hides the difference between starting code and
  authorizing a represented action.
- **Repository-defined MCP commands:** lets inspected content execute before Tool Policy.
- **Shell command strings:** introduce expansion and injection; exact argv is required.
- **Remote HTTP in M5b:** requires an independent OAuth, SSRF, redirect, token, and endpoint
  identity design.
- **SDK v2 pre-release:** inappropriate for the project's stable production dependency boundary.
