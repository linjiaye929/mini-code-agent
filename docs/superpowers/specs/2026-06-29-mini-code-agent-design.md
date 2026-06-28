# Mini CodeAgent 产品与学习设计

日期：2026-06-29  
状态：已确认，待实施计划  
工作名称：Mini CodeAgent（正式发布名称后续确定）

## 1. 目标

从零实现一个可发布到 GitHub 的 Python CodeAgent。项目既是完整学习路径，也是可安装、可测试、可恢复、可审计的终端产品，不以演示代码为交付标准。

最终产品需要具备：

- 自研、可理解、可测试的 Agent Loop。
- Anthropic 与 OpenAI-compatible Provider 适配。
- 强类型工具注册与原生 Tool Calling。
- 文件读取、搜索、写入、局部编辑、Shell、Git 等编程工具。
- Workspace 边界和 allow/ask/deny 权限治理。
- Session、Context Budget、上下文压缩、Checkpoint/Resume。
- 结构化事件、Trace、token/耗时/错误观测。
- Git diff、测试、lint 和失败修复闭环。
- Skills、Hooks、MCP、Subagent、Worktree 等 Claude Code 风格扩展。
- Windows/Linux 支持、自动化测试、CI、版本发布和完整文档。

## 2. 用户与成功标准

### 2.1 主要用户

- 希望理解 CodeAgent 内核而不只会使用框架的后端工程师。
- 希望在本地仓库中安全使用 AI 编程能力的开发者。
- 需要可二次开发 Agent Harness 的团队。

### 2.2 成功标准

达到 `1.0.0` 时必须有当前证据证明：

1. 可通过标准 Python 工具安装并启动 CLI。
2. Anthropic 和至少一个 OpenAI-compatible Provider 通过同一套合约测试。
3. Agent 能在受限 Workspace 中完成真实的跨文件修改任务。
4. 写操作、Shell 和 Git 操作均经过统一 Policy Engine。
5. 中断的任务可从持久化 Checkpoint 恢复。
6. 每次模型调用、工具调用、权限决策和错误都有结构化 Trace。
7. Windows/Linux、Python 3.12/3.13 CI 全部通过。
8. 核心模块测试覆盖率不低于 85%，并有端到端基准任务。
9. README、安全模型、架构说明、贡献指南和 Changelog 完整。
10. Skills、Hooks、MCP、受限 Subagent 和 Worktree 均有至少一条端到端验收链路。
11. GitHub Release、版本 Tag 和可复现安装流程可用。

## 3. 范围与非目标

### 3.1 首版范围

- CLI-first，不在早期同时维护 TUI。
- 单 Agent 长任务优先，在其稳定后引入 Subagent。
- 本地代码仓库优先，不在首版提供 SaaS 多租户平台。
- Provider-neutral，但不追求一开始支持所有模型厂商。
- 使用成熟基础库解决数据校验、CLI、数据库和 HTTP，不使用 Agent 框架隐藏核心循环。

### 3.2 非目标

- 不训练或微调基础模型。
- 不复刻 Claude Code 私有实现、私有提示词或品牌。
- 不使用 LangGraph 作为内核依赖；它可在后期作为可选工作流扩展。
- 不把正则命令黑名单描述为安全沙箱。
- 不在单 Agent 未稳定前堆叠复杂 MultiAgent 编排。

## 4. 总体架构

```text
CLI
 └─ AgentRuntime
     ├─ ModelProvider
     │   ├─ AnthropicProvider
     │   └─ OpenAICompatibleProvider
     ├─ ContextManager
     ├─ ToolRegistry
     │   ├─ Read / Write / Edit / Search
     │   └─ Shell / Git / Test
     ├─ PolicyEngine
     ├─ WorkspaceBoundary
     ├─ SessionStore / CheckpointStore
     └─ EventBus / TraceStore

Optional Extensions
 ├─ Skills
 ├─ Hooks
 ├─ MCP
 ├─ Subagents
 └─ Worktrees
```

核心原则：

- 模型只产生意图，所有现实动作必须经过工具层。
- Tool Registry 负责能力描述、Schema 校验和统一执行契约。
- Policy Engine 在执行前独立裁决，不能由 Prompt 替代。
- WorkspaceBoundary 对路径、符号链接和工作目录实施约束。
- EventBus 是 CLI、Hook、Trace 和未来 TUI 的统一事件源。
- Session 和 Checkpoint 保存可恢复状态，Trace 保存不可变执行事实。

## 5. 核心组件

### 5.1 AgentRuntime

职责：

- 驱动消息到模型再到工具结果的循环。
- 处理最大轮次、取消、超时和停止条件。
- 保证每个 ToolCall 只有一个可追踪结果。
- 通过 EventBus 发布生命周期事件。

它不负责具体 Provider、工具实现、权限规则或持久化细节。

### 5.2 ModelProvider

统一接口至少包含：

- 流式与非流式生成。
- Tool Schema 传递与 ToolCall 解析。
- token usage、finish reason 和错误归一化。
- 取消、超时、限流重试。
- Provider 能力声明。

首发实现：

- `AnthropicProvider`
- `OpenAICompatibleProvider`
- `FakeProvider`，用于可重复测试。

### 5.3 ToolRegistry

每个工具必须声明：

- 稳定名称和用途描述。
- Pydantic 输入 Schema。
- 结构化结果 Schema。
- 风险等级与副作用类型。
- 超时和取消能力。

工具执行失败返回结构化错误，不把任意异常或完整敏感堆栈直接发送给模型。

### 5.4 WorkspaceBoundary

职责：

- 规范化并解析所有文件路径。
- 拒绝根目录外访问。
- 防止 `..`、绝对路径和符号链接逃逸。
- 对文件大小、二进制文件和编码提供显式策略。
- 为写入、编辑和删除保留 diff/备份证据。

### 5.5 PolicyEngine

决策结果：

- `allow`：安全操作自动执行。
- `ask`：展示命令、目标、原因和风险后等待批准。
- `deny`：拒绝执行并向 Agent 返回可解释原因。

决策输入包括工具、参数、Workspace、信任来源、会话模式和用户规则。规则本身可测试、可审计，不能只依赖 Prompt。

### 5.6 ContextManager

职责：

- 维护模型上下文预算。
- 裁剪或落盘超长工具输出。
- 区分稳定指令、工作记忆、历史摘要和最近消息。
- 在阈值到达时生成可恢复压缩结果。
- 防止压缩丢失任务目标、未完成项、关键文件和验证结果。

### 5.7 Session、Checkpoint 与 Trace

- SQLite 保存 Session、Checkpoint 元数据、事件索引和版本。
- 文件系统保存长输出、补丁、摘要和其他大对象。
- Checkpoint 用于恢复可变工作状态。
- Trace 记录不可变生命周期事实，用于调试、评估和审计。

### 5.8 Git 与验证闭环

- 修改前确认仓库状态，绝不覆盖用户未提交修改。
- 修改后展示 diff，并运行发现到的测试/lint 命令。
- 验证失败可返回 Agent 修复，但受最大尝试次数限制。
- 自动提交必须显式开启，默认只生成建议提交信息。

## 6. 调用与数据流

```text
User Input
  -> CLI
  -> Session Load
  -> Context Build
  -> ModelProvider
  -> ToolCall
  -> Schema Validation
  -> Policy Decision
  -> Tool Execution
  -> Event + Trace + Checkpoint
  -> ToolResult
  -> Context Build
  -> ModelProvider
  -> Final Response
```

所有关键步骤都产生带 `session_id`、`turn_id`、`call_id` 和时间戳的事件。

## 7. 错误处理

错误按边界归一化：

- Provider：认证、限流、超时、服务端、无效响应。
- Tool：参数无效、权限拒绝、执行失败、超时、取消。
- Workspace：越界、符号链接逃逸、冲突、文件过大。
- Persistence：数据库锁、迁移失败、损坏 Checkpoint。
- Agent：最大轮次、重复 ToolCall、无进展循环、上下文超限。

可重试错误使用有上限的指数退避；权限拒绝、Schema 错误和越界访问不自动重试。所有用户可见错误提供下一步操作，不泄露密钥。

## 8. 安全模型

最低要求：

- 默认最小权限。
- 读工具和写工具分开授权。
- Shell 使用显式模式和超时；普通结构化工具不通过 Shell 间接实现。
- 环境变量、日志和 Trace 中的 Secret 自动脱敏。
- 仓库内配置、Skills、Hooks 和 MCP 均视为不可信输入。
- 项目配置首次启用时展示来源和权限。
- 高风险操作必须有独立于模型的审批。

容器或操作系统级沙箱属于增强能力。未启用时，产品必须明确说明其安全边界，不能声称达到进程级隔离。

## 9. 质量与测试

工具链：

- `uv`：环境、依赖、锁文件、构建。
- `Pydantic`：领域 Schema 和边界校验。
- `Typer`：CLI。
- `SQLite`：本地状态与索引。
- `pytest`、`pytest-asyncio`、`coverage`：测试。
- `Ruff`、`Pyright`：静态质量。

测试层级：

1. 领域单元测试：消息、事件、策略、路径、状态迁移。
2. Provider 合约测试：所有 Provider 遵守同一语义。
3. Tool 合约测试：Schema、权限、超时、结构化错误。
4. 集成测试：Fake Provider 驱动完整 Agent Loop。
5. CLI 测试：启动、交互、恢复和退出码。
6. 安全测试：路径逃逸、符号链接、命令注入、Secret 脱敏。
7. 端到端基准：在固定样例仓库完成修改并通过测试。

CI 矩阵：

- Windows / Linux
- Python 3.12 / 3.13
- lint、type check、unit、integration、coverage、安全依赖审计、package build

## 10. 分阶段交付

### M0：工程骨架

- 独立 Git 仓库、`uv` 项目、包结构。
- 配置、日志、CLI 健康检查入口。
- Ruff、Pyright、Pytest、Coverage、CI。
- ADR、威胁模型和贡献规范。

### M1：最小 Agent 内核

- 领域模型、Fake Provider、Provider 接口。
- 原生 Tool Calling Agent Loop。
- 只读工具与确定性集成测试。

### M2：安全工具系统

- Read/Write/Edit/Search/Shell/Git。
- WorkspaceBoundary、PolicyEngine、审批和 Diff。
- 路径与命令安全测试。

### M3：长任务能力

- Session、Context Budget、输出落盘、压缩。
- Checkpoint/Resume、Trace、取消和错误恢复。

### M4：编程闭环

- 仓库上下文、Git 状态保护。
- test/lint 发现与修复循环。
- 固定样例仓库端到端基准。

### M5：Claude Code 风格扩展

- Skills、Hooks、MCP、Plan/read-only 模式。
- 扩展来源信任与权限模型。

### M6：高级能力与发布

- Subagent、Worktree 隔离。
- 性能与行为评测。
- README、示例、Changelog、Release、GitHub 发布。

TUI 和复杂多 Agent 团队协调在 CLI 与受限 Subagent 稳定后单独设计，不属于 `1.0.0` 必交范围。

## 11. 学习协作方式

每个里程碑采用：

1. 原理讲解和 Java 概念映射。
2. 用户完成一个关键设计判断或小练习。
3. 先写失败测试，再写最小实现。
4. 运行静态检查和测试。
5. 代码审查并记录 ADR。
6. 形成可演示验收证据。
7. 创建阶段性 Git Tag。

配套文档：

- `docs/learning/knowledge-map.md`：前置知识和里程碑知识地图。
- `docs/resume/project-profile.md`：简历介绍、技术栈、亮点、动机与证据。

## 12. 开源借鉴原则

主要参考方向：

- Learn Claude Code：渐进式 Harness 学习顺序。
- MokioAgent：LangGraph、Context、Checkpoint、Trace 的教学表达。
- mini-swe-agent：最小软件工程 Agent 循环。
- Aider：编辑格式、仓库上下文、Git 与 lint/test 闭环。
- Claude Code 官方文档：Hooks、权限、MCP 和生命周期概念。
- OpenCode：终端产品、模式隔离和扩展体验。

规则：

- 借鉴问题拆分、接口和测试思想，不复制不理解的实现。
- 引入任何代码前检查许可证、版权声明和 NOTICE 要求。
- 记录来源和本项目的差异化设计。
- 不宣称复刻未公开的私有实现。

## 13. 待后续决定

以下决策不阻塞 M0：

- 正式项目名和 PyPI 包名。
- 默认模型与默认 OpenAI-compatible 服务。
- 操作系统级沙箱的首个实现方案。
