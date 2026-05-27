---
description: Start a new change in an isolated worktree (Experimental)
---

覆盖 OpenSpec 内置命令。创建 worktree → 在 worktree 内生成变更 → commit → 回 main。

**Input**: 变更名称（kebab-case），或变更描述。

### Step 1: 确定名称

如果提供了名称则直接用。否则 AskUserQuestion：
> "你想创建什么变更？描述一下你要构建或修复的内容。"

从描述推导 kebab-case 名称（如 "add user authentication" → `add-user-auth`）。

**不要在不理解用户意图时继续。**

### Step 2: 确定 schema

默认使用当前项目的 schema。除非用户明确要求其他 workflow，不要加 `--schema`。

### Step 3: 确认 git 仓库已有至少一个 commit

```bash
git rev-parse --verify HEAD
```

- 返回 0 → 继续
- 返回非 0（新部署项目无 commit）→ 自动创建首次提交：

```bash
git add -A && git commit -m "chore: initial project setup"
```

### Step 4: 创建隔离 Worktree

Artifacts 应在 feature 分支上生成，不在 main 上留痕迹。

```bash
openspec-superpowers-opencode ensure-worktree <name>
cd .worktrees/<name>
```

> worktree 继承了 repo 的所有基础设施文件（`.opencode/`、`openspec/config.yaml` 等）。

### Step 5: 在 worktree 内创建变更

```bash
openspec new change "<name>"
```

此命令在 worktree 的 `openspec/changes/<name>/` 创建 scaffold。

### Step 6: 展示第一个 artifact

```bash
openspec status --change "<name>"
openspec instructions <first-artifact-id> --change "<name>"
```

展示第一个 artifact 的 template 和 instruction，**不需要创建它**。

### Step 7: 提交 scaffold 并回 main

```bash
git add -A && git commit -m "change: <name> (scaffold)"
cd <project-root>
```

### Output

总结变更信息：

- 变更：`<name>`
- Worktree：`.worktrees/<name>/`
- 分支：`feature/<name>`
- Schema：...
- 下一步：`/opsx-continue` 创建下一个 artifact，或 `/opsx-ff <name>` 一次性生成全部

### Guardrails

- 如果同名变更已存在，建议用 `/opsx-continue`
- 不要创建任何 artifact——只展示第一个的 template
- worktree 创建后自动 `cd .worktrees/<name>`，后续命令（`/opsx-continue`）需检测 worktree
