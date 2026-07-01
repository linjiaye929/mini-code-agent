# Mini CodeAgent 简历项目包

> 项目状态：M0 工程基础、M1 Agent Core、M1b 双 Provider、M2a 只读 Workspace/Tool
> Registry、M2b 受治理文件写入、M2c argv 命令执行与 M3a 确定性 Context Budget
> 、M3b 版本化 Session/追加式 Trace、M3c Checkpoint/Resume、M4a hardened 只读 Git
> 、M4b 受治理 Pytest 诊断、M4c 宿主控制的有限 Repair 及 M5a 惰性 Skills/Tool Hooks
> 已发布；M5b host-pinned local stdio MCP 已完成本地实现与真实 SDK 集成。Shell 字符串、
> 项目可执行 Hook、OS 沙箱、remote HTTP/OAuth MCP、自动 Repair Resume、Subagent/
> Worktree 和真实凭证联调尚未实现。`v0.13.0-alpha.0` GitHub prerelease 已发布；
> M5b 当前 Python 3.13 本地为 960 passed、10 个 Windows symlink 条件跳过、90.82%
> 分支覆盖率，Ruff/Pyright/Bandit/locked pip-audit 已通过；`v0.14.0-alpha.0` 的
> Python 3.12、远程 CI、artifact smoke、tag 与 Release 证据发布后再回填。
>
> 本文中的功能、性能和指标是目标或验收方案。只有得到代码、测试、CI、Benchmark 或 Release 证据后，才能改写为已完成成果。

## 1. 30 秒项目介绍

正在从零设计并实现一个面向真实软件工程任务的企业级 Python Mini CodeAgent。项目采用 Framework-light Agent Harness，通过统一 Provider 协议接入 Anthropic Messages 与 OpenAI-compatible Chat Completions，以跨平台 CLI 作为主要交互入口。当前已完成带硬限制和取消传播的 Agent Core、同步/流式模型适配、安全 HTTP 边界，以及由 JSON Schema Tool Registry、跨平台 WorkspaceBoundary、Read/Search、allow/ask/deny Policy、哈希防冲突 Write/Edit、受治理 argv Command、hardened Git status/diff 和固定 Profile Pytest 诊断组成的代码理解、修改与验证链路；每次模型调用前通过确定性 Context Budget 限制请求，并以 SQLite schema v3、required EventJournal、事务式追加 Trace、稳定 Checkpoint 和 fail-closed Resume 管理长任务状态。M4c 在 Agent 外增加宿主控制的有限 Repair 状态机，以 clean/tracked exact scope、pre-policy ActionGuard、Git 前后证据、固定 Pytest、失败指纹和多维预算完成“基线失败 -> 一次修改 -> 宿主验证 -> 有限重试/停止”闭环。

M5a 进一步加入 source-qualified Skill Catalog：严格解析 `SKILL.md`，只暴露 metadata，
按 SHA/文件身份惰性重验并返回标记为不可信的 Markdown；同时以 typed async Hook runner
把宿主注册的 veto/observer 接入 Tool 治理链，保证 continue 不能绕过 Policy、post 失败
不能改写结果。M5b 再接入官方稳定 MCP Python SDK 的 local stdio Tools：启动前审批绝对
executable/argv/cwd，钉住 server identity、完整 Tool 集合和 input/output schema hash，
用 owner-worker 管理跨 Task 进程生命周期，并把 MCP alias 作为 extension 继续送入同一
Policy/approval/result-boundary。下一阶段将实现受限 Subagent 与 Git Worktree。

## 2. 项目定位

- 项目类型：开源 AI Agent 基础设施 / Developer Tooling。
- 目标用户：希望理解、定制或嵌入 CodeAgent 的开发者与团队。
- 核心原则：Framework-light、Provider-neutral、CLI-first、安全默认、可恢复、可观测、可测试。
- 首发平台：Windows、Linux。
- `1.0.0` 边界：单 Agent 核心闭环、本地 Workspace、终端交互、人工权限确认、Skills、Hooks、MCP、受限 Subagent 与 Git Worktree。
- 后续扩展：TUI、复杂多 Agent 团队协调和远程执行。

## 3. 技术栈

最终技术栈以 `pyproject.toml`、ADR 和发布版本为准。

M0 至 M5b 已实际使用 Python 3.12/3.13、`asyncio`、`Protocol`、`dataclasses`、uv、
Hatchling、Pydantic v2、pydantic-settings、Platformdirs、HTTPX、httpx-sse、Typer、Rich、
JSON Schema Draft 2020-12、stdlib `sqlite3`、SQLite WAL/事务/索引、canonical JSON、SHA-256、
Git porcelain v2、Pytest/JUnit XML、defusedxml、PyYAML、官方 MCP Python SDK v1、
JSON-RPC/stdio、pytest-asyncio、Coverage、Ruff 与 Pyright；其余技术随对应里程碑落地。

| 分类 | 技术 |
|---|---|
| 语言与运行时 | Python 3.12/3.13、`asyncio`、`dataclasses`、`enum` |
| 类型系统 | `typing.Protocol`、Generics、TypedDict、严格 Pyright |
| 模型接入 | Anthropic Messages、OpenAI-compatible Chat Completions、HTTPX、httpx-sse |
| 数据模型 | Pydantic v2、JSON Schema Draft 2020-12 |
| CLI | Typer、Rich |
| 工具系统 | 强类型 Tool Registry、统一 Tool Result/Error |
| 文件能力 | `pathlib`、`stat/fstat`、SHA-256、`difflib`、原子 Write、唯一匹配 Edit |
| 命令能力 | `asyncio.subprocess`、argv、process group、`taskkill`、超时/取消/输出限制 |
| 状态持久化 | stdlib `sqlite3`、schema v3 顺序事务迁移、WAL、foreign key、Session/Run/Repair projection、stable Checkpoint、busy timeout |
| 可观测性 | 类型化 started/completed 事件、required EventJournal、追加式 Trace、usage、SHA-256 链 |
| Git 证据 | Git CLI、porcelain-v2 NUL parser、status/diff、hardened config |
| 测试诊断与修复 | 固定 Pytest Profile、JUnit XML、双状态分类、有限 Repair 状态机、失败指纹、多维预算 |
| 扩展治理 | restricted PyYAML、source-qualified Skill Catalog、SHA/文件身份重验、typed async Tool Hooks、monotonic authorization |
| MCP 互操作 | 官方 `mcp` SDK v1、JSON-RPC、local stdio、host-pinned grants、canonical schema SHA-256、owner-worker lifecycle |
| 测试与质量 | Pytest、pytest-asyncio、Coverage、Ruff、Pyright |
| 构建与发布 | `uv`、`pyproject.toml`、GitHub Actions、SemVer、GitHub Release |
| 文档与治理 | Markdown、ADR、威胁模型、贡献指南、Changelog |

## 4. 核心亮点

1. Framework-light 可解释 Agent Loop。
2. Provider-neutral 模型适配层。
3. 强类型 Tool Registry 与协议校验。
4. 安全 Workspace 与路径边界。
5. allow/ask/deny 权限决策系统。
6. 跨平台文件编辑与受治理 argv 命令执行。
7. 确定性 Context Budget 与副作用历史固定。
8. 版本化 Session/Run 与事务式追加 Trace。
9. Checkpoint 与 Resume。
10. Hardened Git 证据与受治理 Pytest 结构化诊断。
11. 宿主控制、精确作用域和可审计停止的有限 Repair Loop。
12. 企业级质量门禁与发布工程。
13. 惰性不可信 Skills 与单调授权 Tool Hooks。
14. Host-pinned、双审批、受治理的 MCP stdio Tools。
15. 面向 Subagent 与 Worktree 的扩展架构。

## 5. 亮点拆解

| 亮点 | 为什么需要 | 技术实现 | 实现功能 | 解决的问题 | 指标或证据 |
|---|---|---|---|---|---|
| 可解释 Agent Loop | 重型框架容易隐藏状态流转、错误传播和模型调用成本 | 显式状态机、`asyncio`、批次预校验、类型化事件、最大轮次/ToolCall/超时限制 | 编排模型请求、原生 ToolCall、结果回传、取消和确定性停止 | 避免无限循环、部分副作用、隐式控制流和框架锁定 | M1 27 项 Runtime 单测与 1 项完整 ToolCall 集成测试通过 |
| Provider-neutral Adapter | 单一供应商会带来协议、能力和成本锁定 | `Protocol`、递归不可变 Message/ToolCall、Anthropic/OpenAI 防腐层、capability、usage 与错误归一化 | 同一 Agent Runtime 无分支切换两种 wire protocol，并完成 ToolCall 往返 | 隔离 content block、role、tool arguments 和 finish reason 差异，避免厂商类型侵入 Core | 124 项 Provider/HTTP/跨适配器测试通过；两种 mock wire 闭环通过 |
| 流式 ToolCall 状态机 | SSE 中参数是不完整 JSON，且并行调用会交错，直接解析或执行会产生错误调用 | 异步生成器、Anthropic block lifecycle、OpenAI per-index state、稀疏元数据缓存、终止后 JSON 校验 | 实时输出 text/tool delta，并在完整 lifecycle 后生成唯一 `ResponseCompleted` | 解决分片归属、ID/name 丢失、参数截断、终止事件缺失和错误完成问题 | 覆盖交错双工具、非法 JSON、索引缺口、元数据变化、缺失终止与流内错误 |
| 安全 Provider HTTP 边界 | 模型响应和错误体不可信，可能泄密或造成内存/连接失控 | HTTPX async context、SSE parser、超时、16 MiB 硬上限、URL/header 校验、client ownership、静态公开错误 | 对同步/流响应统一限制、清理、分类和脱敏 | 防止原始 body/key/exception 泄漏、无界响应、endpoint 替换和 client 泄漏 | HTTP 状态、超限、非 JSON、错误 Content-Type、网络失败和密钥泄漏负向测试 |
| 强类型 Tool Registry | 模型参数和动态工具容易产生类型漂移、异常泄漏与错误关联 | Draft 2020-12 Schema、单次 definition snapshot、`Protocol`、统一 Result/Error、结果上限 | 构造时验证 Schema，执行前校验参数，按名称分发并验证返回 ID/类型/大小 | 阻断非法参数进入执行器，防止动态定义漂移、异常泄漏和无界结果 | M2a 99 项专项测试覆盖 Registry/Workspace/Read/Search/Agent 集成；1 项 symlink 权限跳过 |
| 安全 Workspace | Agent 文件权限过大可能读取仓库外文件或平台特殊路径 | 词法白名单、`resolve(strict=True)`、`relative_to`、symlink/junction 与 `stat/fstat`、文件/遍历预算 | 将模型路径解析、文件类型、大小、编码和目录遍历统一限制在根目录 | 防止 `../`、绝对/盘符/UNC、ADS/设备名、`.git`、链接逃逸和资源耗尽 | M2a 99 项专项测试通过；Linux symlink CI 待验证 |
| 受限 Read/Search | 直接把仓库全文送入模型成本高，模型正则和大文件会造成不可控计算 | 保留换行的行窗口读取、literal search、确定性排序、Unicode casefold 位置映射、结果/preview 上限 | 按相对路径读取代码并返回 path/line/column/preview | 降低无效上下文，避免 ReDoS、错误 Unicode 列号和超大工具输出 | Read/Search 单测与“Read → Search → Final”三轮 Agent 集成通过 |
| 权限决策系统 | 模型请求动作不等于系统应执行，写入、命令和网络风险也不同 | 不可变首匹配 Policy Rule、风险/资源/会话/信任源匹配、`GovernedToolExecutor`、交互审批、非交互 fail-closed | 在执行前依次完成 Schema、预览、allow/ask/deny 和审批；Agent Runtime 拒绝未治理副作用工具 | 防止 Prompt 自授权、未审批落盘和错误配置的非交互自动批准 | 26 项执行器/Registry 测试；拒绝、显式批准、非交互零写入均有回归测试 |
| 防冲突 Write/Edit | Agent 基于旧上下文写文件会覆盖用户或其他进程的新修改 | `read_file` 原始字节 SHA-256、乐观并发前置条件、create-only、唯一 literal match、审批后重复校验 | 创建新文件、哈希匹配替换、精确单点编辑并返回前后哈希和 diff | 将静默覆盖转化为可重试 `conflict`，拒绝零匹配、多匹配和 no-op 编辑 | 32 项相关单测与 3 项真实 Agent 治理写入集成测试通过 |
| 原子文件发布 | 写盘中断可能留下半个源文件，审批界面也不能展示无界内容 | 同目录 `NamedTemporaryFile`、flush/`fsync`、权限位保留、`os.link`/`os.replace`、失败清理、32 KiB diff 上限 | 成功时一次发布完整内容，失败时保留原文件并清理临时文件 | 避免部分写入和临时文件泄漏，控制审批与 ToolResult 体积 | 故障注入、陈旧哈希、大小/NUL/编码、diff 截断和原文件不变测试 |
| 受治理 argv 命令执行 | 测试/构建需要启动进程，但 shell 字符串带来插值注入、平台转义和失控子进程风险 | `create_subprocess_exec`、critical preview、execute 默认 deny、`executable_glob`、Workspace cwd、最小环境、POSIX process group、Windows `taskkill /T /F` | 经显式规则和交互审批运行 argv 命令，返回 exit/stdout/stderr/timeout/overflow，并在取消前清理进程树 | 去除 `shell=True` 解析面，避免 API Key 环境继承、无界输出、超时后父子进程残留和 pipe 死锁 | 29 项 Command 单测、7 项 Tool 单测、4 项 Agent 治理集成测试；父子心跳、异常读取和非交互零执行均覆盖 |
| 确定性 Context Budget | 长任务会超过上下文窗口；直接截断可能拆开调用/结果，或抹掉已完成写操作并诱发重复副作用 | `TokenEstimator` Protocol、canonical JSON UTF-8 估算、Pydantic 预算、原子 ToolCall/ToolResult、只读最近后缀、副作用/未知工具固定、SHA-256 标记、类型化事件 | 每次 Provider I/O 前构造有界 `ContextWindow`；完整 transcript 留在 Runtime，Provider 只看选中历史；固定内容无法容纳时 fail closed | 避免半个工具交换、无界请求、原始省略内容泄漏和因遗忘副作用导致的重复动作；不依赖模型生成摘要 | 18 项 Context 单测、77 项 Context/Runtime/集成测试通过；覆盖 609/610/611 边界、副作用与未知工具固定、零 Provider I/O 失败路径 |
| Checkpoint/Resume | 进程退出不应丢失完整上下文，但从旧 prompt 重跑可能重复真实写入 | SQLite schema v2 事务迁移、稳定 typed transcript、canonical JSON SHA-256、Tool/Workspace fingerprint、增量 Trace 风险扫描、显式 replay policy、原子 claim、单事务 Trace 快照 | 初始及完整 ToolResult 后保存；重开后验证兼容性，将旧 Run 标记 `INTERRUPTED`、新建 Run 并从下一逻辑 turn 继续；并发 writer 使旧 plan stale | 恢复可重放的 Provider/只读中断；阻断未纳入快照的 write/execute/network；防止伪造 plan、并发双 claim、混合快照误报和 stale TOCTOU | 15 项 Resume 分析/claim 单测与 2 项进程边界集成测试；并发 claim 200 轮均一个 winner/一个 stale loser；真实治理写入后 Resume 阻断且文件/审批各一次 |
| 版本化 Session 与追加式 Trace | 进程退出后纯内存事件不可查询；若 Trace 与状态分开写会出现索引/正文不一致，静默持久化失败还会继续产生副作用 | SQLite schema v1、WAL、`BEGIN IMMEDIATE`、foreign key、Session/Run materialized projection、UUID event ID、canonical JSON、SHA-256 前驱链、required `EventJournal`、bounded busy timeout | 单事务追加 typed lifecycle event 并更新 Run/Session；Provider/Tool 前记录 Started，完成后记录 Completed；支持重开查询、分页读取和全链验证 | 消除 Trace/投影跨文件提交缝隙，将持久化故障转化为 `PERSISTENCE_ERROR`，用 started-only 状态标记不确定副作用，阻止后续工具继续执行 | 42 项 persistence 单测与 3 项真实集成测试；覆盖幂等冲突、4 类篡改、锁超时、终态回滚、Secret 扫描及第二个治理写入零落盘 |
| 只读 Git 证据 | Agent 修改前后需要可靠识别用户已有变更，但普通 Git 配置可在 status/diff 中执行 fsmonitor、external diff 或 textconv | argv-only Git CLI、`--porcelain=v2 -z` 解析、exact top-level、`--no-optional-locks`、禁用 fsmonitor/ext-diff/textconv/submodule、联合输出/时间/条目/patch 上限、canonical SHA-256 | `git_status` 返回 typed branch/XY/rename/conflict/untracked；`git_diff` 返回 staged/unstaged patch | 避免 locale 文本误解析、父仓库越界、配置驱动代码执行、可选 index 写入和误将截断 patch 当完整证据 | 27 项 Git 单测、4 项 Tool 单测、1 项真实 Agent 集成；恶意扩展零执行，status/diff 前后 index 字节与纳秒 mtime 不变 |
| 受治理 Pytest 诊断 | 文件写完不等于任务完成，但直接开放测试命令会引入任意 argv、插件和项目代码执行风险 | fixed `PytestProfile`、`python -I -B`、禁用 ambient plugin/cache、Workspace target、execute 默认 deny/独立审批、进程预算、`defusedxml` bounded JUnit parser、process/report 双状态 | 模型只选测试文件/目录；批准后运行真实 Pytest，返回 exit 分类、计数和有界 failure/error diagnostics，并在所有路径清理报告 | 将脆弱终端文本解析改为机器协议；区分测试失败、runner 失败、无测试和报告损坏；阻止模型控制解释器/参数/插件 | 95 项新增测试使全套达到 678 passed；真实 deny/approve/reject/non-interactive/Agent+Trace 集成通过；Python 3.12/3.13 各通过，90.25% 分支覆盖率，四组 artifact smoke |
| 宿主控制的有限 Repair Loop | 诊断可用后，若让模型自行决定改什么、何时测试和是否重试，会形成无界反馈、覆盖用户改动或用文字冒充成功 | 独立 `RepairRuntime`、clean repository、literal exact tracked scope、pre-policy `RepairActionGuard`、固定 Pytest、Git/Workspace 前后证据、canonical failure fingerprint、attempt/time/patch/prompt/repeated-failure 预算、SQLite schema v3 Repair hash chain | 先跑 baseline；每轮只允许一次 Agent read/edit；宿主验证 patch 和测试无副作用后重测，只在完整 passing evidence 下成功，否则以 typed reason 停止 | 阻止 scope 外写入、execute/network、自声明成功、staged/untracked/ignored/submodule/branch 漂移、重复失败和测试残留修改；中断会话不自动重放 | 真实集成覆盖一次缺陷修复、越权写入在审批/落盘前拒绝、dirty repo 在 Provider/Pytest 前拒绝、测试修改仓库即使通过也停止；Python 3.12/3.13 本地各 798 passed、6 个 Windows symlink 条件跳过，3.13 分支覆盖率 90.88%；未虚构 benchmark 提升率 |
| 惰性 Skills 与单调授权 Hooks | 仓库扩展既会占用上下文，也可能通过静默覆盖、动态导入或生命周期回调绕过权限 | restricted PyYAML/Pydantic、direct-child regular-file/reparse 检查、source-qualified ID、SHA-256 + file identity TOCTOU 重验、只读 list/load Tool、async Protocol Hook、稳定优先级、timeout、bounded audit | 模型先发现 metadata，再按 fingerprint 加载 labelled untrusted Markdown；宿主 pre-Hook 可 veto，post-Hook 可观察 | 阻止 Skill 注册执行能力、跨来源 shadow、内容漂移、无界扫描和 Hook 提权；pre 失败在副作用前关闭，post 失败不伪造执行事实 | 真实 Agent 证明恶意 Skill 不能绕过 deny、pre 阻断零落盘、post 失败后结果与后续 observer 保留；Python 3.12/3.13 各 867 passed、90.86% 分支覆盖率；PR/main 五 job CI、v0.13 prerelease 与远端制品摘要验证通过 |
| 受治理 MCP stdio | 直接信任 `tools/list` 会让 server/package 替换新增权限；local server 在 Tool Policy 前已能执行代码，连接审批也不能代表每次调用获批 | 官方 SDK `mcp>=1.28.1,<2`、absolute executable + argv-only、SecretStr environment、独立 connection approver、protocol/server identity、exact grant set、canonical input/output schema hash、owner-worker、per-tool `TrustSource.EXTENSION`、bounded result validator | 宿主审核一个固定 local stdio server，验证后只发布 local aliases；每次调用继续经过 Schema/Preview/Hook/Policy/Tool approval，返回 text/structured JSON | 阻止 PATH/shell 注入、未授权 Tool、schema drift、server metadata 提权、跨 Task AnyIO context 泄漏、无界/多媒体结果和 approval 混淆；超时保留副作用不确定性 | 真实官方 SDK 进程覆盖 handshake/call/shutdown、deny/ask 零远端调用、extra Tool/schema drift 零 admission、cross-task close；M5b 本地全量 960 passed/10 skips、90.82% branch coverage，Ruff/Pyright/Bandit/pip-audit 通过；v0.14 远端证据待发布 |
| 质量门禁 | 企业级项目需要稳定接口和回归保护 | Ruff、严格 Pyright、Pytest、85% 核心覆盖率门槛、哈希构建约束、CI、SemVer | 自动执行 lint、类型检查、测试、构建和安装验证 | 防止低质量变更进入发布版本 | v0.12：Python 3.12/3.13 本地各 798 通过、6 项 symlink 条件跳过，90.88% 分支覆盖率，Bandit/pip-audit 与四组 artifact smoke 通过；PR/main CI 的 Ubuntu/Windows × 3.12/3.13 与 quality 全成功，prerelease 及两个校验摘要一致的制品已发布 |
| 可扩展 Harness | Skills、Hooks、MCP、Subagent 会增加控制流复杂度 | 稳定 Protocol、EventBus、能力声明、依赖倒置、per-tool provenance | 在不侵入 Agent Core 的前提下加入 Skills、Hooks 与 MCP | 避免扩展绕过权限、Trace 和 Session | Skills/Hooks/MCP 均复用 Tool Registry 与 Policy；Subagent/Worktree 待实现 |

## 6. 指标回填规则

以下数据在实现和测试完成前必须保持“待实测”：

| 指标 | 测量方式 | 简历回填格式 |
|---|---|---|
| 核心覆盖率 | `pytest --cov`，明确统计包与排除项 | 核心模块覆盖率达到 `__%` |
| 跨平台兼容性 | GitHub Actions OS/Python 矩阵 | 在 `__` 个环境组合中持续通过 |
| Provider 兼容性 | 对每个 Adapter 运行相同合约测试 | 通过 `__` 个 Provider 的 `__` 项合约测试 |
| 路径安全性 | 路径穿越、绝对路径、symlink、大小写测试 | 通过 `__` 项 Workspace 逃逸测试 |
| Resume 可靠性 | 在模型、工具、写盘阶段注入中断 | `__` 个故障场景恢复成功率 `__%` |
| 修复闭环效果 | 固定缺陷集，对比首次和修复后测试 | 基准通过率由 `__%` 提升至 `__%` |
| Context 效果 | 固定长会话比较 token 与事实保留 | 平均减少 `__%` 上下文 token |
| CI 效率 | 多次 CI 运行的中位数 | CI 中位耗时 `__` 分钟 |
| 发布质量 | build、install、smoke、Release | 完成 `v__` 及跨平台安装验证 |

目标值、计划值和一次性本地结果都不能冒充稳定实测结果。

## 7. 面试展开话术

### 7.1 为什么做这个项目

“我希望理解 CodeAgent 从模型调用到安全工具执行的完整链路，因此没有直接套用重型 Agent Framework，而是实现一个显式、强类型、可观测的 Agent Harness。重点不是聊天界面，而是真实工程环境中的控制流、安全、恢复和验证。”

### 7.2 架构主线

“Provider Adapter 归一化模型差异，Agent Loop 只处理状态迁移，Tool Registry 负责 Schema 和调用边界，Workspace 与 Policy Engine 负责安全，Session Store 和 Trace Recorder 负责恢复与审计。各层通过类型化协议通信，避免具体 Provider、CLI 或工具侵入核心循环。”

### 7.3 为什么强调强类型

“LLM 输出天然不稳定，所以模型边界需要运行时 Schema 校验，核心代码需要静态类型约束。这样可以明确区分参数错误、权限拒绝、工具失败和系统异常，而不是统一变成一段不可处理的文本。”

### 7.4 最有挑战的安全问题

“最大风险不是回答错误，而是错误动作产生真实副作用。所有路径先规范化并验证根目录边界，所有工具调用再经过 allow/ask/deny 策略。Shell 还有超时、取消、输出上限和审批。安全结论由负向测试证明，不只写在文档中。”

### 7.5 Context 压缩策略

“M3a 把上下文管理实现为每次 Provider I/O 前的确定性准入控制，而不是让模型生成摘要。
System Prompt、工具定义和完整消息先统一估算；ToolCall 与 ToolResult 作为原子单元，始终
保留用户目标、最新单元以及写入/执行/网络和未知工具历史，只淘汰较旧的纯只读单元。
如果固定历史仍无法容纳就失败关闭。省略事件记录计数和 transcript 指纹，但不声称能恢复
被省略事实，也不把启发式 UTF-8 估算写成精确 token 或节省率。”

### 7.6 Checkpoint 与 Resume

“M3b 先把 Session、Run 和 Trace 的事实边界做牢，M3c 再只在合法 Provider 输入边界保存完整 typed transcript。Resume 会验证 hash chain、Tool contract 和 Workspace fingerprint，并扫描 Checkpoint 之后的全部事件；任何 write/execute/network 都阻断自动恢复。claim 还会重新分析调用方 plan，并在一个事务里中断旧 Run、启动新 Run、消费快照。Provider/只读重试是显式 at-least-once，不冒充外部 exactly-once。”

### 7.7 Git/test/repair 闭环

“M4a 先把 Git 做成只读证据协议，而不是开放任意 Git 命令：status 使用 porcelain-v2 NUL parser，diff 禁用 external diff/textconv，二者关闭 optional locks、fsmonitor 和 submodule，并要求 Workspace 等于仓库 top-level。M4b 再把测试做成宿主固定 Profile：模型只能选择 Workspace 内 target，execute 默认 deny 并独立审批；Pytest exit 与不可信 JUnit report 分开分类。M4c 用独立 RepairRuntime 形成宿主控制闭环：必须从 clean repository 和 exact tracked scope 开始，ActionGuard 在 Policy 前限制精确写入，Agent 每轮只尝试一次，宿主核对 Git/Workspace 证据并重跑同一测试；只有完整 passing report 成功，重复失败或任一预算/安全条件触发 typed stop。它不自动 reset、commit 或 Resume，也不把审批表述成 OS sandbox。”

### 7.8 Framework-light 的取舍

“Framework-light 不是拒绝依赖。我会使用 Pydantic、HTTP Client、Typer、SQLite 等成熟基础设施，但核心状态机、工具协议、权限模型和 Session 格式由项目控制，兼顾透明度与开发效率。”

### 7.9 Skills 与 Hooks 的安全边界

“我没有把 Skill 当成可动态 import 的插件。项目只扫描宿主指定 root 的一层
`SKILL.md`，用 restricted YAML、Pydantic、source-qualified ID、regular-file 检查和
SHA/file identity 重验形成惰性数据协议；正文明确标成 untrusted，仍受 Tool Policy。
Hook 首版也只接受宿主直接注册的 typed async handler。pre-Hook 只能 veto，continue 仍
经过 Policy/approval；post-Hook 失败不能覆盖已产生的 ToolResult。项目 command Hook 和
durable Hook audit 要等独立进程治理及 run/turn context 合同完成，不能把 in-process
callback 描述成 sandbox。”

### 7.10 MCP 为什么不是“连上 Server 就结束”

“local MCP server 在初始化时已经是一个拥有当前用户权限的进程，所以第一层要审批绝对
executable、argv、cwd 和环境变量名称；连接后也不能把 `tools/list` 当成授权。我用 host
grant 固定 server identity、完整 Tool 名称集合、local alias、side effect、risk 和
input/output schema hash，任何 extra/missing/schema drift 都零 admission。验证后的 alias
仍进入原有 Hook/Policy/Tool approval，并标记为 `TrustSource.EXTENSION`。SDK 的 AnyIO
context 由 owner worker 进入和退出，解决跨 Task close；result 只接受有界 text/structured
JSON。stdio 和审批都不是 sandbox，timeout 也只能报告副作用完成状态未知。”

### 7.11 企业级体现在哪里

“企业级不是功能数量，而是边界清晰、失败可诊断、状态可恢复、安全策略可测试、发布可重复。项目设置严格类型、测试覆盖率门槛、跨平台 CI、安全模型、SemVer 和发布 smoke test。”

### 7.12 如何避免过度设计

“首版先完成单 Agent 的最小完整闭环。Skills、Hooks 和 MCP 已沿已有 Tool、Event、
Policy、Session 协议接入；Subagent 和 Worktree 也必须复用这些边界，不能绕过权限与
Trace。remote MCP/OAuth 等独立威胁面不与 local stdio 混做。”

## 8. 简历成果模板

### 8.1 尚未发布时

- 实现 Provider-neutral Agent Core，以显式状态机驱动“模型响应 -> ToolCall -> ToolResult -> 最终响应”，通过 27 项 Runtime 单测和 1 项确定性集成测试。
- 通过最大轮次、ToolCall 总量、Provider/Tool 超时、重复调用 ID 与取消传播控制无限循环和失控副作用。
- 实现 Anthropic Messages 与 OpenAI-compatible Chat Completions Adapter，在不修改 Agent
  Runtime 的前提下完成两种 wire protocol 的 ToolCall/ToolResult 往返。
- 以显式 SSE 状态机聚合并行工具参数分片，缓存稀疏 `id/name` 元数据，在完整终止协议
  和 JSON 校验通过后才生成 `ResponseCompleted`。
- 构建安全 Provider HTTP 边界，通过超时、响应大小上限、URL/header 校验、client
  ownership 与静态公开错误防止泄密、无界响应和资源泄漏。
- 构建 Draft 2020-12 Tool Registry 与跨平台 WorkspaceBoundary，在执行前拦截非法参数、
  路径逃逸、Windows 特殊路径、链接、特殊/超大/二进制文件及遍历预算超限。
- 实现保留原始换行的 `read_file` 和 Unicode 列号正确的 literal `search_text`，通过
  `asyncio.to_thread` 避免有界磁盘 I/O 阻塞 Agent 事件循环。
- 实现模型不可自授权的 allow/ask/deny Policy 与治理执行器，写操作先生成相对路径、
  风险、理由和 bounded diff，只有显式交互审批后才允许落盘，非交互 `ask` 默认拒绝。
- 以原始字节 SHA-256 实现乐观并发控制，配合 create-only、唯一文本匹配、审批后重复
  校验和同目录原子替换，将陈旧修改从静默覆盖转化为无副作用 `conflict`。
- 实现 argv-only 受治理命令执行器，以最小环境、Workspace cwd、合并输出预算、超时/
  取消和跨平台进程树清理支撑测试构建；默认 deny，显式规则和交互审批后才启动进程。
- 实现确定性 Context Budget，在每次 Provider I/O 前估算完整请求，将 ToolCall/ToolResult
  作为不可拆分单元，保留原始目标、最新单元和所有副作用/未知工具交换，只淘汰较旧只读历史；
  超限时静态失败且不调用 Provider。
- 以有界 `ContextCompacted` 事件和完整 transcript SHA-256 记录省略证据，Runtime 继续持有
  全量消息，Provider 只接收选中窗口；不生成不可验证的滚动摘要。
- 实现 SQLite schema v1 Session/Run 与追加式 Trace，在一个 `BEGIN IMMEDIATE` 事务中同步
  写事件和 projection，以 UUID event ID 实现精确幂等，以 canonical JSON SHA-256 前驱链
  校验 sequence、payload 和 Session head。
- 将持久化从 best-effort 可观测性中分离为 required `EventJournal`：Provider/Tool 前持久化
  Started，失败即返回 `PERSISTENCE_ERROR` 并停止后续动作；真实双 `write_file` 故障注入证明
  第二个文件零落盘，started-only 状态保留供 M3c 判断。
- 实现 stable Checkpoint/Resume：在初始及完整 ToolResult 后原子保存 typed transcript，
  通过 Tool/Workspace fingerprint 与 Checkpoint 后增量 Trace 扫描阻断不确定副作用；
  claim 重新分析 plan，并在一个 SQLite 事务中中断旧 Run、启动新 Run、消费快照。
- 通过 Provider 进程崩溃重开恢复、真实治理写入后零重放、Workspace/Tool 漂移、篡改、
  stale plan、事务回滚和并发双 claim 测试；明确 Provider retry 为 at-least-once，
  Checkpoint 为有界明文且不宣称外部 exactly-once。
- 实现 hardened `git_status`/`git_diff`：解析 porcelain-v2 NUL 协议，严格限制仓库
  top-level、时间与输出，禁用 optional locks、fsmonitor、external diff、textconv 和
  submodule；真实恶意配置测试证明扩展零执行且 Git index 不发生写入。
- 实现受治理 `run_tests`：以宿主固定 Pytest Profile、Workspace target、execute 默认
  deny/独立审批和 argv-only 进程预算限制执行面；将 bounded JUnit 转换为 typed
  process/report 双状态、计数与 diagnostics，并明确审批不等于 OS sandbox。
- 通过真实 Pytest deny/approve/reject/non-interactive、临时报告清理及 Agent+SQLite
  Trace 集成测试，证明失败详情只进入当前模型交换而不进入生命周期 Trace。
- 实现宿主控制的有限 Repair：以 clean Git repository、exact tracked editable scope 和
  pre-policy ActionGuard 约束一次 Agent 修复，以固定 Pytest 和 Git/Workspace 前后证据
  判定结果，并通过 attempt/time/patch/prompt/重复失败预算确定性停止。
- 以 SQLite schema v3 独立记录 Repair lifecycle hash chain；真实集成验证一次缺陷修复、
  scope 外写入在审批和落盘前拒绝、dirty repo 零 Provider/Pytest，以及测试修改仓库即使
  报告通过也不能声明成功。
- 实现 provenance-aware 惰性 Skills：restricted YAML/Pydantic 校验、qualified source ID、
  regular-file/reparse 防护与 SHA/file identity 重验；正文只通过只读 Tool 以
  `untrusted_markdown` 返回，恶意指令无法绕过 deny Policy。
- 实现 monotonic authorization Tool Hooks：pre-Hook 仅 continue/veto 且 timeout/异常
  fail closed，post-Hook 错误隔离并保留原始 ToolResult；bounded audit 不记录参数、结果、
  Skill 正文或原始异常。
- 实现 host-pinned local stdio MCP：启动前审批 absolute executable/argv/cwd，验证
  protocol/server identity、exact Tool grant set 与 input/output schema hash；通过
  owner-worker 管理官方 SDK 跨 Task 生命周期，并让 alias 以 `TrustSource.EXTENSION`
  继续经过 Policy/approval 和有界结果校验。
- Python 3.12/3.13 各 678 项通过、5 项因 Windows symlink 权限跳过，分支覆盖率
  90.25%；Bandit/pip-audit 与 wheel/sdist 四组隔离安装 smoke 通过。
- 完成 Mini CodeAgent M0 工程基础：显式配置优先级、Pydantic 强类型边界、密钥安全 JSON 日志与 `doctor` 诊断 CLI。
- 建立 Ruff、严格 Pyright、Pytest 覆盖率门槛和哈希约束构建，Python 3.12/3.13
  各 583 项通过、4 项因 Windows symlink 权限跳过，分支覆盖率 89.97%。
- wheel 与 sdist 在 Python 3.12/3.13 的四组隔离环境中通过真实 console-script smoke；
  Bandit 无发现，pip-audit 未发现已知依赖漏洞。
- 对 wheel 与 sdist 分别执行隔离安装和真实 console-script smoke，并通过 `py.typed` 发布内联类型信息。
- 设计 Framework-light、Provider-neutral 的 Python Mini CodeAgent，完成 Agent Loop、工具协议、安全 Workspace、权限模型和可恢复执行方案。
- 为 Windows/Linux、严格类型、自动化测试、结构化 Trace 和 SemVer 发布定义工程验收标准。
- 建立覆盖路径逃逸、权限拒绝、上下文预算、故障恢复和修复闭环的验证计划。

### 8.2 发布后

以下内容只能在数据可由 CI、测试、Benchmark 或 Release 证明后使用：

- 从零实现并开源 Python Mini CodeAgent，支持 `__` 类模型接口、`__` 个内置工具及 Windows/Linux。
- 构建强类型 Tool Registry 和安全 Workspace，通过 `__` 项工具合约测试与 `__` 项路径逃逸测试。
- 实现 Checkpoint/Resume，在 `__` 个故障注入场景中达到 `__%` 恢复成功率。
- 建立 Git/test/repair 闭环，使固定基准任务通过率由 `__%` 提升至 `__%`。
- 配置 Ruff、严格 Pyright、Pytest 与跨平台 CI，核心模块覆盖率达到 `__%`。

## 9. 公开证据清单

- 架构图与 ADR。
- 安全模型和威胁边界。
- Provider 与 Tool 合约测试。
- Workspace 逃逸负向测试。
- Context Budget 示例 Trace。
- Checkpoint 故障注入测试。
- Git/test/repair 可复现实例。
- Windows/Linux GitHub Actions 记录。
- 覆盖率报告。
- Benchmark 方法、数据集和原始结果。
- SemVer Release、Changelog 和安装 smoke test。

## 10. 诚信约束

- 未完成功能不得写成已交付能力。
- 未通过可复现测试的数据不得写成性能成果。
- 目标覆盖率、目标成功率和目标兼容范围不能替代实测结果。
- 每条简历亮点最终必须能够链接到代码、测试、CI、文档或 Release 证据。
