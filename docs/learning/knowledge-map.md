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
- Adapter 是防腐层：Agent Core 不应该知道 `tool_use`、`tool_calls` 或 SSE 厂商事件名。
- 流式输出不是“不断返回字符串”，而是带生命周期、索引、终止原因和 usage 的协议。
- Tool arguments 在流中只是 JSON 片段；只有终止后组成完整对象才能执行。

**Python**

- `Protocol` 对应 Java interface，但使用结构化子类型，不要求显式 `implements`。
- `async def` + `yield` 产生异步生成器，对应带背压消费语义的简化 Reactive Stream。
- `async with` 管理 HTTP response/SSE/client 生命周期，对应 Java `try-with-resources`。
- `dataclass(slots=True)` 保存流解析状态，Pydantic 在不可信 wire boundary 做运行时校验。
- 依赖注入 `httpx.AsyncClient` 与 `MockTransport`，对应注入 HTTP client 并使用 mock server。
- 判别联合、`Literal`、类型收窄和严格 Pyright 保证不同事件分支完整处理。

**工程**

- Anthropic：system 位于顶层，工具调用/结果是 assistant/user content block。
- OpenAI-compatible：system 是消息，工具调用位于 assistant，结果使用独立 `tool` role。
- Anthropic 流按 content index 管理 block start/delta/stop；OpenAI 流按 tool index 缓存
  首块 `id/name`，后续块只追加 arguments。
- 认证、限流、超时、协议和服务端错误分类。
- 原始响应脱敏、请求 ID 截断、HTTP body/SSE 累计大小限制。
- Client ownership：内部 client 由 Adapter 关闭，外部注入 client 由调用方关闭。
- Adapter 只分类错误，不自动重试；重试次数、退避、总耗时和模型成本由编排层控制。

**验收练习**

- 同一 Agent Loop 无修改切换两个 Provider。
- 契约测试验证两种适配器输出相同领域事件。
- 模拟限流、超时和畸形响应。
- 手工跟踪两个并行 OpenAI tool call 的交错 arguments 分片，写出每一步 parser state。
- 将 401、429、504、网络断开和非法 JSON 分别映射成公开错误码与 retryable 值。
- 解释为什么不能在收到第一个 JSON 参数片段后立即执行工具。
- 解释为什么 OpenAI Responses API 应做成独立 Adapter，而不是塞进 Chat Completions
  Adapter 的条件分支。

**M1b 代码阅读顺序**

1. `providers/base.py`：先看 Provider-neutral 输入输出合同。
2. `providers/http.py`：理解资源、大小、超时和公开错误边界。
3. `providers/anthropic.py`：跟踪 content block 状态机。
4. `providers/openai_compatible.py`：跟踪稀疏 tool-call chunk 聚合。
5. `tests/integration/test_provider_contract.py`：验证同一 Agent Loop 的可替换性。

**Java/Flink 迁移类比**

| 现有经验 | Agent Provider 对应概念 |
|---|---|
| Java interface + Adapter | Python `Protocol` + Provider Adapter |
| Jackson DTO / Bean Validation | Pydantic wire/domain validation |
| WebClient streaming body | HTTPX async stream + SSE async iterator |
| Flink keyed state | 按 content/tool index 保存流解析状态 |
| watermark/terminal signal | `message_stop` 或 `[DONE]` |
| side output/error classification | 标准化 `ProviderErrorCode` |
| Exactly-once correlation key | ToolCall ID 与 ToolResult ID |

### L3：Tool Registry 与参数校验

**理论**

- 工具由 Schema、执行器和风险元数据组成。
- 模型提出调用不代表系统必须执行。
- 参数校验、权限判断和执行是三个独立阶段。

**Python**

- Pydantic、JSON Schema Draft 2020-12、`Protocol`。
- 结构化子类型让工具无需继承基类，Registry 只依赖 `definition/execute` 合同。
- `MappingProxyType` 与冻结 Pydantic 模型提供定义快照。

**工程**

- 构造时拒绝重复名称和无效 Schema，执行前校验 ToolCall arguments。
- Executor 异常、错误返回类型和 ID 不匹配统一为安全 ToolResult。
- Registry 只读取一次 definition，并限制所有成功 ToolResult 的总字符数。

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

- `pathlib.resolve(strict=True)`、`relative_to`、`lstat/stat/fstat`、符号链接和 junction。
- `stat.S_ISREG` 区分普通文件和设备/FIFO/socket。
- 纯函数式规则评估。

**工程**

- 防止 `..`、绝对路径、symlink/junction、盘符、UNC、ADS、Windows 设备名、
  尾随点/空格和 `.git` 绕过。
- 文件大小在 metadata 与实际 read 两处限制；严格 UTF-8，拒绝 NUL/二进制。
- 路径 containment 使用路径组件比较，不使用字符串前缀。
- allow/ask/deny 规则支持工具、路径、命令和会话范围。
- 非交互环境遇到 ask 时默认拒绝。
- 每次权限决定写入 Trace。

**验收练习**

- 路径穿越和符号链接负向测试。
- 解释 Workspace 检查为什么不是 OS sandbox，以及 TOCTOU 的剩余风险。
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
- `splitlines(keepends=True)` 保留文件原始换行；`casefold` 位置映射修正 Unicode 列号。

**工程**

- 限制 cwd、环境变量、执行时间和输出大小。
- 优先 argv；显式区分 Shell 与直接进程模式。
- 修改前后生成 diff。
- M2a Read/Search 拒绝越界，限制文件/字节/深度/结果/行长/preview。
- Search 只支持 literal，不接受模型正则，避免 ReDoS。
- 二进制、超大和敏感文件默认拒绝。
- M2b Write/Edit 使用 `read_file` 返回的原始字节 SHA-256 作为乐观锁。
- 新文件使用 create-only；已有文件在审批后再次校验哈希再原子替换。
- Edit 只允许唯一 literal 匹配，零匹配、多匹配和 no-op 都拒绝。
- 审批预览限制为相对路径、风险、理由和 bounded unified diff。
- M2c 只支持 argv，不支持 shell 字符串、`shell=True`、重定向或管道语法。
- Command 默认 deny；显式规则可按 executable glob 收窄，ask 仍需交互审批。
- 子进程使用最小环境、Workspace cwd、合并输出预算和进程树清理。
- 输出超限后继续丢弃式 drain，防止 pipe backpressure 阻塞终止。

**验收练习**

- 文件变化后旧 Patch 被拒绝，不静默覆盖。
- 超时命令及其子进程可终止。
- stdout、stderr、退出码和截断状态完整返回。
- 同一测试集通过 Windows 与 Linux。

**Java/Flink 迁移类比**

| 现有经验 | M2a/M2b/M2c 对应概念 |
|---|---|
| Java NIO `Path.normalize/toRealPath` | `Path.resolve(strict=True)` + `relative_to` |
| Bean Validation/Jackson Schema | Pydantic + JSON Schema ToolCall 边界 |
| Service registry/strategy map | `ToolRegistry` definition snapshot 与 dispatch |
| Flink keyed state | 按 ToolCall ID 保持调用和结果关联 |
| Flink state/throughput quota | 文件数、总字节、结果数和深度预算 |
| Source connector dirty input | 仓库文本、路径和模型参数全部不可信 |
| Spring interceptor / Security filter | `GovernedToolExecutor` 固定执行治理顺序 |
| JPA `@Version` | SHA-256 乐观并发前置条件 |
| Flink checkpoint state version | Read hash 标识 Edit 所依据的文件快照 |
| 两阶段发布 | 临时文件写完并校验后通过 `os.replace` 一次发布 |
| Java `ProcessBuilder(List)` | argv-only `asyncio.create_subprocess_exec` |
| `Future.cancel()` | 取消信号 + 显式进程树清理 |
| Flink task timeout | 命令 timeout 分类和有界终止 |
| 有界队列/背压 | 输出保留预算 + overflow 后 discard drain |

**M2b 代码阅读顺序**

1. `policy/models.py`：先理解决策、风险、资源、会话与信任源。
2. `policy/engine.py`：跟踪首匹配规则和安全默认值。
3. `policy/executor.py`：跟踪 Schema、Preview、Policy、Approval、Dispatch 顺序。
4. `workspace/boundary.py`：跟踪哈希前置条件、临时文件和原子发布。
5. `tools/write_file.py` 与 `tools/edit_file.py`：理解工具语义如何复用边界。
6. `tests/integration/test_governed_write_agent.py`：验证 Read -> Hash -> Edit -> Approval。

**M2c 代码阅读顺序**

1. `command/models.py`：理解请求、结果和资源预算。
2. `command/environment.py`：检查平台环境白名单和 Secret 排除。
3. `command/runner.py`：跟踪 process/output/timeout/cancellation 竞态。
4. `tools/run_command.py`：理解 Schema、cwd 与 critical preview。
5. `policy/engine.py`：理解 execute 默认 deny 和 executable glob。
6. `tests/unit/command/test_runner.py`：跟踪父子进程、overflow 与异常清理证据。

### L6：Context Budget 与压缩

**理论**

- 上下文管理首先是请求准入控制，其次才是语义压缩。
- System Prompt、Tool Schema、消息和预留输出共同占用预算。
- ToolCall 与对应 ToolResult 是关联原子单元，不能截断其中一半。
- 已完成副作用如果从上下文消失，模型可能用新 ID 重复执行，因此必须固定保留。
- 压缩是有损操作；静态省略标记只能证明发生过省略，不能恢复事实。

**Python**

- `Protocol` 定义可替换 estimator，Pydantic 冻结预算和窗口 DTO。
- canonical JSON、UTF-8 字节估算、SHA-256 指纹。
- 不可变原子单元、稳定顺序、确定性窗口选择。

**工程**

- 每次 Provider I/O 前统一估算，预留模型输出空间。
- 完整请求可容纳时不改写；超限时保留用户目标、最新单元和所有副作用/未知工具交换。
- 只有纯只读工具交换和普通消息可进入最近后缀淘汰。
- 省略标记只包含消息数、交换数和完整 transcript 指纹，不复制原文。
- 固定内容、最新单元或固定历史无法容纳时 fail closed，不盲目重试 Provider。
- M3a 不生成滚动摘要、不落盘工具输出、不提供 durable memory；这些需要后续 Trace。

**验收练习**

- 手算 609/610/611 三个边界预算，解释为什么保留的只读交换数不同。
- 构造“写操作 -> 旧只读 -> 最新只读”，证明压缩后写操作仍在原位置。
- 删除当前工具定义后重放同一 transcript，解释未知工具为什么必须按副作用固定。
- 对比完整 transcript、Provider 看到的窗口和 `AgentResult`，说明所有权差异。
- 解释 transcript SHA-256 能证明什么，以及为什么它不是 Secret 保护或 Checkpoint。

**Java/Flink 迁移类比**

| 现有经验 | M3a 对应概念 |
|---|---|
| JVM heap/request admission | Provider 调用前的 context preflight |
| Kafka/Flink 原子记录边界 | ToolCall + ToolResult 不可拆分单元 |
| Flink state retention | 按副作用分类固定与淘汰历史 |
| 有界队列/背压 | 超预算时先压缩或拒绝，不向 Provider 盲发 |
| Checkpoint metadata hash | transcript 指纹仅标识状态，不保存状态本身 |

**M3a 代码阅读顺序**

1. `context/models.py`：预算约束和不可变 `ContextWindow`。
2. `context/estimator.py`：Provider-neutral 的确定性估算合同。
3. `context/manager.py`：transcript 校验、原子分组、固定和最近后缀选择。
4. `agent/events.py`：有界 `ContextCompacted` 证据。
5. `agent/runtime.py`：Provider I/O 前的统一准入和静态失败映射。
6. `tests/unit/context/test_manager.py`：边界值、副作用固定和泄密负向测试。
7. `tests/integration/test_context_budget_agent.py`：完整 transcript 与 Provider 窗口分离。

### L7：Session、Checkpoint、Resume 与 Trace

**理论**

- Session 是逻辑任务容器，Run 是一次执行尝试，Trace 是追加式生命周期事实。
- Session/Run 表是事件日志的物化视图，不等于 Checkpoint。
- `event_id` 解决同一事件重试幂等，`(session_id, sequence)` 解决会话内顺序。
- `ToolStarted` 没有对应 `ToolCompleted` 表示结果不确定，不表示执行失败或可安全重试。
- Hash chain 能发现不一致，不等于签名审计或防篡改存储。
- Checkpoint 保存可恢复状态；Resume 还必须验证 Workspace、配置和 Schema 兼容性。

**Python**

- stdlib `sqlite3`、context manager、短连接和参数化 SQL。
- Pydantic 冻结 DTO、`TypeAdapter` 判别事件联合、`Protocol` Journal。
- canonical JSON、SHA-256 前驱链、UUID 幂等键。
- SQLite 事务、索引、WAL、foreign key、busy timeout 和 `PRAGMA user_version`。

**工程**

- M3b 用一个 `BEGIN IMMEDIATE` 同时追加 Trace 与更新 Session/Run projection。
- 每次 Provider 前写 `ModelStarted`，每次 Tool 执行前写 `ToolStarted`。
- Required Journal 失败立即停止；UI/日志 EventSink 继续 best-effort。
- 保存 Run 起止、停止原因、轮次、ToolCall 数量和累计 usage。
- Trace 不保存 prompt、arguments、ToolResult、diff 或命令输出。
- 配置 Secret 只在自由错误文本中按值替换；未知 Secret 无法自动识别。
- 查询、事件大小、Session 事件数和数据库锁等待都有硬上限。
- M3c 才保存消息/Checkpoint，并处理 started-only Tool 的恢复决策。

**验收练习**

- 画出成功一轮 ToolCall 的 8 个事件并说明每个写入时机。
- 在 `RunStopped` Trace insert 前注入 SQLite trigger，证明 projection 同事务回滚。
- 同一 event ID 重放两次，再修改 payload 重放，解释两个结果为什么不同。
- 删除中间 sequence、修改 previous hash、payload 和 Session head，分别运行完整性验证。
- 锁住数据库超过 busy timeout，验证 Agent 停止而不是无限重试。
- 对比 Session、Run、Trace 与 Checkpoint：指出哪些数据能查询、哪些数据可恢复。
- 解释为什么 started-only 写操作在 Resume 时不能自动重放。

#### M3c Checkpoint/Resume 实现笔记

- `CheckpointSaved` 与完整状态行在同一个 `BEGIN IMMEDIATE` 事务提交；这是“日志事实 +
  状态快照”的原子边界，不是把两个文件按顺序写完。
- 数据库 schema 从 v1 迁移到 v2，但 Trace envelope 保持 v1，因此历史 hash chain 不需要
  重写；类似 Flink 状态后端升级时区分元数据格式与业务序列化格式。
- 稳定点只出现在合法 Provider 输入边界：初始用户消息，或完整
  `Assistant ToolCall -> User ToolResult` 之后。中间态不能当作 Checkpoint。
- Resume 不只读取快照，还扫描快照 sequence 之后的 WAL 式事实。任何未纳入快照的
  write/execute/network Tool 都是不可自动重放的外部状态。
- Tool contract SHA-256 类似 JobGraph/serializer compatibility；Workspace SHA-256 类似
  输入状态版本。二者变化都要求停止，而不是猜测兼容。
- `ResumePlan` 是可序列化数据，不是权限凭证；claim 必须重新分析，防止调用方伪造 plan
  绕过副作用策略。
- claim 在一个事务内中断 source Run、启动 resumed Run、消费 Checkpoint；并发进程只有
  一个成功，类似数据库乐观锁加唯一状态迁移。
- Provider retry 是显式 at-least-once。没有目标系统 idempotency key 或事务时，Agent
  无法承诺外部副作用 exactly-once。
- Checkpoint 为恢复必须保存完整消息，因此会持久化 prompt、模型文本、工具参数/结果和
  命令输出；当前是有界明文，不等于 Secret scrubbing 或加密。

#### M3c 代码阅读练习

1. 从 Runtime 的初始 `_save_checkpoint` 跟到 SQLite 行和 `CheckpointSaved`，标出事务
   内外边界。
2. 在 Provider 返回 ToolCall 后、Tool 执行前、ToolCompleted 后分别模拟崩溃，判断最新
   Checkpoint 和增量 Trace 会让 Resume 得到什么结论。
3. 修改一个 Tool 的 `input_schema` 或 `side_effect`，解释为什么 fingerprint 必须变化。
4. 让两个线程 claim 同一个 plan，说明 `BEGIN IMMEDIATE`、Trace head 和 available 状态
   如何共同产生一个 winner。
5. 构造一个 Pydantic `ResumePlan` 试图绕过分析，定位 claim 重新分析的防线。
6. 对比 Flink Checkpoint：哪些是可类比的状态/版本/恢复概念，哪些分布式 barrier 和
   exactly-once 能力本项目没有。

**Java/Flink/Kafka 迁移类比**

| 现有经验 | M3b 对应概念 |
|---|---|
| Kafka partition log | 一个 Session 内按 sequence 追加的 Trace |
| Kafka producer idempotency key | 全局唯一 `event_id` |
| Flink keyed state | Session/Run projection |
| Flink checkpoint barrier | `ToolStarted` 只是动作边界，不是 checkpoint |
| JDBC transaction | Trace insert 与 projection update 原子提交 |
| 数据库 materialized view | Session/Run 表从事件生命周期维护 |
| Backpressure/timeout | SQLite busy timeout 后 fail closed |

**M3b 代码阅读顺序**

1. `agent/events.py`：事件 ID、started/completed 合同和 required `EventJournal`。
2. `persistence/models.py`：Session/Run/Trace DTO 与资源上限。
3. `persistence/schema.py`：SQLite v1 DDL 与连接 PRAGMA。
4. `persistence/codec.py`：canonical JSON、Secret scrub 和 event hash。
5. `persistence/journal.py`：事务、幂等、状态迁移和 projection。
6. `persistence/trace.py`：typed read 与分页完整性验证。
7. `agent/runtime.py`：required Journal 和 best-effort EventSink 的失败策略。
8. `tests/integration/test_persistent_trace_agent.py`：重开、真实写入阻断与篡改证据。

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
