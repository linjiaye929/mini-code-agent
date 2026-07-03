# 受治理测试执行

[English](governed-test-execution.md) | [简体中文](governed-test-execution.zh-CN.md)

## 目的

M4b 为 Agent Verification 增加一个窄范围测试能力，而不是再开放任意命令接口。宿主选择
Python Environment 和 Pytest Profile；模型只能请求已有 Workspace-relative Test File/
Directory，并说明为什么需要运行。

能力分为四层边界：

1. `RunTestsTool` 校验 Model Input 并准备 Approval Preview。
2. `GovernedToolExecutor` 应用 Execute Policy 和独立 Approval。
3. `PytestRunner` 使用 Process Budget 构造并执行固定 argv Profile。
4. JUnit Parser 将不可信有界 Report 转换成 Typed Diagnostic。

## 组合

```python
import sys
from pathlib import Path

from mini_code_agent.policy import (
    GovernedToolExecutor,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    SessionMode,
    StaticApprovalHandler,
    TrustSource,
)
from mini_code_agent.testing import PytestProfile, PytestRunner
from mini_code_agent.tools import RunTestsTool, ToolRegistry
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.workspace import WorkspaceBoundary

root = Path.cwd()
workspace = WorkspaceBoundary(root)
runner = PytestRunner(
    root,
    profile=PytestProfile(
        python_executable=Path(sys.executable),
        default_targets=("tests",),
        trusted_plugins=("pytest_asyncio.plugin",),
    ),
)
registry = ToolRegistry([RunTestsTool(workspace, runner)])
executor = GovernedToolExecutor(
    registry,
    policy=PolicyEngine(
        [
            PolicyRule(
                id="ask-project-tests",
                decision=PolicyDecision.ASK,
                rationale="Tests execute repository code.",
                tool_glob="run_tests",
                side_effect=SideEffect.EXECUTE,
            )
        ]
    ),
    approval=StaticApprovalHandler(approved=True),
    session_mode=SessionMode.INTERACTIVE,
    trust_source=TrustSource.MODEL,
)
```

使用当前 Environment 的 `sys.executable` Path，但不要 Resolve Symlink。POSIX 下 Resolve
`.venv/bin/python` 可能丢失 Virtual Environment Identity 并选择 Base Interpreter。
该 Path 是 Host Configuration，绝不是 ToolCall Argument。

## 模型与宿主控制

| 值 | Owner | 边界 |
|---|---|---|
| Python Executable | Host | Immutable `PytestProfile` 中的 Absolute Path |
| Default Target | Host | 再次通过 `WorkspaceBoundary` 校验 |
| Trusted Pytest Plugin | Host | 最多 10 个 Import Module Name，固定 `-p` |
| Timeout 与 `--maxfail` | Host | Immutable Profile + Hard `PytestLimits` |
| Test Target | Model | 最多 32 个已有 Workspace File/Directory |
| Reason | Model | 必填的有界 Approval Text |
| cwd | Harness | 永远是 Resolved Workspace Root |
| JUnit Path | Harness | 随机 Host Temporary File |
| 其他 argv 与 Environment | Harness | 固定，模型不可控制 |

省略 `targets` 时选择 Host Default；显式空 List 非法。如果 Host Default 为空，Pytest
在 Workspace Root 正常 Discovery。

## 固定命令

生成 argv 等价于：

```text
<host-python> -I -B -m pytest
  -q --disable-warnings --maxfail=<host-value>
  -p no:cacheprovider
  [-p <host-trusted-plugin>]...
  --junitxml=<managed-temporary-path>
  -- [validated-target]...
```

- `create_subprocess_exec` 保持 argv Boundary，永不调用 Shell。
- `-I` 忽略 User-site 和 `PYTHON*` Startup Influence。
- `-B` 阻止 Bytecode Cache Write 弄脏原本只读的 Test Run。
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 移除环境 Entry-point Plugin。
- `-p no:cacheprovider` 阻止 Harness 创建 `.pytest_cache`。
- `--` 在模型选择的 Target 前终止 Option Parsing。

Project Test 和 `conftest.py` 仍然会执行，并可能写文件。Trusted Plugin 是 Host
Configuration；如果允许模型指定 Plugin，就等于给模型一个 Import Execution Primitive。

## Target 校验

Tool 接受 File 与 Directory。每个 Target：

- 使用 POSIX 风格 Workspace-relative Syntax；
- 拒绝 Absolute、Drive、UNC、ADS、`.git`、`%`、Backslash、Node ID `::` 和 Leading Dash；
- 通过 `WorkspaceBoundary` 拒绝 Link/Junction；
- 必须是已存在的 Directory 或 Regular File；
- 归一化为 Workspace-relative Display Path；
- 按 Resolved Platform Path Identity 去重。

Preview 和 Execute 会运行同一 Preparation。Approval 后被删除或替换的 Target 会失败，
不会静默改变命令。

## Process 与 Report 状态

Process Status：

| 状态 | 含义 |
|---|---|
| `passed` | Pytest Exit 0 |
| `failed` | Exit 1 |
| `interrupted` | Exit 2 |
| `internal_error` | Exit 3 |
| `usage_error` | Exit 4 |
| `no_tests` | Exit 5 |
| `timed_out` | 超过 Host Timeout |
| `output_limit_exceeded` | stdout/stderr 超过预算 |
| `unknown_exit` | 其他 Exit 或没有更强分类 |

Report Status：

| 状态 | 含义 |
|---|---|
| `complete` | 有界 Report 解析成功 |
| `missing` | 没有 Report |
| `invalid` | Encoding、XML、Field 或 Outcome Structure 非法 |
| `unsafe` | File Type 或 DTD/Entity 被拒绝 |
| `too_large` | 超过 Byte 或 Test-case Budget |

损坏 Report 不会抹掉 Process Exit、stdout、stderr 或 Duration，而是返回空 Report Count/
Diagnostic 和非 Complete Status。Managed Report 的 Full Path、POSIX Path 与 Random Filename
会在序列化前替换为 `<managed-junit-report.xml>`。

## JUnit 信任边界

Repository Test Code 与 Pytest 同处一个 Process Tree，可能篡改 JUnit File，因此 Parser
将其视为攻击者控制：

1. 在平台支持时，不跟随 Link 打开一个精确 Host-created Path；
2. 要求 Regular File；
3. 最多读取 `max_report_bytes + 1`；
4. 严格 UTF-8 Decode；
5. 拒绝 DTD 和 Entity Declaration；
6. 只解析 `testsuite` 或 `testsuites`；
7. 从有界 `testcase` Element 计算 Count；
8. 拒绝 Contradictory Outcome 和 Invalid Attribute；
9. 确定性截断返回 Diagnostic Count 与 Text。

Aggregate XML Attribute 是冗余的不可信 Claim，因此被忽略。Count 来自 Parser 接受的实际
Case Element。

## 清理与取消

Random Report File 在 Process Launch 前创建并关闭，使 Windows 可以重新打开。
`try/finally` 会在成功、Command Failure、Parser Failure、Timeout、Output Overflow 或
Cancellation 后删除该精确 Path，绝不递归删除由 Test Code 控制的路径。

`CommandRunner` 负责 Process-tree Cleanup。Cancellation 只会在其 Shielded Cleanup 后传播，
随后外层 `finally` 清理 Report。

Path Replacement 可以阻止普通 Pytest Output 或 `sys.argv` 回显 Random Temporary Name，
但任意 Test Code 仍可转换、编码、拆分或用其他方式外传数据。防止此类行为需要 Process
Isolation，而不是 Output Substitution。

## 安全边界

Approval 只回答动作是否可以运行，不提供隔离。批准的 Test 以 Mini CodeAgent OS User
身份运行任意 Project Code，可以读写该用户可访问路径、启动 Child Process、访问允许的
网络资源、通过其他 OS Channel 读取凭证，并修改 Workspace。

Timeout、Output Limit、Plugin Control、Minimal Environment、Path Validation 和 Process
Cleanup 只能降低意外执行与资源滥用，不能替代 Container、受限 OS Account、VM、seccomp/
Job-object Policy 或 Network Sandbox。

Test stdout/stderr/Diagnostic 可能含源码或 Secret，会返回当前 Model Exchange，但不会进入
Lifecycle Trace Event。Checkpoint Payload 是有界明文，可能包含完整 ToolResult。
