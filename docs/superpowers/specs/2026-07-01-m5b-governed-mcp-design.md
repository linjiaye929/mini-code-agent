# M5b Governed MCP Stdio Design

## Goal

Connect host-approved local MCP servers through the official stable Python SDK while preserving
the Agent's existing monotonic authorization model. MCP tools become ordinary registered tools,
but process launch, protocol negotiation, tool discovery, every tool call, and every returned
content block are independently bounded and fail closed.

M5b is a library API for local `stdio` servers. It does not add Streamable HTTP, SSE, OAuth,
server registries, one-click repository configuration, arbitrary shell commands, Resources,
Prompts, Roots, Sampling, Elicitation, Tasks, dynamic tool refresh, server-provided instructions,
or OS-level sandboxing.

## Approaches Considered

### Hand-roll JSON-RPC and the MCP lifecycle

A minimal client could write newline-delimited JSON to a subprocess. That would reduce the
dependency graph, but it would duplicate protocol version negotiation, cancellation, error
mapping, process-tree shutdown, and evolving MCP types. Rejected because protocol plumbing is not
the project's differentiator and a partial implementation would be less secure.

### Expose every tool returned by any configured MCP server

This matches permissive MCP hosts: connect, trust `tools/list`, and publish whatever appears.
It lets a replaced package add a powerful tool, change a schema, or relabel side effects without
host review. Rejected because remote discovery must not create Agent authority.

### Host-pinned profiles over the official stable SDK

The selected design uses `mcp>=1.28.1,<2`, the stable v1 line. A host-created profile pins one
exact executable/argument vector, working directory, environment names, expected protocol and
server identity, and an exact grant set. The client starts only after explicit connection
approval, verifies initialization and tool contracts, and adapts the approved tools into the
existing Registry and `GovernedToolExecutor`.

## Security Invariants

1. MCP configuration is trusted host composition. M5b never reads server commands or grants from
   a repository, Skill, model message, MCP server, environment-discovered file, or remote registry.
2. A server command is an absolute, existing, executable, unlinked regular file plus an exact
   argument tuple. Command and working-directory paths are revalidated immediately before launch.
   It is never passed through `cmd.exe`, PowerShell, `sh`, a command string, URL opener, PATH
   resolution, or shell expansion.
3. A new server process cannot start until a dedicated connection approver sees the complete,
   untruncated command, working directory, and environment variable names and explicitly approves.
4. Environment values are `SecretStr`, never shown in approval requests, public errors, tool
   definitions, audit-safe snapshots, `repr`, or model context. The SDK's small platform default
   environment is inherited; only explicitly configured keys are added.
5. The profile pins one protocol version and exact server name/version. Initialization mismatch,
   missing Tools capability, unsupported capability policy, timeout, malformed response, or
   disconnect closes the process and exposes no tools.
6. Client callbacks for Roots, Sampling, Elicitation, and logging are not installed. Server
   `instructions`, descriptions, titles, icons, annotations, metadata, and notifications never
   enter the system prompt or grant authority.
7. Every admitted remote tool has a host grant with exact remote name, local alias, host-owned
   description, side-effect class, risk level, input-schema SHA-256, and optional output-schema
   SHA-256. Risk and side effect never come from server annotations.
8. The complete discovered tool name set must equal the grant set. Missing, duplicate, unexpected,
   paginated, renamed, or dynamically changed tools fail connection rather than being partially
   exposed.
9. Every remote input and output schema is valid JSON Schema and is hashed from canonical JSON.
   The observed hash must match the grant before a `ToolDefinition` exists.
10. Local aliases are the only names shown to the model. They use the project's bounded lowercase
    Tool naming contract and must be unique across the final Registry.
11. MCP tools implement the ordinary `RegisteredTool` contract. Calls therefore follow schema
    validation, ActionPreview, ActionGuard, Hooks, Policy, optional per-call approval, remote call,
    post-Hooks, and Tool result bounds.
12. MCP Tool Policy requests use `TrustSource.EXTENSION`, resolved from trusted host metadata per
    local tool name. Connection approval cannot bypass per-tool Policy or approval.
13. Startup, initialization, listing, calls, and close have independent hard timeouts.
    `asyncio.CancelledError` propagates and context-manager cleanup is always attempted.
14. Tool output accepts only bounded text blocks and bounded JSON `structuredContent`. Image,
    audio, resource-link, embedded-resource, task, `_meta`, and unknown content are rejected as an
    unsupported result; no partial result is returned.
15. When an output schema is granted, `structuredContent` is required and revalidated locally.
    If no output schema is granted, structured content is still size/depth bounded but does not
    acquire trusted meaning.
16. Protocol, SDK, subprocess, schema, timeout, and content errors map to static public error codes.
    Raw exceptions, absolute paths, environment values, stderr, protocol frames, and server
    messages are not returned to the model.
17. Server stderr is discarded in M5b. This prevents unbounded buffering and secret leakage into
    logs, at the cost of reduced diagnostics.
18. Closing a client makes all adapted tools unavailable. A timed-out or cancelled mutating call
    is reported as having uncertain completion; the client never claims the remote side effect was
    rolled back.

## Stable Dependency and Protocol Baseline

M5b pins:

- Python SDK: `mcp>=1.28.1,<2`;
- expected protocol: `2025-11-25`;
- transport: direct local `stdio`;
- JSON Schema validation: existing `jsonschema` Draft 2020-12 support.

The SDK v1 branch remains the production recommendation while v2 is pre-release. The upper bound
prevents a future stable v2 from silently changing lifecycle and type behavior.

The profile's protocol version must equal the package-supported version constant. A later release
may add an explicit compatibility table; M5b does not negotiate down to older profiles.

## Host Configuration Contract

### `McpToolGrant`

An immutable Pydantic model contains:

- `remote_name`: exact case-sensitive MCP tool name, 1-128 allowed MCP characters;
- `local_name`: bounded Mini Code Agent Tool alias;
- `description`: host-owned model-facing description;
- `side_effect`: host classification;
- `risk`: host classification;
- `input_schema_sha256`: lowercase SHA-256;
- `output_schema_sha256`: lowercase SHA-256 or `None`.

The grant does not accept server annotations, titles, descriptions, or executable metadata.

### `McpServerProfile`

An immutable Pydantic model contains:

- `server_id`: stable bounded host identifier;
- `command`: absolute existing executable regular-file path;
- `args`: bounded tuple of bounded tokens;
- `cwd`: existing absolute non-symlink directory selected by the host;
- `environment`: bounded map from portable variable names to `SecretStr`;
- `expected_protocol_version`;
- `expected_server_name`;
- `expected_server_version`;
- one to 32 unique grants;
- bounded startup, list, call, and close timeouts;
- bounded tool count, schema bytes, result bytes, text blocks, text characters, JSON depth, and JSON
  node limits.

The profile rejects relative, missing, linked/reparse, non-regular, and non-executable command
paths. It revalidates command and working directory before process creation to catch path drift.
Shell metacharacters in argument tokens are data because no shell is used. Empty/NUL-containing
tokens are rejected. The profile retains no "auto approve" or "trust server annotations" switch.

## Connection Approval

`McpConnectionApprovalRequest` is an immutable public-safe projection containing:

- server ID;
- exact `command` tuple;
- display working directory;
- sorted environment key names;
- static warning that the process receives the Agent user's operating-system privileges.

`McpConnectionApprover` is a trusted async protocol. `McpStdioClient.connect(...)` calls it before
constructing SDK server parameters or entering the stdio context. False, exception, timeout, or
malformed return fails closed with `connection_not_approved`.

Approval is intentionally separate from `ApprovalHandler`: connection approval authorizes
starting one long-lived external process; Tool approval authorizes one represented action after a
validated ActionPreview. Conflating them would make either prompt misleading.

## SDK Boundary

### `mini_code_agent.mcp.sdk`

An internal `McpSessionFactory` protocol isolates SDK-specific values from the core client:

```python
class McpSession(Protocol):
    async def initialize(self) -> McpInitializeSnapshot: ...
    async def list_tools(self) -> McpToolPage: ...
    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult: ...
    async def aclose(self) -> None: ...


class McpSessionFactory(Protocol):
    async def open(self, profile: McpServerProfile) -> McpSession: ...
```

The production factory owns `stdio_client` and `ClientSession` inside one dedicated asyncio worker,
because the SDK's AnyIO contexts must exit in the task that entered them. The public session proxy
can be called or closed from another task; close signals the owner worker, which performs SDK
process-tree shutdown. No optional client callbacks are installed, stderr uses `os.devnull`, and
SDK Pydantic objects become small internal snapshots. Raw SDK objects never reach Agent code.

Tests can use a deterministic fake session. A separate real stdio integration test still proves
the official SDK transport, handshake, listing, call, and shutdown path.

### Timeouts and cancellation

The client owns outer `asyncio.timeout(...)` boundaries even though SDK v1 can accept a read
timeout. This covers process creation, initialization, list processing, and adapter code in one
deadline. On timeout, it calls `aclose()` under the close budget, then returns a static error.

The production session also configures SDK per-request read timeouts where supported so a timed-out
request emits protocol cancellation. Cancellation from the application is re-raised after
shielded bounded cleanup.

## Contract Verification

After initialization:

1. require exact protocol version;
2. require exact server name and version;
3. require Tools capability;
4. reject task-required tools and `tools.listChanged=true` for M5b;
5. ignore server instructions and all unrelated server capabilities.

Tool listing is one page only. Any `nextCursor` is rejected. The client then:

1. bounds serialized response size and tool count;
2. rejects duplicate remote names;
3. requires exact equality between observed remote names and grant names;
4. validates and canonicalizes each input and optional output schema;
5. compares each canonical SHA-256 with its grant;
6. creates local definitions from the grant description, side effect, and observed pinned input
   schema;
7. creates one immutable adapter per grant.

Canonical schema hashing uses UTF-8 JSON with sorted keys, compact separators, `ensure_ascii=True`,
and no NaN/Infinity. Hashes establish contract identity, not server authorship or safety.

## Tool Adapter and Governance

`McpTool` stores a connected client reference, one grant, and one verified definition.

`preview(...)` returns:

- local Tool name and granted side effect;
- granted risk;
- a static bounded summary;
- resource `mcp://<server_id>/tools/<remote_name>`;
- no fake shell command or diff.

`execute(...)` forwards only the already schema-validated argument object to the connected client.
It maps connection state and remote failures to static errors. It never reconnects implicitly,
refreshes grants, or retries a side-effecting call.

`GovernedToolExecutor` gains an optional immutable `trust_sources` mapping keyed by registered local
Tool name. Unknown keys are rejected. Existing callers keep the constructor-wide default; MCP
composition marks every MCP alias as `TrustSource.EXTENSION`. The resolved source is used for Hook
context and Policy evaluation.

## Result Normalization

The SDK snapshot contains only:

- ordered text strings;
- optional JSON `structured_content`;
- `is_error`.

Normalization first enforces block count and text/JSON aggregate limits. It rejects any unsupported
content block before retaining text, preventing partial-success truncation. It validates JSON
depth, node count, string sizes, object key sizes, and finite numbers.

The model-facing `ToolResult.content` is deterministic compact JSON:

```json
{
  "content_type": "mcp_tool_result",
  "server_id": "local-git",
  "tool": "status",
  "text": ["clean"],
  "structured_content": {"clean": true}
}
```

`structured_content` is omitted when absent. Remote `isError=true` maps to
`ToolResult.is_error=true` but keeps bounded text so the model can correct inputs. Protocol and
client validation errors use the project's static error envelope.

## Lifecycle and State

`McpStdioClient` is an async context manager with states:

```text
new -> approving -> connecting -> verifying -> ready -> closing -> closed
                         \---------- failure ----------/
```

- `connect()` is single-use;
- tools are available only in `ready`;
- concurrent tool calls are serialized in M5b because server concurrency semantics are unknown;
- close is idempotent;
- a server disconnect marks the client failed and future calls do not restart it;
- connection failure never leaves partially admitted Tool definitions.

Serializing calls is a conservative throughput tradeoff. A later profile may explicitly grant
concurrency after idempotency and cancellation semantics are defined.

## Package Boundaries

### `mini_code_agent.mcp.models`

Defines profiles, grants, approval request, lifecycle state, snapshots, limits, and typed public
error codes. It freezes mappings and validates uniqueness/bounds.

### `mini_code_agent.mcp.contracts`

Validates JSON Schemas, computes canonical hashes, compares listing/grants, and builds verified
Tool definitions. It has no process or SDK dependency.

### `mini_code_agent.mcp.sdk`

Defines the internal session/factory protocols and the official SDK v1 stdio implementation.

### `mini_code_agent.mcp.client`

Owns approval, lifecycle, deadlines, contract verification, serialized calls, cleanup, and static
failure mapping.

### `mini_code_agent.mcp.tools`

Implements `McpTool`, previews, deterministic result normalization, and adapter construction.

### `mini_code_agent.mcp.__init__`

Exports only the stable host API. Raw SDK types and internal protocol snapshots remain private.

## Failure Semantics

Connection failures raise `McpConnectionError` with one code and one static public message.
Expected codes include:

- `connection_not_approved`;
- `connection_timeout`;
- `connection_failed`;
- `identity_mismatch`;
- `protocol_mismatch`;
- `tools_capability_missing`;
- `dynamic_tools_unsupported`;
- `tool_contract_mismatch`;
- `tool_schema_invalid`;
- `tool_listing_too_large`;
- `unsupported_server_feature`;
- `close_failed`.

Tool calls never raise expected remote failures into the Agent. They return static errors:

- `mcp_not_connected`;
- `mcp_tool_timeout`;
- `mcp_tool_failed`;
- `mcp_tool_result_invalid`;
- `mcp_tool_result_too_large`;
- `mcp_tool_result_unsupported`;
- `mcp_tool_completion_unknown`.

For timeout, cancellation, transport loss, or close during an EXECUTE/WRITE/NETWORK call, public
wording states that completion is unknown. Read-only failures can use ordinary failure wording.

## Testing Strategy

### Models and contract tests

- exact field bounds, frozen secret map, duplicate grant/name rejection;
- command/cwd/environment validation and safe approval projection;
- canonical schema hash stability and non-finite JSON rejection;
- invalid schema, hash mismatch, missing/unexpected/duplicate/paginated tools;
- server identity, protocol, capability, and dynamic-list rejection;
- host description/side-effect/risk wins over remote metadata.

### Lifecycle tests with fake sessions

- approval occurs before factory open and denied approval never opens;
- approval exception/timeout/malformed return fails closed;
- startup, initialize, list, call, and close deadlines;
- cleanup after every failure and cancellation;
- single-use connect, idempotent close, no partial definitions;
- serialized calls and no retries;
- disconnect leaves client unavailable;
- public errors contain no command path, secret value, stderr, or raw exception.

### Tool and governance tests

- exact local definitions and remote-name routing;
- preview uses host side effect/risk and stable MCP resource;
- `TrustSource.EXTENSION` reaches Hooks and Policy;
- Policy deny/ask occurs before remote call;
- connection approval does not bypass Tool approval;
- unsupported result blocks, `_meta` omission, text/JSON/depth/node/byte limits;
- output-schema required and locally revalidated;
- remote business error remains a bounded corrective Tool result;
- timeout/cancellation produces completion-unknown semantics for side effects.

### Real stdio integration

A tiny test server built with the official SDK exposes one read-only deterministic tool. On POSIX,
the test creates a regular executable launcher whose shebang targets the active environment because
the venv Python path is normally a symlink; on Windows it uses the resolved Python executable. The
production factory verifies handshake identity and schema hashes, executes through `AgentRuntime`
plus `GovernedToolExecutor`, and confirms process shutdown. A sibling malicious fixture proves
unexpected tool and schema drift are rejected.

### Release gates

- Python 3.12 and 3.13 full suites on Windows and Ubuntu;
- Ruff format/check and strict Pyright;
- branch-aware package coverage at least 85%;
- Bandit and locked runtime dependency audit;
- wheel/sdist isolated smoke tests;
- `v0.14.0-alpha.0` prerelease with verified artifact hashes.

## Documentation and Learning Deliverables

M5b adds:

- an MCP architecture guide with lifecycle, trust boundaries, and sequence diagrams;
- an ADR for stdio-only, host-pinned grants, and stable SDK v1;
- L10 prerequisites and notes comparing MCP to Java RPC/SPI, API gateways, Flink connectors, and
  Spark catalog contracts;
- exercises for schema hashing, process approval, timeout uncertainty, malicious server metadata,
  and Policy non-bypass;
- a resume highlight explaining governed external-tool interoperability, contract pinning, and
  bounded failure handling.

## Non-Claims

- MCP is a protocol, not a sandbox or trust mechanism.
- `stdio` limits who can connect to the child process; it does not limit what that process can do.
- Explicit approval informs the user; it does not make a malicious executable safe.
- Schema hashes detect reviewed-contract drift; they do not prove package provenance or behavior.
- A read-only label is a host assertion about intended behavior, not enforcement inside the server.
- Policy controls whether the client calls a granted Tool. It cannot constrain arbitrary work a
  malicious server performs during startup or inside its process.
- Timeouts stop waiting and trigger cancellation/cleanup; they cannot prove a remote side effect
  did not happen.
- Discarding stderr avoids leakage but makes operational diagnosis less detailed.
- M5b does not provide OS sandboxing, code signing, package installation verification, remote MCP,
  OAuth, resource access, prompt import, model sampling, or dynamic capabilities.

## Source Alignment

The lifecycle follows the official MCP 2025-11-25 specification:

- <https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle>
- <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>

The process-consent and stdio boundaries follow the official security guidance:

- <https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices>

The implementation uses the official Python SDK stable v1 line and its stdio client:

- <https://github.com/modelcontextprotocol/python-sdk/tree/v1.x>
- <https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/client.md>

M5b intentionally implements a strict host subset, not compatibility with every server feature.
