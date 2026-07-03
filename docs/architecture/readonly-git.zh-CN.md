# 加固的只读 Git 证据

[English](readonly-git.md) | [简体中文](readonly-git.zh-CN.md)

## 目的

M4a 允许 Agent 检查仓库状态，但不给予 Git Mutation 权限。`git_status` 返回强类型
Porcelain-v2 Snapshot；`git_diff` 返回有界 Staged 或 Unstaged Patch。

## 仓库边界

`GitClient` 解析配置的 Workspace，并通过以下命令校验：

```text
git rev-parse --show-toplevel --is-bare-repository
```

Git 报告的 Top-level 必须等于 Workspace Root。父仓库中的嵌套目录会被拒绝，否则
Status/Diff 可能暴露配置边界外的路径。Bare Repository 和 Non-repository 也会被拒绝。
Linked Git Worktree 只要自身 Top-level 与 Workspace 一致即可使用。

## 进程加固

命令只使用 argv，并通过已有的有界 `CommandRunner` 执行。每次调用包含：

```text
git --no-pager --no-optional-locks
    -c core.fsmonitor=false
    -c diff.external=
    -C <workspace>
```

Status 忽略 Submodule Recursion。Diff 增加 `--no-ext-diff`、`--no-textconv` 和末尾 `--`。
这些控制阻止被检查仓库为上述操作启用已知 Git Execution Extension。真实测试配置恶意
fsmonitor 和 external-diff Command，并证明二者都不会执行。

`--no-optional-locks` 还阻止仅为 Refresh 产生的 Index Write。测试比较 Status/Diff
前后的 `.git/index` Byte 和 Nanosecond Modification Time。

## Status 协议

Client 请求：

```text
status --porcelain=v2 -z --branch --untracked-files=all --ignore-submodules=all
```

Parser 消费 NUL Record，并支持：

- 普通 Tracked Entry；
- 带第二个 Original-path Field 的 Rename/Copy Entry；
- Unmerged Entry；
- Untracked Entry。

Branch OID、Head、可选 Upstream、Ahead/Behind Count、XY Status、Submodule State、Current
Path 和 Original Path 都会校验。Path 中的 Space、Tab、Newline 和 Leading Dash 是数据，
不是 Option。Unknown Record、Malformed Metadata、Replacement-decoded Text 和 Entry
Overflow 都失败关闭。

结果是带 SHA-256 的 Canonical Typed JSON。它只是时间点观察，可能立刻变旧。

## Diff 协议

`git_diff` 接受一个严格 Boolean：`staged`。执行：

```text
diff --no-ext-diff --no-textconv --ignore-submodules=all
     --unified=3 [--cached] --
```

Combined Process Output 默认预算 2 MiB。Character Overflow 也会失败；Partial Patch
绝不作为完整证据返回。结果包含 Mode、Patch、Byte/Character Count 和 SHA-256。

## 失败 Contract

Git Startup、Timeout、Overflow、Repository Mismatch、Bare Repository、Non-zero Exit
和 Malformed Output 映射为稳定 `GitErrorCode`。公开错误不包含 Command stderr、Absolute
Path、Repository Content 或 Raw Exception。

## 非承诺

- 这不是 OS 沙箱。
- Git Output 可能向模型暴露源码和 Secret。
- Status/Diff 不会锁住 Working Tree，无法阻止并发修改。
- SHA-256 只标识返回证据，不是 Signature。
- M4a 不执行 Stage、Commit、Reset、Clean、Checkout、Fetch、Push 或 Repair。
- 禁用已知 Extension Point 不代表未来任意 Git Command 默认安全。
