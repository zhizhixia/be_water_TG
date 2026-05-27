---
description: Implement tasks from an OpenSpec change in existing worktree (Experimental)
---

按照 schema 定义的 apply 阶段流程，在已存在的 worktree 中实现变更。

**Input**: 可选指定变更名称（如 `/opsx-apply add-auth`）。省略时从上下文推断，如果模糊则用 AskUserQuestion 让用户选择。

**Apply 只负责实现**——worktree 由 `/opsx-ff` / `/opsx-new` 提前创建。

```
1. 选择变更 → 检测 worktree → 进入
2. Pre-flight → 检查 skill 文件、工具链
3. 逐任务实现 → task() 派 Agent + 审查
4. Verification → openspec status
5. 提交 → 在 worktree 中提交
```

合并、清理、retrospective、archive、PR 由 `/opsx-finish` 处理。

---

### Step 1: 选择变更

如果提供了名称则直接用，否则：
- 从上下文推断
- 如果只有一个活跃变更则自动选中
- 如果模糊则 `openspec list --json` → AskUserQuestion

宣布：`"Using change: <name>"`

### Step 2: 确保 worktree 存在并进入

```bash
openspec-superpowers-opencode ensure-worktree <name>
cd .worktrees/<name>
```

### Step 3: 读取 schema apply 指令

```bash
openspec instructions apply --change "<name>" --json
```

同时读取 schema.yaml 中的 `apply:` 阶段指令了解完整流程。

### Step 4: Pre-flight

**4a. 确认以下 skill 文件都可 Read：**

{{SUPERPOWERS_BASE_PATH}}subagent-driven-development\implementer-prompt.md
{{SUPERPOWERS_BASE_PATH}}subagent-driven-development\spec-reviewer-prompt.md
{{SUPERPOWERS_BASE_PATH}}subagent-driven-development\code-quality-reviewer-prompt.md
{{SUPERPOWERS_BASE_PATH}}test-driven-development\SKILL.md

如果有任一 Read 失败，STOP 并告知用户，不要静默降级。

**4b. 确认 plan.md 中提到的工具链（mvn、docker、node 等）可用。**

### Step 5: 逐任务实现

Read：
  {{SUPERPOWERS_BASE_PATH}}subagent-driven-development\implementer-prompt.md
  {{SUPERPOWERS_BASE_PATH}}test-driven-development\SKILL.md

对 plan.md 中的每个微任务：
1. 使用 implementer-prompt.md 模板构造基础 prompt
2. 从 TDD SKILL.md 中提取 RED-GREEN-REFACTOR 流程、铁律作为 prompt 的前置指令
3. 通过 `task()` 派 Agent 实现（load_skills=[]，TDD 通过 prompt 嵌入传递）：

```typescript
task(
  category="deep",
  load_skills=[],
  description="实现 <任务名>",
  prompt="<TDD 前置指令 + 模板填充后的内容>",
  run_in_background=true
)
```

> 注意：Superpowers skills 是文件系统路径，不支持 `load_skills` 机制。
> 必须通过 Read 读取后在 prompt 中嵌入 TDD 指令。

### Step 6: 审查每个任务的产出

任务完成后，依次执行两项审查：

**Spec 审查**：
Read：
  {{SUPERPOWERS_BASE_PATH}}subagent-driven-development\spec-reviewer-prompt.md

用 spec-reviewer-prompt.md 模板构造审查 prompt，派 `task(subagent_type="oracle", ...)` 审查。

**代码质量审查**：
Read：
  {{SUPERPOWERS_BASE_PATH}}subagent-driven-development\code-quality-reviewer-prompt.md

用 code-quality-reviewer-prompt.md 模板构造审查 prompt，派 `task(subagent_type="oracle", ...)` 审查。

全部通过 → 标记 tasks.md checkbox 为完成。

### Step 7: Verification

所有任务完成后，在 worktree 中运行验证：

```bash
openspec status --change "<name>" --json
```

确认所有 artifacts 已完成。如果有未完成的，按提示补充。

### Step 8: 在 worktree 中提交变更

```bash
git add -A && git commit -m "<name>: <描述>"
```

### 完成

```
✓ 变更 <name> 已实现并提交。
  Worktree 路径: .worktrees/<name>/
  分支: feature/<name>

下一步: 运行 /opsx-finish 进行合并、清理和归档。
```

---

**幂等性**：如果某些步骤已完成（如已实现部分任务、已提交），则跳过。
通过检查 `.worktrees/<name>/` 是否存在、`git status`、tasks.md 的 checkbox 状态等来判断。

**用户中断**：任何时候用户可中断。再次运行 `/opsx-apply` 时从上一次中断处继续。
