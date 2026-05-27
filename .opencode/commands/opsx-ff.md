---
description: Create a change and generate all artifacts in isolated worktree (Experimental)
---

Fast-forward：创建 worktree → 在 worktree 内生成全部 artifacts → commit → 回 main。

**Input**: 变更名称（kebab-case），或变更描述。

### Step 1: 确定名称

如果提供了名称则直接用。否则 AskUserQuestion 询问。

### Step 2: 确认 git 仓库已有至少一个 commit

```bash
git rev-parse --verify HEAD
```

- 返回 0 → 继续
- 返回非 0 → 自动创建首次提交：

```bash
git add -A && git commit -m "chore: initial project setup"
```

### Step 3: 创建隔离 Worktree

Artifacts 在 feature 分支上生成，不在 main 上留痕迹。

```bash
openspec-superpowers-opencode ensure-worktree <name>
cd .worktrees/<name>
```

### Step 4: 在 worktree 内创建变更 scaffold

```bash
openspec new change "<name>"
```

### Step 5: 生成全部 artifacts

使用 **TodoWrite** 追踪 artifact 进度。

按依赖顺序循环（artifacts with `status: "ready"` first）：

对各 artifact：

a. 获取指令：
```bash
openspec instructions <artifact-id> --change "<name>" --json
```

b. 读取已完成的依赖 artifact 获取上下文

c. 使用 `template` 结构 + `instruction` 指引创建文件

d. 显示 "✓ Created <artifact-id>"

e. 循环直到 `openspec status --change "<name>" --json` 中所有 `applyRequires` 为 `"done"`

### Step 6: 提交 artifacts 并回 main

```bash
git add -A && git commit -m "change: <name>"
cd <project-root>
```

### Output

- 变更：`<name>`
- Worktree：`.worktrees/<name>/`
- 分支：`feature/<name>`
- 已创建：`<N>` 个 artifacts
- 提示：`/opsx-apply` 开始实现

### Artifact Creation Guidelines

- `context` 和 `rules` 是给你的约束，不是写入文件的内容
- 始终先读依赖 artifact 再创建新 artifact
- 上下文不清晰时优先 AskUserQuestion
- 确认每个 artifact 文件已写入后再继续下一个
