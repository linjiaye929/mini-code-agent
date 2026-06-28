# Mini CodeAgent 学习知识地图

面向 Java 后端与 Flink/Spark SQL 开发者。目标不是学完所有 Agent 框架，而是通过实现一条可验证的纵向链路，掌握 Agent Harness：

```text
用户输入
  -> Provider
  -> ToolCall
  -> Schema 校验
  -> 权限判断
  -> 工具执行
  -> ToolResult
  -> Agent 决策
  -> Session / Checkpoint / Trace
```

## 1. 学习与工程约束

- Python 3.12/3.13。
- 使用 `uv` 管理环境、依赖、锁文件和构建。
- 使用 Pydantic 定义配置、消息、工具参数与持久化边界。
- SQLite 保存 Session、Checkpoint 元数据和事件索引；JSONL/文件保存完整 Trace 与大对象。
- Provider-neutral，首发支持 Anthropic 与 OpenAI-compatible。
- CLI-first，同时支持 Windows 与 Linux。
- 不以 LangGraph 作为运行内核。
- 所有循环、权限、重试和资源消耗必须有明确上限。

## 2. 前置知识分级

### 2.1 开始 M0/M1 前必须掌握

#### Python

- 模块、包、导入规则、src layout 和 `pyproject.toml`。
- 类型标注、泛型、`Protocol`、`Literal`、联合类型。
- `dataclass` 与 Pydantic `BaseModel` 的区别。
- 异常链、异常分类和资源清理。
- `pathlib`、文件编码、换行符与原子写入。
- `subprocess`、退出码、stdin/stdout/stderr 和超时。
- `asyncio`、协程、取消和超时的基本语义。
- 上下文管理器、迭代器、异步迭代器。
- JSON 序列化和领域对象边界。

#### Agent

- Chat Message、System Prompt、Tool Definition、ToolCall、ToolResult。
- Agent Loop 与普通 Chat Completion 的区别。
- 原生 Tool Calling，而不是用正则从自然语言解析命令。
- 上下文窗口、token budget 和停止条件。
- Prompt Injection 与工具执行风险。
- 模型输出和仓库内容都不可信。

#### 工程

- HTTP API、流式响应、超时、重试和限流。
- SQLite 事务、索引、迁移和 WAL 基础。
- Git status、diff、工作区、分支、提交和 Worktree。
- 单元测试、集成测试、契约测试。
- 配置、Secret 和日志脱敏。
- Windows/Linux 路径与 Shell 差异。

### 2.2 在里程碑中边做边学

- Anthropic 与 OpenAI-compatible 协议差异。
- JSON Schema 与工具参数校验。
- token 估算与上下文压缩。
- CLI 权限确认与非交互模式。
- Patch、冲突检测和文件快照。
- SQLite 事件模型与 Checkpoint。
- 类型化事件、结构化日志和关联 ID。
- Git/test/repair 闭环。
- Skills、Hooks、MCP。
- Subagent 的预算、隔离和结果汇总。
- Python 打包、SemVer 和跨平台 CI。

### 2.3 `1.0.0` 后再学

- 复杂 MultiAgent 调度算法。
- 高并发 ToolCall。
- 分布式任务队列和远程执行集群。
- 容器、虚拟机或 OS 级强沙箱。
- OpenTelemetry 分布式链路。
- 大规模 Agent Eval 数据集。
- 插件签名和软件供应链证明。
- 长期记忆、向量数据库和复杂 RAG。
- Web UI、IDE 插件和 TUI。

## 3. 学习单元与产品里程碑映射

| 学习单元 | 内容 | 产品里程碑 |
|---|---|---|
| L0 | Python 工程骨架 | M0 |
| L1 | Agent Loop | M1 |
| L2 | Provider 与 Tool Calling | M1 |
| L3 | Tool Registry | M2 |
| L4 | Workspace 与权限 | M2 |
| L5 | File/Edit/Shell/Git 工具 | M2 |
| L6 | Context Budget 与压缩 | M3 |
| L7 | Session/Checkpoint/Trace | M3 |
| L8 | Git/test/repair | M4 |
| L9 | Skills 与 Hooks | M5 |
| L10 | MCP | M5 |
| L11 | Subagent 与 Worktree | M6 |
| L12 | CI、Benchmark 与发布 | M6 |

## 4. 学习单元

### L0：Python 工程骨架

**理论**

- 依赖管理、可重复构建和配置分层。
- 领域模型与基础设施实现分离。
- Framework-light 不等于无结构。

**Python**

- `uv`、`pyproject.toml`、src layout。
- Pydantic Settings、类型标注。
- Typer、Ruff、Pyright、Pytest、Coverage。

**工程**

- 配置优先级：默认值、文件、环境变量、CLI。
- Secret 不进入日志、SQLite 或 Git。
- Windows/Linux CI 矩阵。

**验收练习**

- 在干净环境中一条命令安装并运行测试。
- CLI 输出版本、配置来源和诊断信息。
- 缺少配置时返回可操作错误，而不是堆栈噪声。

### L1：最小 Agent Loop

**理论**

- Agent Loop 是受控状态机，不是无限 `while`。
- 状态至少包含消息、轮次、预算和停止原因。
- 停止原因区分完成、失败、取消和超限。

**Python**

- 枚举、判别联合、模式匹配。
- 异常边界、不可变事件、依赖注入和 Fake。

**工程**

- 最大轮次、超时、取消和错误传播。
- 核心循环不直接依赖具体 Provider SDK。

**验收练习**

- Fake Provider 驱动“回答 → 调工具 → 回答”的完整循环。
- 达到最大轮次后确定性停止。
- Provider、工具和持久化失败都不会形成无限循环。

### L2：Provider 抽象与原生 Tool Calling

**理论**

- 统一领域模型与 Provider 原始模型的区别。
- Capability：工具、流式、并行调用和 usage。
- ToolCall ID 必须关联调用与结果。
- Retry 只适用于可重试且满足幂等约束的操作。

**Python**

- `Protocol`、适配器、泛型。
- HTTP/SDK 异步调用和流式迭代。

**工程**

- Anthropic 与 OpenAI-compatible 消息转换。
- 认证、限流、超时、协议和服务端错误分类。
- 原始响应脱敏和大小限制。

**验收练习**

- 同一 Agent Loop 无修改切换两个 Provider。
- 契约测试验证两种适配器输出相同领域事件。
- 模拟限流、超时和畸形响应。

### L3：Tool Registry 与参数校验

**理论**

- 工具由 Schema、执行器和风险元数据组成。
- 模型提出调用不代表系统必须执行。
- 参数校验、权限判断和执行是三个独立阶段。

**Python**

- Pydantic、JSON Schema、Generics。
- 同步与异步函数的统一包装。
- 装饰器和反射的适用边界。

**工程**

- 工具名冲突、禁用和发现。
- 结构化 ToolResult/ToolError。
- 输出长度限制、截断和落盘引用。

**验收练习**

- 注册、查找、禁用和调用工具。
- 非法参数无法进入执行器。
- 未知工具返回可供模型修正的结构化错误。
- Registry 不依赖 Provider SDK。

### L4：Workspace 安全边界与权限

**理论**

- 模型输出、用户仓库和第三方扩展都是不可信输入。
- Workspace 边界不能只做字符串前缀判断。
- 策略决策与工具执行必须分离。

**Python**

- `pathlib.resolve`、符号链接和路径规范化。
- 纯函数式规则评估。

**工程**

- 防止 `..`、绝对路径、symlink、盘符和 UNC 绕过。
- allow/ask/deny 规则支持工具、路径、命令和会话范围。
- 非交互环境遇到 ask 时默认拒绝。
- 每次权限决定写入 Trace。

**验收练习**

- 路径穿越和符号链接负向测试。
- deny 永不进入执行器。
- ask 只有明确确认后执行。
- 决策结果能说明命中的规则和原因。

### L5：文件编辑与 Shell/Git 工具

**理论**

- 读、写、Patch、Shell 和 Git 的风险不同。
- 文件修改需要前置条件，避免覆盖并发变化。
- Shell 字符串不是跨平台抽象。

**Python**

- 原子替换、编码、换行符。
- `subprocess`、进程组、超时、取消和输出流。

**工程**

- 限制 cwd、环境变量、执行时间和输出大小。
- 优先 argv；显式区分 Shell 与直接进程模式。
- 修改前后生成 diff。
- 二进制、超大和敏感文件默认拒绝。

**验收练习**

- 文件变化后旧 Patch 被拒绝，不静默覆盖。
- 超时命令及其子进程可终止。
- stdout、stderr、退出码和截断状态完整返回。
- 同一测试集通过 Windows 与 Linux。

### L6：Context Budget 与压缩

**理论**

- 上下文管理是信息保留问题，不只是删除旧消息。
- 区分稳定指令、目标、工作记忆、工具结果和历史。
- 压缩是有损操作，必须保存来源和版本。

**Python**

- token 估算接口、滑动窗口、优先级。
- 结构化摘要校验。

**工程**

- 预留模型输出和 ToolCall 空间。
- 大型工具输出先裁剪、提取或落盘。
- 压缩失败使用保守降级。

**验收练习**

- 构造超长会话触发压缩。
- 压缩后保留目标、限制、未完成项和验证结果。
- Trace 说明删除、保留和压缩了什么。

### L7：Session、Checkpoint、Resume 与 Trace

**理论**

- Session 是逻辑会话，Checkpoint 是可恢复状态。
- Trace 是追加式事实记录，不等于普通日志。
- Resume 必须验证 Workspace、配置和 Schema 兼容性。

**Python**

- SQLite 事务、迁移、索引、WAL。
- JSON 字段版本化和事务边界。

**工程**

- 保存消息、轮次、预算、ToolCall 状态和停止原因。
- Checkpoint 不保存明文 Secret。
- 使用 session/run/turn/call ID 关联事件。
- Trace 包含耗时、usage、权限和脱敏错误。

**验收练习**

- 在工具执行前后注入中断并恢复。
- 重复 Resume 不会重放已完成的高风险操作。
- 旧 Session 可迁移或明确拒绝。
- 根据 Trace 重建关键时间线。

### L8：Git、测试与 Repair Loop

**理论**

- Repair Loop 是有预算的反馈控制循环。
- 测试失败不自动证明最近一次修改错误。
- Git diff 是变更证据，不是安全保证。

**Python**

- Git 子进程封装、测试输出解析。
- 失败分类和有限状态转换。

**工程**

- 修改前检查工作区状态。
- 不覆盖或回滚用户已有修改。
- 限制修复轮次、时间、token 和改动范围。
- 每轮记录 diff、命令和失败摘要。

**验收练习**

- 完成“修改 → 测试失败 → 修复 → 通过”。
- 达到上限后停止并保留诊断。
- 无测试项目与测试命令失败能正确区分。

### L9：Skills 与 Hooks

**理论**

- Skill 是可发现、按需加载、受约束的能力说明。
- Hook 是生命周期扩展点，不能破坏核心状态机。
- 外部说明和 Hook 配置可能包含恶意内容。

**Python**

- 插件发现、entry points、动态导入。
- Hook 协议、优先级和错误隔离。

**工程**

- Skill 信任级别和能力范围。
- Hook 同步/异步、顺序和失败策略。
- 防止同名覆盖与循环加载。
- Trace 记录来源和版本。

**验收练习**

- 加载、禁用和冲突检测。
- Hook 失败不破坏事务或权限边界。
- 不可信 Skill 无法绕过 deny。

### L10：MCP

**理论**

- MCP 包括客户端、服务器、能力和传输。
- 远程 MCP 工具仍需经过本地 Registry 与 Policy。
- 连接成功不代表工具可信。

**Python**

- JSON-RPC、stdio 异步流、生命周期和资源清理。

**工程**

- 初始化、能力协商、工具同步和断线恢复。
- MCP Schema 映射到内部 ToolDefinition。
- 输出大小、超时和权限限制。

**验收练习**

- 连接最小 MCP Server 并调用工具。
- Server 崩溃后 Agent 受控失败。
- MCP 工具不能绕过 Workspace 和权限规则。

### L11：Subagent 与 Worktree

**理论**

- Subagent 是有预算的子任务执行者，不允许无限递归。
- 隔离维度包括上下文、文件、Git 和权限。
- 返回结果必须携带证据，不只给自然语言结论。

**Python**

- 并发任务、取消传播、结构化聚合、进程生命周期。

**工程**

- 限制深度、数量、token、时间和工具权限。
- 使用 Worktree 隔离可能冲突的修改。
- 父 Agent 对合并和最终输出负责。

**验收练习**

- 两个只读 Subagent 并行分析独立问题。
- 子任务超时不阻塞父 Agent。
- Worktree 不污染主工作区。
- 冲突时停止并提供 diff，不强行合并。

### L12：CI、Benchmark 与发布

**理论**

- 企业级意味着可诊断、可升级、可回滚和可审计。
- 兼容性包括 CLI、配置、数据库和 Provider 合约。
- 发布物必须可复现。

**Python**

- Wheel、版本、包元数据和 CLI entry point。
- 测试矩阵、Coverage 和数据库迁移测试。

**工程**

- Windows/Linux × Python 3.12/3.13 CI。
- 单元、集成、契约、安全和端到端测试。
- 依赖锁定、Secret 扫描、制品校验。
- SemVer、Changelog 和升级说明。

**验收练习**

- 从干净环境安装发布制品并运行。
- 主体 CI 不依赖真实 Provider Secret。
- 旧配置和数据库升级路径经过测试。
- Release 失败不会产生半成品版本。

## 5. Java 与大数据概念映射

| Python / Agent | Java / 大数据近似概念 | 关键差异 |
|---|---|---|
| `uv` + `pyproject.toml` | Maven/Gradle | 同时管理虚拟环境、锁文件和打包 |
| `Protocol` | `interface` | 支持结构化子类型，不要求显式 implements |
| Pydantic Model | POJO + Jackson + Bean Validation | 校验、Schema、序列化集中在一个模型 |
| `dataclass` | Record/POJO | 更轻，不提供完整运行时校验 |
| 装饰器 | Annotation + Wrapper/AOP | 装饰器直接在运行时转换可调用对象 |
| 上下文管理器 | try-with-resources | 用于事务、文件、锁和临时资源 |
| `asyncio` | CompletableFuture/Reactor | 阻塞调用会阻塞事件循环 |
| `pathlib.Path` | `java.nio.file.Path` | 同样需要处理符号链接和规范化 |
| `subprocess` | ProcessBuilder | Shell、转义和子进程终止差异更明显 |
| pytest fixture | JUnit Extension/Test Fixture | 组合更动态 |
| Python entry point | ServiceLoader/SPI | 常用于 CLI 与插件发现 |
| SQLite | JDBC 嵌入式数据库 | 关注单写者、事务和 WAL |
| Agent Loop | 状态机 / Flink 算子循环 | 必须显式控制预算、停止和副作用 |
| Tool Registry | Command Bus + Bean Registry | 工具 Schema 会暴露给模型 |
| Provider Adapter | 外部服务 Adapter | 还需归一化 ToolCall 与流式语义 |
| Checkpoint | Flink Checkpoint 的简化类比 | 恢复本地 Agent 状态，不解决分布式一致性 |
| Trace Event | 审计事件 / Flink 指标与日志 | 关联模型、工具、权限和成本 |

不要机械照搬 Spring：

- 优先显式构造和小型工厂，不先建 DI 容器。
- 优先 `Protocol` 和组合，不建立深层继承。
- 不为每个类创建接口，只为真实多实现和测试替身抽象。
- 不把所有异常包装成一个通用业务异常。
- 不把 Agent Loop 拆成大量无法独立理解的 Service。

## 6. 推荐顺序约束

1. 没有 Fake Provider 测试前，不接第二个真实 Provider。
2. 没有权限边界前，不开放 Shell 写操作。
3. 没有 Session 和 Trace 前，不做自动 Repair。
4. 没有单 Agent 稳定闭环前，不做 Subagent。
5. 没有本地 Tool Registry 前，不接 MCP。
6. 没有明确预算前，不做并行和递归。

## 7. 防止教程地狱

1. 每个知识点必须服务当前里程碑，用不到的进入 backlog。
2. 保持约 30% 学习、70% 实现与验证。
3. 一次只引入一个主要未知量。
4. 每个里程碑都产生可运行成果和自动化测试。
5. 优先阅读官方协议、SDK 类型和源码测试。
6. 遇到问题先做最小复现，不立即换框架。
7. 为关键选择写短 ADR，记录替代方案和代价。
8. 连续研究两小时仍无法转化为验收项，就退回最小需求。
9. 第一版不提前建设 Web UI、分布式调度或插件市场。
10. 以失败场景定义企业级质量：超时、取消、拒绝、恢复和损坏都要可测试。

## 8. 每个单元的完成定义

一个学习单元只有同时满足以下条件才算完成：

- 输入、输出和边界清晰。
- 正常路径可运行。
- 至少一个失败路径有测试。
- 循环和资源有上限。
- 错误可诊断。
- 不依赖人工篡改内部状态。
- 能说明与下一单元的接口。
- Windows/Linux 差异已验证或明确记录。

最终目标不是功能最多，而是行为可预测、权限可控制、状态可恢复、过程可追踪、扩展边界清晰。

