# Mini CodeAgent 简历项目包

> 项目状态：M0 工程基础、M1 Agent Core、M1b 双 Provider、M2a 只读 Workspace/Tool
> Registry、M2b 受治理文件写入、M2c argv 命令执行与 M3a 确定性 Context Budget
> 、M3b 版本化 Session/追加式 Trace 及 M3c Checkpoint/Resume 已在本地完成；Shell 字符串、
> OS 沙箱、真实凭证联调、远程 CI 和 GitHub Release 尚未执行。
>
> 本文中的功能、性能和指标是目标或验收方案。只有得到代码、测试、CI、Benchmark 或 Release 证据后，才能改写为已完成成果。

## 1. 30 秒项目介绍

正在从零设计并实现一个面向真实软件工程任务的企业级 Python Mini CodeAgent。项目采用 Framework-light Agent Harness，通过统一 Provider 协议接入 Anthropic Messages 与 OpenAI-compatible Chat Completions，以跨平台 CLI 作为主要交互入口。当前已完成带硬限制和取消传播的 Agent Core、同步/流式模型适配、安全 HTTP 边界，以及由 JSON Schema Tool Registry、跨平台 WorkspaceBoundary、Read/Search、allow/ask/deny Policy、哈希防冲突 Write/Edit 和受治理 argv Command 组成的代码理解、修改与验证链路；每次模型调用前通过确定性 Context Budget 限制请求，并以 SQLite v1 Session/Run projection、required EventJournal、事务式追加 Trace 和 SHA-256 链持久化生命周期证据。

规划能力包括 Git、测试、诊断、修复闭环。工程侧以严格类型、自动化测试、Windows/Linux CI、安全模型和 SemVer 发布流程保证可维护性，并为 Skills、Hooks、MCP、Subagent 与 Worktree 扩展提供稳定边界。

## 2. 项目定位

- 项目类型：开源 AI Agent 基础设施 / Developer Tooling。
- 目标用户：希望理解、定制或嵌入 CodeAgent 的开发者与团队。
- 核心原则：Framework-light、Provider-neutral、CLI-first、安全默认、可恢复、可观测、可测试。
- 首发平台：Windows、Linux。
- `1.0.0` 边界：单 Agent 核心闭环、本地 Workspace、终端交互、人工权限确认、Skills、Hooks、MCP、受限 Subagent 与 Git Worktree。
- 后续扩展：TUI、复杂多 Agent 团队协调和远程执行。

## 3. 技术栈

最终技术栈以 `pyproject.toml`、ADR 和发布版本为准。

M0/M1/M1b/M2a/M2b/M2c/M3a/M3b 已实际使用 Python 3.12/3.13、`asyncio`、`Protocol`、`dataclasses`、uv、
Hatchling、Pydantic v2、pydantic-settings、Platformdirs、HTTPX、httpx-sse、Typer、Rich、
JSON Schema Draft 2020-12、stdlib `sqlite3`、SQLite WAL/事务/索引、canonical JSON、SHA-256、
Pytest、pytest-asyncio、Coverage、Ruff 与 Pyright；其余技术随对应里程碑落地。

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
| 状态持久化 | stdlib `sqlite3`、schema v2/v1 事务迁移、WAL、foreign key、Session/Run projection、stable Checkpoint、busy timeout |
| 可观测性 | 类型化 started/completed 事件、required EventJournal、追加式 Trace、usage、SHA-256 链 |
| Git 闭环 | Git CLI、status/diff、测试发现、诊断反馈、有限次数修复 |
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
10. Git、测试、诊断、修复闭环。
11. 企业级质量门禁与发布工程。
12. 面向 Skills、Hooks、MCP、Subagent、Worktree 的扩展架构。

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
| Checkpoint/Resume | 进程退出不应丢失完整上下文，但从旧 prompt 重跑可能重复真实写入 | SQLite schema v2 事务迁移、稳定 typed transcript、canonical JSON SHA-256、Tool/Workspace fingerprint、增量 Trace 风险扫描、显式 replay policy、原子 claim | 初始及完整 ToolResult 后保存；重开后验证兼容性，将旧 Run 标记 `INTERRUPTED`、新建 Run 并从下一逻辑 turn 继续 | 恢复可重放的 Provider/只读中断；阻断未纳入快照的 write/execute/network；防止伪造 plan、并发双 claim 和 stale TOCTOU | 14 项 Resume 分析/claim 单测与 2 项进程边界集成测试；并发 claim 连续 5 次均一个 winner；真实治理写入后 Resume 阻断且文件/审批各一次 |
| 版本化 Session 与追加式 Trace | 进程退出后纯内存事件不可查询；若 Trace 与状态分开写会出现索引/正文不一致，静默持久化失败还会继续产生副作用 | SQLite schema v1、WAL、`BEGIN IMMEDIATE`、foreign key、Session/Run materialized projection、UUID event ID、canonical JSON、SHA-256 前驱链、required `EventJournal`、bounded busy timeout | 单事务追加 typed lifecycle event 并更新 Run/Session；Provider/Tool 前记录 Started，完成后记录 Completed；支持重开查询、分页读取和全链验证 | 消除 Trace/投影跨文件提交缝隙，将持久化故障转化为 `PERSISTENCE_ERROR`，用 started-only 状态标记不确定副作用，阻止后续工具继续执行 | 42 项 persistence 单测与 3 项真实集成测试；覆盖幂等冲突、4 类篡改、锁超时、终态回滚、Secret 扫描及第二个治理写入零落盘 |
| Git/test/repair loop | 文件写完不等于任务完成 | Git status/diff、测试发现、诊断解析、有限重试 | 修改后运行验证，将失败反馈给 Agent 修复 | 建立修改、验证、修复、再验证闭环 | 首次通过率、修复后通过率、平均修复轮次 |
| 质量门禁 | 企业级项目需要稳定接口和回归保护 | Ruff、严格 Pyright、Pytest、85% 核心覆盖率门槛、哈希构建约束、CI、SemVer | 自动执行 lint、类型检查、测试、构建和安装验证 | 防止低质量变更进入发布版本 | Python 3.12/3.13 各 505 通过、3 项 symlink 权限跳过；90.09% 分支覆盖率；四组构建安装 smoke 通过；远程 CI 待验证 |
| 可扩展 Harness | Skills、Hooks、MCP、Subagent 会增加控制流复杂度 | 稳定 Protocol、EventBus、能力声明、依赖倒置 | 在不侵入 Agent Core 的前提下增加能力 | 避免扩展绕过权限、Trace 和 Session | 插件合约测试；扩展数量后续回填 |

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

“Agent 修改后不会直接宣布完成，而是检查 Git diff、运行测试、结构化解析失败，再在有限轮次内修复。最终状态会区分测试通过、重试耗尽、权限阻断和环境错误。”

### 7.8 Framework-light 的取舍

“Framework-light 不是拒绝依赖。我会使用 Pydantic、HTTP Client、Typer、SQLite 等成熟基础设施，但核心状态机、工具协议、权限模型和 Session 格式由项目控制，兼顾透明度与开发效率。”

### 7.9 企业级体现在哪里

“企业级不是功能数量，而是边界清晰、失败可诊断、状态可恢复、安全策略可测试、发布可重复。项目设置严格类型、测试覆盖率门槛、跨平台 CI、安全模型、SemVer 和发布 smoke test。”

### 7.10 如何避免过度设计

“首版先完成单 Agent 的最小完整闭环。Skills、Hooks、MCP、Subagent 和 Worktree 只沿已有 Tool、Event、Policy、Session 协议接入，不能绕过权限与 Trace。”

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
- 完成 Mini CodeAgent M0 工程基础：显式配置优先级、Pydantic 强类型边界、密钥安全 JSON 日志与 `doctor` 诊断 CLI。
- 建立 Ruff、严格 Pyright、Pytest 覆盖率门槛和哈希约束构建，Python 3.12/3.13
  各 505 项通过、3 项因 Windows symlink 权限跳过，分支覆盖率 90.09%。
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
