# Mini CodeAgent

[English](README.md) | [简体中文](README.zh-CN.md)

一个从第一性原理出发构建、框架轻量且模型供应商无关的编码 Agent。

> 项目状态：预发布 Alpha。M8 已提供模型供应商无关的 Agent Core、Anthropic/OpenAI-compatible
> 适配器、带 Schema 校验的 Tool Registry、跨平台 Workspace 边界、有界 Read/Search、
> 带冲突检测的 Write/Edit、受策略治理的 argv 命令执行、确定性上下文准入、加固的只读 Git
> 证据、受治理 Pytest 诊断、版本化 SQLite Session/Trace、失败关闭的 Checkpoint/Resume、
> 由宿主控制的有限 Repair Loop、带来源信息的惰性 Skills、由宿主注册的确定性 Tool Hooks、
> 由宿主固定配置的本地 MCP stdio Tools、受限只读分析 Subagent、由宿主管理且需要单独批准
> 才能采用的 Worktree 实现候选，以及调用真实模型的 `run`/`chat` 命令和仅绑定本机回环地址的
> Web 工作台。当前尚未实现 OS 沙箱、Shell 字符串执行、项目提供的可执行 Hook、Repair 自动
> 恢复、remote HTTP/OAuth MCP、自动 commit/merge/push 和真实 Provider CI。

## 环境要求

- Python 3.12 或 3.13
- uv 0.11.25

## 开发与验证

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

如果通过 `pip` 安装了 `uv`，但 Windows `PATH` 中找不到 `uv`，可以把上述命令中的
`uv` 改为 `python -m uv`。

## 配置

配置优先级如下：

```text
默认值 < TOML 文件 < MINI_CODE_AGENT_* 环境变量 < CLI 显式参数
```

默认配置路径由 Platformdirs 按操作系统约定生成。Secret 可以通过环境变量传入，
`doctor` 不会打印 Secret。支持的配置项见 `config.example.toml`。

## 使用硅基流动运行

从示例复制本地配置文件，并只在当前 Shell 中设置 API Key：

```powershell
Copy-Item .\config.example.toml .\config.toml
$env:MINI_CODE_AGENT_OPENAI_API_KEY = "your-siliconflow-api-key"
```

模型名称必须使用你的硅基流动账户中实际可用的完整标识。示例配置使用
`Pro/zai-org/GLM-4.7`，OpenAI-compatible 地址为：

```text
https://api.siliconflow.cn/v1
```

对当前工作区执行一次编码任务：

```powershell
mini-code-agent run "Inspect this project and summarize its architecture." `
  --config .\config.toml `
  --workspace .
```

启动交互式任务循环：

```powershell
mini-code-agent chat --config .\config.toml --workspace .
```

启动本地 Web 工作台：

```powershell
mini-code-agent web --config .\config.toml --workspace .
```

浏览器默认打开 `http://127.0.0.1:8765`。使用 `--no-open` 只启动服务，使用 `--port`
指定其他端口。命令拒绝绑定非回环地址。Workspace 在进程启动时固定，浏览器不能切换。

Web 工作台会展示 Agent 生命周期事件、Tool 活动、Token 使用量、有界动作预览和文件 diff。
写文件或执行命令会暂停，直到用户在检查器中批准或拒绝。API Key 只保存在服务端配置中；
浏览器只能看到“是否已配置”的布尔值和进程随机请求令牌。

进程内最多保留最近 20 次运行，因此刷新浏览器可以恢复当前记录。重启服务会清空这部分
界面历史；恢复出来的旧运行也不会自动进入后续模型上下文。

`chat` 中的每条输入都是针对同一 Workspace 发起的一次独立、有界 Agent Run，并不代表
已经实现持久对话记忆。只读工具自动运行；写文件和本地 argv 命令需要确认。
`run --non-interactive` 遇到写入或命令请求时会拒绝，而不是弹出审批。

这些命令会调用配置的真实模型 API，并消耗 Provider 额度。CI 使用 Mock HTTP Transport，
不需要真实 API Key。

## Provider 适配器

两个适配器实现相同的 `ModelProvider` 协议：

```python
from pydantic import SecretStr

from mini_code_agent.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
)

anthropic = AnthropicProvider(
    api_key=SecretStr("..."),
    model="your-claude-model",
)

compatible = OpenAICompatibleProvider(
    api_key=SecretStr("..."),
    model="your-model",
    base_url="https://your-provider.example/v1",
)
```

应用必须对适配器内部创建的 Client 调用 `await provider.aclose()`。外部注入的
`httpx.AsyncClient` 仍由调用方管理。M1b 测试使用 `httpx.MockTransport`；没有单独执行
带真实凭证的 Smoke Test 时，不声称真实 API 调用成功。

## 只读 Workspace

```python
from pathlib import Path

from mini_code_agent.tools import ReadFileTool, SearchTextTool, ToolRegistry
from mini_code_agent.workspace import WorkspaceBoundary

workspace = WorkspaceBoundary(Path.cwd())
tools = ToolRegistry([
    ReadFileTool(workspace),
    SearchTextTool(workspace),
])
```

模型提供的路径必须是 Workspace 相对的 POSIX 风格路径。边界会拒绝链接、`.git`、
跨平台特殊路径、非普通文件、二进制文件、非 UTF-8 文件和超出资源限制的请求。
这是文件系统策略，不是 OS 沙箱。

## 受治理文件写入

有副作用的 Tool 必须通过 `GovernedToolExecutor` 组合。安全默认值允许读取、写入时询问、
执行和网络动作默认拒绝。修改已有文件必须携带 `read_file` 返回的 SHA-256；创建新文件
使用 create-only 语义。

完整组合方式与并发限制见
[受治理文件写入](docs/architecture/governed-writes.zh-CN.md)。

## 受治理命令执行

`run_command` 只接受显式 argv，永远不使用 `shell=True`。执行默认拒绝，必须命中明确的
Policy Rule。Runner 会校验 cwd、移除任意环境变量、限制时间和输出，并在超时、输出溢出
或取消时清理进程树。

这是本地进程生命周期治理，不是 OS 沙箱。详见
[受治理命令执行](docs/architecture/governed-command-execution.zh-CN.md)。

## Context Budget

每次请求 Provider 前都会估算完整请求。压缩会保留最初目标、最新完成单元以及所有有副作用
或未知 Tool 的交互；ToolCall 与 ToolResult 批次保持原子性。较旧的只读历史可以被省略，
同时记录有界计数和指纹证据。如果必须保留的历史仍无法放入预算，运行会在调用 Provider
之前停止。

默认 UTF-8 Estimator 是确定性、Provider-neutral 的估算器，并不是厂商精确 Tokenizer。
M3a 也不等于持久记忆或崩溃安全的重放保护。详见
[确定性 Context Budget](docs/architecture/context-budget.zh-CN.md)。

## Session 与 Trace

```python
from pathlib import Path

from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.persistence import SqliteSessionTraceStore

with SqliteSessionTraceStore(Path("agent-state.db")) as store:
    session = store.create_session()
    runtime = AgentRuntime(
        provider,
        tools,
        journal=store.journal(session.session_id),
    )
    result = await runtime.run(user_prompt="Inspect the project.")
    verification = store.verify_trace(session.session_id)
```

SQLite schema v3 在事务边界中保存有界的 Trace-envelope-v1 生命周期事件、Checkpoint、
Session/Run 投影和独立的有限 Repair 生命周期 Journal。

Required Journal 会在 Provider I/O 前记录 `ModelStarted`，在 Tool 执行前记录
`ToolStarted`；持久化失败会停止后续工作。Event ID 是幂等键，每个 Session 的 SHA-256
链可以发现不一致的行和投影。

Trace 事件不包含 Prompt、Tool 参数/结果、Patch 或命令输出。Hash Chain 没有签名，
也不防恶意篡改。详见
[Session 与追加式 Trace](docs/architecture/session-trace.zh-CN.md)。

## Checkpoint 与 Resume

M3c 只在 Provider 调用前保存完整强类型状态：第一次调用前，以及完整 ToolResult 批次之后。
SQLite schema v2 将每个快照与 `CheckpointSaved` 原子绑定。Resume 会验证 Trace、Tool
Contract 和有界 Workspace Fingerprint，再显式判断是否可以重放 Provider 或只读操作。
任何尚未进入 Checkpoint 的 write、execute 或 network 动作都会阻止自动 Resume。

Checkpoint Payload 包含完整 Prompt、模型文本、Tool 参数/结果和命令输出，并以有界明文
保存。这是本地崩溃恢复，不是加密、分布式协调或外部执行的 exactly-once。数据库应放在
模型无法控制的 Workspace 外。详见
[Checkpoint 与安全恢复](docs/architecture/checkpoint-resume.zh-CN.md)。

## 只读 Git

`git_status` 将有界 `--porcelain=v2 -z` 输出解析为强类型分支和文件记录。
`git_diff` 返回有界 staged 或 unstaged Patch。Workspace 必须正好是仓库顶层。

Git Client 禁用分页、可选锁、fsmonitor、external diff、textconv 和 submodule 递归。
它不会把模型控制的任意 Git argv 暴露出来，也不提供 add/commit/reset/checkout/push。
Git 证据可能包含源码或 Secret，并且只是某个时间点的观察。详见
[加固的只读 Git 证据](docs/architecture/readonly-git.zh-CN.md)。

## 受治理 Pytest

`run_tests` 执行宿主配置的固定 Pytest Profile。模型只能提供可选的、已存在的 Workspace
相对文件或目录以及原因；不能选择 Python 可执行文件、cwd、Plugin、Timeout、Report Path、
环境变量或任意参数。

固定 argv 使用隔离 Python 启动，禁止写入字节码和 `.pytest_cache`，关闭环境中的 Plugin
自动加载，在 Target 前加入 `--`，并把有界内置 JUnit XML 转换成强类型 Process/Report
状态、计数和诊断。Execute 仍然默认拒绝，必须有显式 Policy Rule 和 Approval。

通过审批的测试仍会以 Agent 进程的 OS 权限运行任意仓库代码。这是受治理执行，不是沙箱。
详见[受治理测试执行](docs/architecture/governed-test-execution.zh-CN.md)。

## 有限 Repair Loop

`RepairRuntime` 在 `AgentRuntime` 之上拥有一个显式反馈控制 Session。它要求干净仓库、
精确的已跟踪可编辑文件集合、明确 Approval、Required Repair Journal，以及一组固定的
宿主 Pytest Target。每次 Agent 尝试都由 `RepairActionGuard` 限制为读取和精确范围写入；
Execute 与 Network 在普通 Policy 和 Approval 之前就会被拒绝。

宿主在接受 Patch 前验证 Git Status/Diff 和 Workspace Identity，随后重新运行相同测试；
只有完整通过报告才能成功，否则按强类型安全或预算原因停止。Attempt、Elapsed Time、
Patch Size、Prompt Size 和重复失败 Fingerprint 都有独立上限。失败改动留在工作区供检查；
Runtime 不会 stage、commit、reset、clean，也不会自动恢复中断的 Repair。

详见[有限 Repair Loop](docs/architecture/bounded-repair-loop.zh-CN.md)。

## 受治理 Skills 与 Hooks

Skill 仅从宿主配置目录中发现直接子级 `SKILL.md`。严格 UTF-8、受限 YAML、Pydantic Metadata、
带来源 ID、普通文件检查、文件身份和 SHA-256 共同保护发现与加载 Contract。
`list_skills` 只暴露 Metadata；`load_skill` 要求之前观察到的 Fingerprint，并返回明确标记为
不可信的 Markdown。Skill 内容不能注册可执行能力，也不能绕过 Tool Policy。

Tool Hook 是受信任宿主代码直接注册的强类型 Async Handler。Pre-Hook 可以继续或阻止，
但继续后仍需经过普通 Policy 和 Approval。Post-Hook 观察真实结果；超时、异常或非法返回
不能替换结果。当前不支持仓库 Command/HTTP/Prompt Hook 或动态 Python Import。进程内 Hook
拥有 Agent 进程权限，并未被沙箱隔离。

详见[受治理 Skills 与 Tool Hooks](docs/architecture/governed-extensions.zh-CN.md)。

## 受治理 MCP stdio

本地 MCP Tool 使用官方稳定 Python SDK v1 和直接 stdio。受信任宿主 Profile 固定绝对
Executable/argv/cwd、Server Identity、精确 Tool Grant、由宿主定义的 Description/
Side Effect/Risk、Canonical Input/Output Schema Hash 和硬生命周期/内容上限。

启动进程需要单独 Connection Approval。验证后的 Local Alias 仍通过普通 Tool Registry、
Hooks、Policy 和可选 Tool Approval，并标记为 `TrustSource.EXTENSION`。Server Instructions、
Descriptions、Annotations、Icons 和 `_meta` 都不是权限来源，也不会复制进模型可见定义。

结果只接受有界文本或 Object 形状的 Structured JSON。调用串行且不重试；有副作用 Tool
超时时会报告完成状态不确定。stdio 和用户审批都不是 OS 沙箱。当前不支持 Remote HTTP/OAuth、
Resources、Prompts、Roots、Sampling、Elicitation、Tasks、动态 Tool List 和包安装。

详见[受治理 MCP stdio](docs/architecture/governed-mcp.zh-CN.md)。

## 受治理分析 Subagent

M6a 为每个不可变宿主 Profile 暴露一个受治理 Parent Tool。模型可以提交一到四个唯一且
有界的任务；宿主固定 Child System Prompt、精确只读 Tool 名称、Agent Limits、Concurrency、
Deadline 和 Result Budget。

每个 Child 在 Provider 请求前必须拥有独立 Provider/Executor、精确 `READ_ONLY` Definition、
`governance_enforced is True`，并把每个 Child Tool 标记为 `TrustSource.SUBAGENT`。
Child 只获得一个新的任务消息，不继承 Parent 或 Sibling Transcript，也没有 Delegation Tool。

所有 Child 归属一个 `asyncio.TaskGroup`。Semaphore 限制并发；单 Child 和批次 Timeout
拥有强类型结果；输入顺序保持不变；外部取消会先取消并 Join 所有 Child，再继续向上传播。
Parent 只收到有界、不可信 Summary 和 ToolResult Metadata/SHA-256 证据，不收到原始 Child
Transcript 或 Tool 内容。Event 不记录任务、Prompt、Summary、参数、结果、仓库内容或异常文本。

进程内 Context 隔离不是 OS 沙箱。M6a 不能写文件、运行命令、调用 Network Tool、发起嵌套
审批、持久化 Child Trace、创建 Worktree 或合并改动。

详见[受治理分析 Subagent](docs/architecture/governed-subagents.zh-CN.md)。

## 受治理 Worktree 候选

M6b 增加一个单独受治理的实现 Tool。Parent 模型只能提供 `task` 和 `reason`；宿主固定干净
仓库、外部 State Root、Git Executable、允许路径前缀、实现 Profile、可选固定测试和资源限制。

宿主创建 Locked Detached `--no-checkout` Worktree，并从原始 Git Object Bytes 物化精确
Index。新的非交互 Child 只能使用宿主批准的 Read/Search/Write/Edit 和可选固定 `run_tests`，
且全部标记为 `TrustSource.SUBAGENT`。它不能使用 Git、任意命令、MCP/Network、递归 Delegation
或 Parent Approval。

Child 停止后，宿主将完整目录树与不可变 Base Manifest 和 Mutation Ledger 对账。Ready
Candidate 以 Canonical Manifest 和 Content-addressed Blob 形式保存在仓库外，临时 Worktree
随后被验证并删除。Child 完成永远不会直接修改 Parent Checkout。

`adopt_subagent_candidate` 是单独的高风险 WRITE Tool 和 Approval。它要求原始干净 `HEAD`，
并在第一次替换前重新验证所有路径和 Hash；仅应用验证通过的新增或修改，结果保持 unstaged、
uncommitted。冲突时零写入；部分失败必须被证明已经回滚，否则标记为 uncertain。
`discard_subagent_candidate` 只接受验证过的 Ready Candidate。

Worktree 路径分离和带回滚意识的 Adoption 不是 OS 沙箱或崩溃原子事务。M6b 不删除或重命名
文件，不执行任意命令，不 commit/merge/push，也不声称 Token 或延迟得到提升。

详见[受治理 Worktree 候选](docs/architecture/governed-worktree-candidates.zh-CN.md)。

## 文档导航

### 学习与面试

- [学习知识地图](docs/learning/knowledge-map.md)
- [学习进度与实现证据](docs/learning/progress.md)
- [M7 CLI 与 Provider 学习笔记](docs/learning/m7-cli-provider-runtime.md)
- [M8 Web 工作台学习笔记](docs/learning/m8-web-console.md)
- [完整简历项目包](docs/resume/project-profile.md)
- [M7 CLI 简历与面试说明](docs/resume/m7-cli-project-profile.md)
- [M8 Web 工作台简历与面试说明](docs/resume/m8-web-console-profile.md)

### 核心架构

- [Agent Core](docs/architecture/agent-core.zh-CN.md)
- [Provider 适配器](docs/architecture/provider-adapters.zh-CN.md)
- [只读 Workspace Tool](docs/architecture/readonly-tools.zh-CN.md)
- [受治理文件写入](docs/architecture/governed-writes.zh-CN.md)
- [受治理命令执行](docs/architecture/governed-command-execution.zh-CN.md)
- [Context Budget](docs/architecture/context-budget.zh-CN.md)
- [Session 与 Trace](docs/architecture/session-trace.zh-CN.md)
- [Checkpoint 与 Resume](docs/architecture/checkpoint-resume.zh-CN.md)
- [只读 Git](docs/architecture/readonly-git.zh-CN.md)
- [受治理 Pytest](docs/architecture/governed-test-execution.zh-CN.md)
- [有限 Repair Loop](docs/architecture/bounded-repair-loop.zh-CN.md)
- [Skills 与 Hooks](docs/architecture/governed-extensions.zh-CN.md)
- [MCP stdio](docs/architecture/governed-mcp.zh-CN.md)
- [分析 Subagent](docs/architecture/governed-subagents.zh-CN.md)
- [Worktree 候选](docs/architecture/governed-worktree-candidates.zh-CN.md)
- [威胁模型](docs/architecture/threat-model.zh-CN.md)

### 设计与决策

- [产品设计](docs/superpowers/specs/2026-06-29-mini-code-agent-design.md)
- ADR 位于 `docs/adr/`
- 实施计划与历史规格位于 `docs/superpowers/`

## License

Apache-2.0
