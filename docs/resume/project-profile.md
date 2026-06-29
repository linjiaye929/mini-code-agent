# Mini CodeAgent 简历项目包

> 项目状态：M0 工程基础与 M1 Provider-neutral Agent Core 已在本地完成；真实模型 Adapter 待 M1b。
>
> 本文中的功能、性能和指标是目标或验收方案。只有得到代码、测试、CI、Benchmark 或 Release 证据后，才能改写为已完成成果。

## 1. 30 秒项目介绍

正在从零设计并实现一个面向真实软件工程任务的企业级 Python Mini CodeAgent。项目采用 Framework-light Agent Harness，计划通过统一 Provider 协议兼容 Anthropic 与 OpenAI-compatible 模型，以跨平台 CLI 作为主要交互入口。当前已完成可复现 Python 工程骨架、强类型配置、密钥安全日志、诊断 CLI，以及带原生 ToolCall、硬限制、超时、取消传播和类型化事件的 Provider-neutral Agent Core。

规划能力包括可解释 Agent Loop、强类型 Tool Registry、安全 Workspace、allow/ask/deny 权限决策、文件编辑与 Shell 工具、Context Budget 与压缩、Session/Checkpoint/Resume、结构化 Trace，以及 Git、测试、诊断、修复闭环。工程侧以严格类型、自动化测试、Windows/Linux CI、安全模型和 SemVer 发布流程保证可维护性，并为 Skills、Hooks、MCP、Subagent 与 Worktree 扩展提供稳定边界。

## 2. 项目定位

- 项目类型：开源 AI Agent 基础设施 / Developer Tooling。
- 目标用户：希望理解、定制或嵌入 CodeAgent 的开发者与团队。
- 核心原则：Framework-light、Provider-neutral、CLI-first、安全默认、可恢复、可观测、可测试。
- 首发平台：Windows、Linux。
- `1.0.0` 边界：单 Agent 核心闭环、本地 Workspace、终端交互、人工权限确认、Skills、Hooks、MCP、受限 Subagent 与 Git Worktree。
- 后续扩展：TUI、复杂多 Agent 团队协调和远程执行。

## 3. 技术栈

最终技术栈以 `pyproject.toml`、ADR 和发布版本为准。

M0/M1 已实际使用 Python 3.13、`asyncio`、`Protocol`、uv、Hatchling、Pydantic v2、
pydantic-settings、Platformdirs、Typer、Rich、Pytest、pytest-asyncio、Coverage、Ruff 与
Pyright；其余技术随对应里程碑落地。

| 分类 | 技术 |
|---|---|
| 语言与运行时 | Python 3.12/3.13、`asyncio`、`dataclasses`、`enum` |
| 类型系统 | `typing.Protocol`、Generics、TypedDict、严格 Pyright |
| 模型接入 | Anthropic Adapter、OpenAI-compatible Adapter、HTTP/SSE 流式传输 |
| 数据模型 | Pydantic v2、JSON Schema |
| CLI | Typer、Rich |
| 工具系统 | 强类型 Tool Registry、统一 Tool Result/Error |
| 文件能力 | `pathlib`、路径规范化、原子写入、差异编辑、编码与换行处理 |
| Shell 能力 | `subprocess`、PowerShell/POSIX shell、超时、取消、输出限制 |
| 状态持久化 | SQLite、版本化 Session/Checkpoint Schema、文件大对象存储 |
| 可观测性 | 类型化事件、JSONL Trace、correlation ID、usage、脱敏 |
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
6. 跨平台文件编辑与 Shell 执行。
7. Context Budget 与可恢复压缩。
8. Session、Checkpoint 与 Resume。
9. 结构化 Trace 与可观测性。
10. Git、测试、诊断、修复闭环。
11. 企业级质量门禁与发布工程。
12. 面向 Skills、Hooks、MCP、Subagent、Worktree 的扩展架构。

## 5. 亮点拆解

| 亮点 | 为什么需要 | 技术实现 | 实现功能 | 解决的问题 | 指标或证据 |
|---|---|---|---|---|---|
| 可解释 Agent Loop | 重型框架容易隐藏状态流转、错误传播和模型调用成本 | 显式状态机、`asyncio`、批次预校验、类型化事件、最大轮次/ToolCall/超时限制 | 编排模型请求、原生 ToolCall、结果回传、取消和确定性停止 | 避免无限循环、部分副作用、隐式控制流和框架锁定 | M1 27 项 Runtime 单测与 1 项完整 ToolCall 集成测试通过 |
| Provider-neutral Adapter | 单一供应商会带来协议、能力和成本锁定 | `Protocol`、递归不可变 Message/ToolCall、同步/流事件、capability、usage 与错误归一化 | Agent Core 仅依赖领域协议，不导入厂商 SDK | 隔离消息格式、流式事件、usage 和错误语义差异 | Provider 合约与 Fake Provider 6 项测试通过；真实 Adapter 待 M1b |
| 强类型 Tool Registry | 动态参数容易产生缺失、类型漂移和不可解释错误 | Pydantic、JSON Schema、Generics、统一 Result/Error | 注册、发现、校验和调用工具 | 将错误拦截在工具边界，提高可维护性 | Schema snapshot、负向测试、静态类型检查 |
| 安全 Workspace | Agent 文件权限过大可能影响工作区外文件 | `pathlib`、canonicalization、根目录约束、symlink 检查、原子写入 | 将所有文件操作限制在指定 Workspace | 防止 `../`、绝对路径和符号链接逃逸 | 路径逃逸测试数量和通过率待回填 |
| 权限决策系统 | 文件写入、命令和网络操作具有不同风险 | Policy Engine、风险分级、规则匹配、交互审批、默认拒绝 | 按工具、参数、路径决定 allow/ask/deny | 保留人在回路，控制真实副作用 | Policy matrix、审批 Trace、安全回归测试 |
| 跨平台 Edit/Shell | Windows/Linux 的 shell、路径、编码和信号行为不同 | `subprocess`、Shell Adapter、UTF-8、超时、取消、输出截断 | 可靠编辑文件并执行受控开发命令 | 避免进程失控、日志爆量和平台漂移 | Windows/Linux CI、超时终止测试 |
| Context Budget | 长任务会超过上下文窗口，直接截断会丢关键事实 | token estimator、消息优先级、滚动摘要、工具输出落盘 | 预算预估、压缩、保留任务目标和未完成项 | 降低上下文超限和无效 token 消耗 | 压缩前后 token、关键信息 golden test |
| Checkpoint/Resume | 网络错误、进程退出和人工中断不应导致全部重跑 | SQLite、版本化 Schema、原子快照、幂等恢复 | 保存会话状态并从中断点继续 | 提高长任务容错和问题复现能力 | 故障注入场景、恢复成功率和耗时待回填 |
| 结构化 Trace | 文本日志无法回答 Agent 为什么执行某动作 | 类型化事件、correlation ID、耗时、usage、JSONL、脱敏 | 记录模型、工具、权限、压缩、恢复和错误事件 | 支持调试、审计、成本分析和行为评估 | Trace Schema 覆盖、解析测试、脱敏测试 |
| Git/test/repair loop | 文件写完不等于任务完成 | Git status/diff、测试发现、诊断解析、有限重试 | 修改后运行验证，将失败反馈给 Agent 修复 | 建立修改、验证、修复、再验证闭环 | 首次通过率、修复后通过率、平均修复轮次 |
| 质量门禁 | 企业级项目需要稳定接口和回归保护 | Ruff、严格 Pyright、Pytest、85% 核心覆盖率门槛、哈希构建约束、CI、SemVer | 自动执行 lint、类型检查、测试、构建和安装验证 | 防止低质量变更进入发布版本 | 本地 73 项通过、1 项因 Windows symlink 权限跳过、95.82% 覆盖率、wheel/sdist 安装 smoke；远程 CI 待验证 |
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

“压缩不能简单删除最早消息。我会区分稳定指令、用户目标、工作记忆、最近工具结果和历史细节，按优先级保留。压缩事件写入 Trace，并通过 golden test 验证关键事实未丢失。”

### 7.6 Checkpoint 与 Resume

“Checkpoint 不只保存聊天记录，还包含 Agent 状态、预算、工具结果、权限决策和 Schema 版本。恢复时需要防止有副作用的 ToolCall 被重复执行，因此会设计调用 ID、幂等边界和故障注入测试。”

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
- 定义同步/流式 Provider Protocol、能力声明、usage 与公开错误模型，并以 ScriptedProvider 在无网络/Secret 条件下验证完整链路；真实 Adapter 待 M1b。
- 完成 Mini CodeAgent M0 工程基础：显式配置优先级、Pydantic 强类型边界、密钥安全 JSON 日志与 `doctor` 诊断 CLI。
- 建立 Ruff、严格 Pyright、Pytest 覆盖率门槛和哈希约束构建，本地 73 项测试通过、1 项因 Windows symlink 权限跳过，分支覆盖率 95.82%。
- 对 wheel 与 sdist 分别执行隔离安装和真实 console-script smoke，并通过 `py.typed` 发布内联类型信息。
- 设计 Framework-light、Provider-neutral 的 Python Mini CodeAgent，完成 Agent Loop、工具协议、安全 Workspace、权限模型和可恢复执行方案。
- 为 Windows/Linux、严格类型、自动化测试、结构化 Trace 和 SemVer 发布定义工程验收标准。
- 建立覆盖路径逃逸、权限拒绝、上下文压缩、故障恢复和修复闭环的验证计划。

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
