# 受治理命令执行

[English](governed-command-execution.md) | [简体中文](governed-command-execution.zh-CN.md)

## 数据流

```text
Model ToolCall
    |
    v
ToolRegistry
    |-- Draft 2020-12 argv/cwd/timeout/reason validation
    v
RunCommandTool.preview
    |-- WorkspaceBoundary resolves cwd
    |-- critical risk + exact argv + reason
    v
PolicyEngine
    |-- execute default: deny
    |-- optional executable_glob narrowing
    `-- explicit interactive ask/allow rule required
    v
CommandRunner
    |-- create_subprocess_exec (never shell=True)
    |-- stdin DEVNULL + minimal environment
    |-- concurrent bounded stdout/stderr
    `-- process-tree cleanup on limit/timeout/cancellation
    v
Correlated structured ToolResult
```

## 命令 Contract

`run_command` 接受一个 argv Array、Workspace-relative cwd、1 到 300 秒 Timeout 和有界
Reason。它不接受 Shell Text、stdin、Environment Override、Background Mode 或 Detached
Execution。

提交的精确 argv 会展示在 Approval 中。`ActionPreview` 将执行标为 Critical。Execute
默认拒绝，应用必须安装显式 Rule。Rule 可以按 Tool、Risk、cwd Resource、Session/Trust
Source 和 `executable_glob` 收窄。Executable Glob 匹配提交的第一个 argv Element，
而不是可执行文件的密码学 Identity。

非零 Exit Code 是正常 Command Result。Spawn、Output I/O 和 Cleanup Failure 使用静态
Error Code。Timeout 与 Output Limit Termination 作为 Result Flag 返回，使 Agent 可以
改用更小命令，而不需要解析 Exception Text。

## 限制

| 限制 | 默认值 | 硬上限 |
|---|---:|---:|
| Tool 请求 Timeout | 30 秒 | 300 秒 |
| Runner Timeout Policy | 300 秒 | 3,600 秒 |
| 保留的 stdout/stderr 总量 | 1 MiB | 8 MiB |
| Argv Item | 64 | 64 |
| 每个 Argument 字符数 | 4,096 | 4,096 |
| Cleanup Wait | 5 秒 | 10 秒 |

超过保留字节预算后，Stream Reader 继续读取并丢弃。这既避免 Pipe Backpressure 导致进程
终止死锁，也不让内存随 Command Output 无限增长。

## 环境

Child 只继承平台启动白名单：PATH、Temporary Directory、Locale、Home 和必需的 Windows
System Variable。API Key 和任意 Project Variable 不继承。显式空 Environment 保持为空；
只有 `None` 才表示从宿主派生最小 Environment。

最小化 Environment 能降低意外 Secret Inheritance，但不能阻止已批准进程打开当前 OS
用户可以访问的 Secret File。

## 进程生命周期

- POSIX 创建新 Session，向 Process Group 发送 SIGTERM，有界等待后升级到 SIGKILL。
- Windows 创建无可见 Console 的新 Process Group，并调用绝对路径
  `%SystemRoot%\System32\taskkill.exe /T /F`。
- Output Overflow 和 Timeout 在返回前终止 Process Tree。
- Cancellation 会 Shield 有界 Cleanup，随后重新抛出 `CancelledError`。
- 意外 Pipe Reader Failure 会终止 Process Tree，并返回 `command_io_failed`。
- Tree Cleanup 失败时仍尝试 Kill Root Process，但返回 `command_cleanup_failed`；
  Partial Cleanup 永远不会报告为成功。

测试使用一个启动 Heartbeat Grandchild 的 Parent Python Process。Runner 返回后 Heartbeat
必须停止变化，以此验证生命周期 Postcondition，而不是依赖容易受 PID Reuse 影响的检查。

## 安全非承诺

Runner 不是沙箱。批准的进程可以读写 Workspace 外部、访问网络、检查 Host State、打印
敏感文件内容，或故意使 Descendant 脱离。Process Group 和 `taskkill` 是 Best-effort
生命周期控制，不是 Security Principal。

Regex 或 Glob 过滤不能使任意命令执行变得安全。强隔离需要单独 Backend，例如 Container、
受限 OS Identity/Token、Namespace、AppContainer、seccomp 或同类平台控制。

## Java 与 Flink 类比

| 现有经验 | Command Runner 概念 |
|---|---|
| `ProcessBuilder(List<String>)` | 仅 argv 的 `create_subprocess_exec` |
| `CompletableFuture.cancel()` | Coroutine Cancellation + 显式 OS Process Cleanup |
| Executor Timeout | Monotonic Timeout + Bounded Termination |
| Bounded Queue/Backpressure | Retained-byte Budget + Discard Drain |
| Flink Task Failure Classification | Exit Code、Timeout 与 Infrastructure Error |
| Task Slot Lifecycle | Process Group Lifecycle，但没有 Security Isolation |
