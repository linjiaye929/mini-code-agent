# Mini CodeAgent Chinese Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为中文读者提供完整的项目入口和核心架构文档，同时保留英文文档与双向语言导航。

**Architecture:** 英文原文件继续作为英文事实来源；中文文件使用 `.zh-CN.md` 后缀并保持相同章节结构。根 README 提供语言切换，中文 README 链接中文架构文档和现有中文学习/简历资料。

**Tech Stack:** Markdown、PowerShell、Git、GitHub CLI

---

### Task 1: 中文 README 与语言入口

**Files:**
- Create: `README.zh-CN.md`
- Modify: `README.md`

- [ ] **Step 1: 在英文 README 标题下添加语言切换**

添加：

```markdown
[English](README.md) | [简体中文](README.zh-CN.md)
```

- [ ] **Step 2: 创建完整中文 README**

按英文 README 当前顺序翻译 Requirements、Development、Configuration、SiliconFlow、
Provider、Workspace、Policy、Context、Session、Checkpoint、Git、Pytest、Repair、
Skills/Hooks、MCP、Subagent、Worktree、Documentation 和 License。代码块、配置键、类名、
安全边界及未实现项保持原义。

- [ ] **Step 3: 校验 README 本地链接**

Run:

```powershell
@('README.md','README.zh-CN.md') | ForEach-Object {
  Select-String -Path $_ -Pattern '\]\(([^)#]+\.md)(?:#[^)]+)?\)' -AllMatches
}
```

Expected: 所有本地 Markdown 链接都能解析为存在的文件。

- [ ] **Step 4: 提交 README**

```powershell
git add -- README.md README.zh-CN.md
git commit -m "docs: add Chinese README"
```

### Task 2: 基础架构中文文档

**Files:**
- Create: `docs/architecture/agent-core.zh-CN.md`
- Create: `docs/architecture/provider-adapters.zh-CN.md`
- Create: `docs/architecture/readonly-tools.zh-CN.md`
- Create: `docs/architecture/governed-writes.zh-CN.md`
- Create: `docs/architecture/governed-command-execution.zh-CN.md`
- Create: `docs/architecture/context-budget.zh-CN.md`
- Create: `docs/architecture/session-trace.zh-CN.md`
- Create: `docs/architecture/checkpoint-resume.zh-CN.md`

- [ ] **Step 1: 逐篇翻译并保留章节结构**

每篇文档顶部添加：

```markdown
[English](name.md) | [简体中文](name.zh-CN.md)
```

保留代码块、类型名、事件名、错误码、路径和命令；明确区分治理边界与 OS Sandbox。

- [ ] **Step 2: 检查中英文标题结构**

Run:

```powershell
rg -n '^#{1,4} ' docs/architecture/agent-core* docs/architecture/provider-adapters* docs/architecture/readonly-tools* docs/architecture/governed-writes* docs/architecture/governed-command-execution* docs/architecture/context-budget* docs/architecture/session-trace* docs/architecture/checkpoint-resume*
```

Expected: 每一对文件拥有相同数量和层级的标题。

- [ ] **Step 3: 提交基础架构翻译**

```powershell
git add -- docs/architecture/*.zh-CN.md
git commit -m "docs: translate core architecture guides"
```

### Task 3: 高级能力中文文档

**Files:**
- Create: `docs/architecture/readonly-git.zh-CN.md`
- Create: `docs/architecture/governed-test-execution.zh-CN.md`
- Create: `docs/architecture/bounded-repair-loop.zh-CN.md`
- Create: `docs/architecture/governed-extensions.zh-CN.md`
- Create: `docs/architecture/governed-mcp.zh-CN.md`
- Create: `docs/architecture/governed-subagents.zh-CN.md`
- Create: `docs/architecture/governed-worktree-candidates.zh-CN.md`
- Create: `docs/architecture/threat-model.zh-CN.md`

- [ ] **Step 1: 逐篇翻译高级架构文档**

保持英文文档中的 Purpose、Composition、Failure Matrix、Threat Boundary、Non-claims 等
结构；不得把 approval、Workspace boundary、stdio 或 Worktree 描述成 OS 级隔离。

- [ ] **Step 2: 检查文件一一对应**

Run:

```powershell
$english = Get-ChildItem docs/architecture -Filter '*.md' |
  Where-Object Name -NotLike '*.zh-CN.md'
$missing = $english | Where-Object {
  -not (Test-Path (Join-Path $_.DirectoryName ($_.BaseName + '.zh-CN.md')))
}
$missing
```

Expected: 无输出。

- [ ] **Step 3: 提交高级架构翻译**

```powershell
git add -- docs/architecture/*.zh-CN.md
git commit -m "docs: translate advanced architecture guides"
```

### Task 4: 文档导航、验证与 GitHub 元数据

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: 补齐双语文档导航**

英文 README 的 Documentation 保留英文架构链接并补充中文入口；中文 README 链接
`.zh-CN.md` 架构文档、`docs/learning/*.md` 和 `docs/resume/*.md`。

- [ ] **Step 2: 运行 Markdown 与仓库检查**

Run:

```powershell
git diff --check
uv run ruff format --check .
uv run ruff check .
```

Expected: 三条命令退出码均为 0。

- [ ] **Step 3: 更新 GitHub Description**

Run:

```powershell
gh repo edit linjiayebat/mini-code-agent --description "从零实现、框架轻量且模型无关的 Python 编码 Agent，支持安全工具、人工审批、可恢复状态与可审计执行。"
```

Expected: 命令退出码为 0。

- [ ] **Step 4: 验证 GitHub 元数据**

Run:

```powershell
gh repo view linjiayebat/mini-code-agent --json description,url
```

Expected: `description` 为新的中文简介。

- [ ] **Step 5: 提交导航并推送**

```powershell
git add -- README.md README.zh-CN.md
git commit -m "docs: complete bilingual navigation"
git push origin main
```
