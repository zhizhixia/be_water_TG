---
description: Finish an implemented change - merge worktree, cleanup, archive, PR (Experimental)
---

完成 `/opsx-apply` 实现后的变更收尾工作：合并 worktree、清理、产出 retrospective、归档、开 PR。

**Input**: 可选指定变更名称（如 `/opsx-finish add-auth`）。省略时从 `.worktrees/` 目录自动检测。

**收尾流程**：

```
0. 检测     → 找到要完成的 worktree
1. 测试     → 切换到 worktree 运行测试套件（验证 worktree 自身完好）
2. 回顾     → 产出 retrospective.md（文件操作，与代码无关）
3. 归档     → openspec archive -y（文件操作，与代码无关）
4. 选项     → Read finishing-a-development-branch skill → 展示 4 个选项
5. 执行     → 按用户选择执行
```

**两个测试点**，定位不同问题：

| 时机 | 测什么 |
|------|--------|
| Step 1 — worktree 内 | 验证 worktree 自身实现完好，避免带着已有问题往下走 |
| 选项 1 rebase 之后 | base 变了，代码基于新基线可能有冲突。rebase 后再测一次 |

---

### Step 0: 确保 worktree 存在并选中

```bash
openspec-superpowers-opencode ensure-worktree <name>
```

如果已有多个 worktree，`cd .worktrees/<name>` 根据用户指定的名称进入。

如果没有任何 worktree，`ensure-worktree` 创建时会失败并退出——没有 worktree 就没有可完成的分支。

### Step 1: 切换到 worktree 并验证测试

```bash
cd .worktrees/<name>
```

运行测试套件（自动检测项目类型）：

```
- 存在 package.json     → npm test
- 存在 Cargo.toml       → cargo test
- 存在 requirements.txt
  或 pyproject.toml     → pytest
- 存在 go.mod           → go test ./...
- 其他                  → 从 Makefile / CI 配置推断或 AskUserQuestion
```

**测试失败** → STOP，告知用户失败详情，修复后再运行 `/opsx-finish`。

> 这个测试点的目的是验证 worktree 自身实现完好，避免带着已有问题往下走。
> 它与 rebase 后的测试不冲突——Step 1 测 worktree 自身，rebase 后测基线变更。

### Step 2: 产出 retrospective

Read retrospective artifact 的 instruction：

```bash
openspec instructions retrospective --change "<name>"
```

按 instruction 产出 retrospective.md。

### Step 3: 归档变更

```bash
openspec archive -y
```

此操作将：
- 把 `openspec/changes/<name>/specs/` 的 delta specs 同步到 `openspec/specs/`
- 变更目录移到 `openspec/changes/archive/YYYY-MM-DD-<name>/`

### Step 4: 读取 finishing-a-development-branch skill

Read：
  {{SUPERPOWERS_BASE_PATH}}finishing-a-development-branch\SKILL.md

按 skill 内容执行：

**Step 4a: 确定基础分支**（通常是 main 或 master）

**Step 4b: 展示 4 个选项给用户：**

```
实现完成。接下来要做什么？

1. 合并到主分支（本地）
2. 推送并创建 Pull Request
3. 保留分支，稍后处理
4. 丢弃本次变更

请选择：
```

**不要添加额外解释** — 保持选项简洁。

### Step 5: 执行用户选择#### 选项 1: 本地合并（rebase）

> worktree 分支未推送过，rebase 不重写任何公开历史。
> 每个 task 一个 commit 是**有意义的逻辑边界**，保留它们比 squash 成一个对 `git blame` / `git bisect` 更友好。
>
> **两个测试点**：
> - Step 1 已测 worktree 自身实现完好
> - rebase 后再测一次：base 变了，代码基于新基线可能有冲突

```bash
git checkout <feature-branch>
git rebase <base-branch>            # 线性化到 base 分支最新
<test command>                       # 自动检测项目类型运行测试
git checkout <base-branch>
git merge <feature-branch>          # fast-forward
git worktree remove .worktrees/<name>  # 先移除 worktree，分支才可删除
git branch -d <feature-branch>
```

#### 选项 2: 推送并创建 PR

```bash
git push -u origin <feature-branch>
gh pr create --title "<title>" --body "## Summary\n..."
```

PR 创建后，worktree 保留供参考（但用户可手动删除）。

#### 选项 3: 保留分支

告知用户分支和 worktree 路径。不做任何清理。

#### 选项 4: 丢弃

**要求用户输入 'discard' 确认**后执行：

```bash
git checkout <base-branch>
git branch -D <feature-branch>
git worktree remove .worktrees/<name>
```

---

**无 worktree 时**：如果用户运行 `/opsx-finish` 但对应 worktree 已被手动清理，
检查是否有对应分支残留，建议用户手动处理。

**幂等性**：如果 retrospective.md 已存在则跳过；如果变更已归档则跳过。
