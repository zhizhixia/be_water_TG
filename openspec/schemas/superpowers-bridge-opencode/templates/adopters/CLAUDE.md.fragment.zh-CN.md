<!-- Source: superpowers-bridge/templates/adopters/CLAUDE.md.fragment.zh-CN.md -->
<!-- 把这一节贴进你专案的 CLAUDE.md,让 Claude 知道如何分流本 repo 使用本 schema 的工作。 -->
<!-- 若你客制 schema 名称或 bridge repo URL,请对应修改;否则保持原样即可。 -->

## 变更工作流(Claude Code 启动先读)

本 repo 采用 [`superpowers-bridge`](https://github.com/JiangWay/openspec-schemas/tree/main/superpowers-bridge) 衔接 OpenSpec 与 Superpowers。整合规则(语言、artifact 路径、PRECHECK)以该 bridge README 为准;以下是给 Claude 的 routing 指引。

### 入口分流

| 你看到的触发 | 应该怎么做 |
|---|---|
| 使用者以 narrative 开「设计讨论 / 脑力激荡」 | 先 verbal `superpowers:brainstorming`,**不**写到 `docs/superpowers/specs/`;对话收敛后依下方 5 条判准升级到 `/opsx:propose` |
| 使用者直接呼叫 `/opsx:new` / `/opsx:ff` / `/opsx:propose` | 走 schema 既定流程;artifact instruction 会在每步注入 |
| 使用者明确说 bug fix / typo / config 微调 / 文件更新 | 直接 PR,**不**建 change(见下方 skip 规则) |
| 已经在某个 change 中 | `/opsx:continue` 或 `/opsx:apply` / `/opsx:verify` / `/opsx:archive` 推进 |

### 何时**不**走 opsx(直接 PR)

| 情境 | 直接 PR? |
|---|---|
| 新功能 / 新 capability / 架构变更 / breaking change | ? 要走 opsx |
| Bug fix(不变更合约)/ 测试补写 / linter 规则 / 非破坏性升级 / typo / 文件 / config 值微调 | ? 直接 PR |

原则:**流程仪式跟风险成正比**。动到对外合约 / schema / 跨系统介接 / 合规边界 → opsx;其他 → 直接 PR。

### Verbal brainstorm 升级到 opsx 的 5 条判准

5 条**全满足**才升级(任一缺则继续 brainstorm,不写到 `docs/superpowers/specs/`):

1. **Scope 锁定** —— 一句话讲清「包含/不包含什么」
2. **主要设计分歧已收敛** —— 替代方案选过,剩下 TBD 有明确 owner 与影响面
3. **跨系统依赖盘点过** —— 对方就绪 / 暂 mock / 真未知,三选一讲得清
4. **验收条件可陈述** —— 具体 pass 条件(例:`./mvnw clean verify` 通过 + N 个成果)
5. **对话进入收敛** —— 最近几轮在 confirm 不在发散

全满足 → 主动建议使用者「要不要 `/opsx:propose`?」,使用者 ack 后落地。永远不要自动触发。

### Front-door 反模式(别做)

- 让 brainstorming 写到 `docs/superpowers/specs/`
- 让 writing-plans 写到 `docs/superpowers/plans/`
- TBD 没收敛就升级到 opsx
- 对 bug fix / typo 也建 change

详细见 [superpowers-bridge README §进入与离开的判断](https://github.com/JiangWay/openspec-schemas/blob/main/superpowers-bridge/README.zh-CN.md#进入与离开的判断entry--exit-gates)。
