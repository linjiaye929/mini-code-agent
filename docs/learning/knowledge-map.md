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

#### M4a 只读 Git 实现补充

- 把 porcelain v2 当作 wire protocol：固定版本、机器字段和 NUL framing，不能按人类
  输出或当前 locale 猜测。
- Git index 与 working tree 是两个状态层；XY 的第一个字符描述 index，第二个描述
  working tree。
- status/diff 的“只读”是业务语义，不代表进程不会执行配置扩展或写 optional lock；
  安全边界必须由固定 argv 和负向测试证明。
- `rev-parse --show-toplevel` 是仓库 ownership 检查。Workspace 子目录不能隐式继承父
  仓库权限。
- canonical status SHA-256 类似 Flink offset 快照：标识观察到的状态，但不能阻止后续
  并发变化。
- Java 类比：porcelain parser 类似解析稳定 DTO 协议；`--` 类似 prepared statement
  把参数和控制语法分离；GitErrorCode 类似服务边界统一异常码。

#### M4b 受治理 Pytest 与结构化诊断

**前置知识**

- Pytest 发现规则、exit code 0-5、fixture/`conftest.py` 与插件加载。
- `asyncio.subprocess`、argv 与 shell string 的差异、进程树取消。
- XML 的元素/属性/编码，以及 DTD/实体为什么属于不可信输入风险。
- Pydantic frozen model、`Protocol`、`StrEnum` 和跨字段不变量。
- Policy 的 allow/ask/deny、交互/非交互模式及审批与执行隔离。

**本项目用到的知识**

- 固定 `PytestProfile`：解释器、timeout、`maxfail`、默认 target 和可信插件均由宿主
  配置，模型不能构造命令。
- `python -I -B` 隔离用户 site 与 `PYTHON*` 启动影响，并避免 Harness 测试运行写入
  `__pycache__`；
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 禁止环境中的 entry-point 插件自动扩展执行面。
- POSIX 的 `.venv/bin/python` 可能是 symlink；不能对 `sys.executable` 调用
  `resolve()`，否则会丢失 venv identity，`-I` 下会落到不含 Pytest 的 base Python。
- `-p no:cacheprovider` 避免 Harness 自己创建 `.pytest_cache`；不代表测试代码只读。
- model target 经 `WorkspaceBoundary` 解析为现存文件/目录，并在 `--` 后传给 Pytest。
- 进程状态和报告状态拆成两个 failure domain：测试 exit code 仍可保留，即使 JUnit
  缺失、非法、不安全或过大。
- JUnit 先做字节上限、普通文件、严格 UTF-8、DTD/实体拒绝，再用 `defusedxml` 解析；
  计数从 `testcase` 重新计算，不相信 XML 汇总属性。
- diagnostics 对用例数、条数、message 和 traceback 分别设限；报告临时路径在取消和
  异常路径都清理。
- stdout/stderr/diagnostics 会进入当前模型上下文，但生命周期 Trace 只记录
  `ToolStarted/ToolCompleted`，不落测试正文。

**Java/Flink/Spark SQL 映射**

- 固定 Pytest profile 类似服务端 prepared statement：客户端只能绑定参数，不能改
  SQL/命令结构。
- JUnit XML 类似版本化 Kafka/Flink 事件协议；终端输出更像日志，不应靠正则当 DTO。
- process status 与 report status 类似 Flink Job 状态和 Checkpoint 元数据状态：一个
  成功或失败不能推导另一个。
- timeout、输出字节、报告字节、case 数和 diagnostics 数类似多维 backpressure/
  quota；只限制一项仍可能在其他维度耗尽资源。
- Policy approval 类似鉴权，OS sandbox 类似运行时隔离；有权限执行不等于进程只能
  访问 Workspace。

**阅读顺序**

1. `testing/models.py`：先看不可变预算、状态和结果一致性。
2. `testing/junit.py`：跟踪不可信文件从 byte limit 到 typed diagnostic。
3. `testing/pytest_runner.py`：看固定 argv、最小环境、状态分类和 `finally` 清理。
4. `tools/run_tests.py`：看模型参数如何变成 Workspace target 和 ActionPreview。
5. `tests/integration/test_governed_pytest_agent.py`：看 deny/ask、真实 Pytest、Agent
   ToolResult 和 SQLite Trace 的完整边界。

**练习**

1. 分别制造 exit 1、2、3、4、5，解释为什么不能都叫“测试失败”。
2. 修改 JUnit 的汇总 `tests="999"`，验证解析结果为什么仍由 `testcase` 决定。
3. 写一个带 DTD 的 20 字节报告和一个超过字节上限的 DTD 报告，解释检查顺序。
4. 在未配置 `pytest_asyncio.plugin` 时运行 async test，再作为 trusted plugin 配置，
   比较显式依赖与 ambient plugin 的差异。
5. 让测试打印一个 marker，检查 ToolResult 与 SQLite Trace，说明数据保存边界。
6. 解释为什么 `-p no:cacheprovider` 不能支持“测试执行不修改工作区”的结论。

#### M4c 宿主控制的有限 Repair

**前置知识**

- 反馈控制循环：输入是结构化失败证据，执行器产生一次受限修改，宿主重新测量并决定
  继续或停止。
- Git tracked、ignored、untracked、index、working tree 的差异；“status 干净”不等于
  指定文件已被 Git 跟踪。
- Git pathspec magic，尤其是 `:(top,literal)` 如何把路径当数据而不是匹配语法。
- Python `Protocol`、不可变 Pydantic model、`asyncio` 取消传播和单调时钟预算。
- canonical JSON、SHA-256、SQLite 事务、materialized projection 与 hash chain。

**本项目用到的知识**

- `RepairRuntime` 是 `AgentRuntime` 外层的宿主状态机。模型只负责一次修复尝试，不能
  决定测试命令、成功标准、重试次数或终止条件。
- Admission 先要求显式 Repair approval，再验证三个根目录一致、仓库完全干净、目标是
  现存普通文件，并用 `git ls-files --error-unmatch -z -- :(top,literal)...` 验证 exact
  tracked set。
- `RepairActionGuard` 位于普通 Policy 前：只读动作继续进入 Policy；write resource 必须
  精确等于 scope；execute/network 直接拒绝。它是额外约束，不替代 Schema、Policy 和审批。
- baseline 与每轮验证都使用相同宿主固定 target。只有 process=`passed` 且
  report=`complete` 才算成功，模型文本不能声明“已修复”。
- Worker 返回后，宿主验证 branch 未变、staged diff 为空、状态只有 scope 内普通 unstaged
  `M`，patch 非空且有进展，并在测试前后再次核对 Git 指纹和 Workspace file identity。
- 失败指纹只纳入稳定的 status/count/diagnostic 字段，排除 stdout、stderr、duration 和
  details；默认第二次出现同一失败就停止，避免无进展循环。
- attempts、elapsed time、full patch bytes、same-failure appearances 和 Worker prompt
  characters 是独立预算；任一超限都有 typed stop reason。
- SQLite schema v3 用独立 `repair_runs`/`repair_events` 保存生命周期 hash chain，不保存
  prompt、patch 或 diagnostics。started-only Repair 不能自动 Resume，因为可能已经写盘。

**Java/Flink/Spark SQL 映射**

- `RepairRuntime` 类似 Flink JobManager 控制重启策略：Task/模型只执行一次工作，是否重试
  由控制平面依据确定性状态和预算决定。
- `RepairActionGuard -> Policy -> Approval -> Tool` 类似服务端多层拦截器；前一层拒绝后，
  后续鉴权和业务执行都不应发生。
- baseline/failure fingerprint 类似 Checkpoint state hash：它用于比较观察结果，不是锁，
  也不能阻止并发写入。
- exact tracked scope 类似 Spark SQL 写入前固定目标分区集合；仅检查“当前无脏数据”不能
  证明目标属于受治理数据集。
- `repair_runs` projection 加 append-only events 类似 CQRS 的物化视图与事件日志；必须在
  同一事务更新，否则终态和事件链可能分裂。

**阅读顺序**

1. `repair/models.py` 与 `repair/fingerprint.py`：先看预算、不变量和稳定失败身份。
2. `repair/scope.py` 与 `policy/executor.py`：看 pre-policy exact-scope guard。
3. `git/client.py`：看 literal tracked-path query 和 exact output-set 校验。
4. `repair/worker.py`：看一次 Agent run 如何被适配成一次 attempt。
5. `repair/runtime.py`：按 admission、baseline、attempt、verification、terminal 顺序跟踪。
6. `persistence/repair.py`：看 Repair event/projection 的事务和完整性验证。
7. `tests/integration/test_bounded_repair_agent.py`：看真实成功、越权、dirty repo 和测试
   mutation 四条端到端路径。

**练习**

1. 解释 ignored 文件为什么可能让 `git status` 干净，却不能成为 Repair editable path。
2. 让 Worker 尝试修改 scope 外文件，确认 denial 发生在 approval handler 和磁盘写入前。
3. 构造 stdout 每次变化但 diagnostics 相同的失败，说明 canonical fingerprint 为何稳定。
4. 让测试创建并删除临时文件，说明最终状态核对为何仍不是 OS isolation。
5. 中断一个 started-only Repair，说明为什么“重新运行同一 prompt”可能重复副作用。
6. 比较“自动 reset 失败 patch”和“保留工作树供检查”的数据安全取舍。

### L9：Skills 与 Hooks

**前置知识**

- YAML frontmatter 是数据协议，不是配置即代码；要理解 duplicate key、alias、custom tag
  和 `SafeLoader` 的边界。
- `lstat/stat/fstat`、symlink、Windows junction/reparse point、device/inode 与
  time-of-check/time-of-use（TOCTOU）。
- SHA-256 只能证明“本次字节与先前观察一致”，不能证明来源可信。
- Pydantic frozen model、`Protocol`、`dataclass(frozen=True)`、async timeout 与
  `CancelledError` 传播。
- 单调授权：扩展可以缩小权限，但不能把宿主的 deny/ask 提升为 allow。

**本项目用到的知识**

- `SkillRoot` 由宿主显式配置；发现只扫描一层目录，并对 root/child/`SKILL.md` 做
  regular-file 与 reparse 检查。
- restricted PyYAML 拒绝 duplicate key、alias、custom tag、非字符串 key；Pydantic 再
  拒绝未知字段、非法名称和 SemVer。
- Skill ID 固定为 `source:name`。managed/user/project 同名共存，同一 source 冲突全部
  quarantine，不使用隐式覆盖顺序。
- `list_skills` 只返回 descriptor；`load_skill` 必须携带发现时 SHA，并重验路径、打开句柄
  identity、metadata、byte count 与 hash。
- Skill 正文标记为 `untrusted_markdown`，不自动进入 system prompt，也不能注册 Tool、
  Hook、Provider 或 Policy。
- `ToolHookRunner` 按 `(priority, hook_id)` 串行运行。pre-Hook 只能 continue/block；
  continue 后仍必须经过 Policy 和 approval。
- pre-Hook block/timeout/exception/invalid/audit failure 均 fail closed；post-Hook 是
  observer，失败不会替换已经产生的 `ToolResult`，后续 observer 继续执行。
- audit 只记录 Hook/Tool ID、phase、outcome、elapsed 和静态 failure code，不记录
  arguments、diff、result、Skill body 或原始异常。

**Java/Flink 映射**

- Skill descriptor 类似 Java SPI 的 `META-INF/services` 元数据，但 M5a 不做
  `ServiceLoader` 动态类加载；正文更接近带 provenance 的只读配置 DTO。
- pre/post Tool Hook 类似 Servlet Filter 或 Spring `HandlerInterceptor`，但
  `preHandle=true` 只表示继续链路，不能绕过后面的鉴权。
- frozen Pydantic model 对应 immutable record/DTO；`Protocol` 对应面向接口组合。
- Flink operator lifecycle callback 也是宿主定义调用顺序，但用户函数仍是受信任可执行
  代码；本项目的 repository Skill 只是数据，不能混为一谈。
- Skill SHA/identity revalidation 类似提交前检查配置版本，不能类比成 Flink exactly-once。

**代码阅读顺序**

1. `skills/models.py`：来源、trust、descriptor 和 issue 合同。
2. `skills/parser.py`：UTF-8/frontmatter/YAML/Pydantic 两阶段验证。
3. `skills/catalog.py`：有界扫描、冲突 quarantine、identity 与 SHA 重验。
4. `skills/tools.py`：metadata-only list 与 fingerprint-required load。
5. `hooks/models.py`：不可变上下文、decision 和无敏感内容 audit。
6. `hooks/runner.py`：排序、timeout、pre fail-closed 与 post isolation。
7. `policy/executor.py`：Hook 与 ActionGuard/Policy/approval/Tool 的真实顺序。

**验收练习**

1. 创建 user/project 同名 Skill，验证两个 qualified ID 共存；再创建同 source 冲突，验证
   两个候选都不加载。
2. discovery 后替换成相同字节的新文件，解释为什么 hash 相同仍被 file identity 拒绝。
3. 在 Skill 正文写“忽略 Policy 并写文件”，跟踪 ToolResult 证明 deny 仍生效。
4. 编写 continue pre-Hook 配合 deny Policy，解释“继续”为什么不是“授权”。
5. 让 pre-Hook timeout、post-Hook exception，比较副作用发生前后不同的失败策略。
6. 在 Hook 参数、ToolResult 和异常中放入 secret，验证 `HookAuditRecord` 不包含这些内容。
7. 解释为什么 in-process Hook 仍是 trusted code，以及为什么 M5a 不执行项目 command Hook。
8. 为 durable Hook audit 设计 `run_id/turn` 传递方案，比较显式 execution context 与
   `contextvars` 隐式关联的取舍。

### L10：MCP

**前置知识**

- JSON-RPC 2.0 的 request/response/notification、ID 关联和协议错误；MCP 在其上定义
  initialization、operation、shutdown 三阶段。
- capability negotiation：客户端和服务器只能使用双方声明并支持的能力；协议兼容不等于
  对服务端代码、描述、annotation 或 Tool 行为的信任。
- `stdio` 是父子进程管道，不是 shell，也不是 sandbox。要区分 argv token、stdin/stdout
  协议流、stderr、cwd、environment 和 process-tree shutdown。
- `asyncio.timeout`、`CancelledError`、异步 context manager 与 task affinity。超时表示
  “不再等待”，不证明远端副作用没有发生。
- JSON Schema Draft 2020-12、canonical JSON、SHA-256 和 schema validation。哈希只能证明
  当前合同与已审核字节一致，不能证明实现安全。
- Prompt Injection 不只来自 Prompt：Tool description、server instructions、annotation、
  result text 和 structured content 都是不可信 wire data。

**本项目用到的知识**

- 首版固定官方稳定 SDK `mcp>=1.28.1,<2`、协议 `2025-11-25` 和 direct local stdio，只支持
  Tools；HTTP/OAuth/Resources/Prompts/Roots/Sampling/Elicitation/Tasks 均不开放。
- `McpServerProfile` 由宿主创建，固定 absolute executable、argv、cwd、SecretStr
  environment、server identity、Tool grants 与所有资源上限；启动前再次校验 command/cwd。
- connection approval 在 process open 前展示完整 command/cwd 和环境变量名称；值不展示。
  这与单次 Tool approval 是两个不同授权。
- initialization 必须匹配 protocol、server name/version、Tools capability，并拒绝
  `tools.listChanged`。
- `tools/list` 只接受一页，远端 Tool 名称集合必须与 host grants 完全相等；extra/missing/
  duplicate/pagination/task-required/schema drift 任一出现都不注册任何 Tool。
- `McpToolGrant` 绑定 remote name、local alias、host description、SideEffect、RiskLevel 和
  input/output schema SHA-256；服务端 description/title/annotation/instructions 不提供权限。
- production SDK adapter 用 owner worker 持有 stdio/ClientSession context，解决 AnyIO
  context 必须在进入 Task 退出的问题，外部 Task 通过 proxy 使用和关闭。
- 本地 alias 实现普通 `RegisteredTool`，调用继续经过 Schema -> ActionPreview -> Hook ->
  Policy -> approval -> Tool；per-tool provenance 固定为 `TrustSource.EXTENSION`。
- 结果只接受 bounded text 和 object-shaped structured JSON。图片、音频、resource、
  `_meta`、超限、非有限数值或 output-schema mismatch 整体失败，不返回截断成功。
- 调用串行且零重试。read-only timeout 返回 timeout；write/execute/network timeout 返回
  completion unknown，因为取消和 kill 不能回滚已发生副作用。

**Java/Flink/Spark 映射**

| 既有经验 | MCP 对应概念 | 关键差异 |
|---|---|---|
| Java RPC client/stub | `ClientSession` + Tool proxy | MCP Tool 由模型选择，必须再经过本地 Policy |
| API Gateway allowlist | host-pinned Tool grants | grant 同时钉住名称、schema、side effect 和 risk |
| Java SPI/ServiceLoader | `tools/list` discovery | 远端 metadata 不会动态加载本地代码，也不能自动获权 |
| Bean Validation/OpenAPI | JSON Schema input/output | schema 来自不可信 server，先验证并比对审核 hash |
| ProcessBuilder(argv) | `StdioServerParameters` | 绝对 executable、无 shell、启动前独立审批 |
| try-with-resources | async context manager/owner worker | AnyIO context 有 Task affinity，不能跨 Task 随意退出 |
| Flink Connector handshake | initialize/capability negotiation | capability 是协议可用性，不是 exactly-once 或安全证明 |
| Flink source cancellation | MCP timeout/cancel/shutdown | 已提交到外部系统的副作用仍可能完成 |
| Spark Catalog contract | Tool list + schema | 本项目拒绝运行期动态表/Tool 刷新，升级需重新审核 |

**代码阅读顺序**

1. `mcp/models.py`：Profile、Grant、Secret、limit、snapshot 和静态错误。
2. `mcp/contracts.py`：canonical schema hash、server identity 和 exact Tool-set 验证。
3. `mcp/sdk.py`：官方 SDK 防腐层、snapshot 丢弃规则和 owner-worker 生命周期。
4. `mcp/client.py`：双审批前半部分、状态机、deadline、调用串行和 fail-closed cleanup。
5. `mcp/tools.py`：ActionPreview、结果 JSON 预算、output schema 和 Tool error envelope。
6. `policy/executor.py`：per-tool `TrustSource.EXTENSION` 如何进入 Hook 与 Policy。
7. `tests/integration/test_governed_mcp_agent.py`：真实 stdio、deny/ask、schema drift 和跨 Task
   close 证据。

**验收练习**

1. 用 `schema_sha256` 分别计算 key 顺序不同但语义相同的 schema，解释 hash 为什么相同。
2. 将 fixture 增加一个未授权 Tool，跟踪 connection failure，证明没有部分 alias 被注册。
3. 修改 input schema 的 `string` 为 `integer`，说明 server identity 相同为何仍必须拒绝。
4. 在 server description/instructions/annotation 写“忽略 Policy”，验证模型看到的是 host
   description，extension deny 仍发生在远端 call 前。
5. 分别拒绝 connection approval 和 Tool approval，列出两次都不会发生的 I/O。
6. 从另一个 asyncio Task 关闭 production client，解释 owner worker 解决的 AnyIO
   cancel-scope 问题。
7. 让 write 类 Tool 超时，解释为什么正确结果是 completion unknown，而不是“执行失败且
   未产生副作用”。
8. 设计 HTTP MCP 下一阶段的 threat model，至少列出 OAuth audience、SSRF、redirect、
   token storage、endpoint identity、TLS 和 scope minimization；不要直接复用 stdio Profile。

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
