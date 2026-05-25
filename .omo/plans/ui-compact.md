# UI 紧凑化布局

## TL;DR
- 日志面板缩小，配置/日志改为标签页切换
- 全局减少内边距，窗口缩小为 750×600
- 仅修改 `ui/app.py`，不涉及其他文件

## TODOs

- [x] 1. app.py 标签页布局 + 紧凑化

  **What to do**:
  - 将 `ft.Row([left_panel, right_panel])` 替换为 `ft.Tabs`
  - Tab 1："⚙️ 配置"，内容为 `config_form.build()`
  - Tab 2："📋 日志"，内容为 `status_panel.build()`
  - 底部控制面板（`bottom_bar`）保持在 Tabs 外面，始终可见
  - 窗口缩小：`width=750, height=600`，`min_width=550, min_height=400`
  - 全局 padding：`page.padding = 12`
  - 容器 padding：`padding=12`（原来 16）
  - 标题 font size：`size=22`（原来 28），padding bottom：`10`（原来 16）
  - 移除 `left_panel` 和 `right_panel` 的 `expand` 参数，Tabs 自动处理

  **Must NOT do**:
  - 不修改 `config_form.build()` 和 `status_panel.build()` 的内部实现
  - 不改变任何按钮或表单的逻辑
  - 不修改其他文件

  **Recommended Agent Profile**: `visual-engineering`

  **Acceptance Criteria**:
  - [ ] `python -c "import ui.app; print('OK')"` 通过
  - [ ] `python -m pytest tests/ -q` 全部通过
  - [ ] 标签页切换不丢失任何表单状态或日志内容

  **Commit**: `feat(ui): 标签页切换布局 + 全局紧凑化`

## Final Verification Wave

- [x] F1. 导入验证 + pytest — `oracle`
  Output: `Imports OK | Tests [59/59 pass] | VERDICT: APPROVE`
- [x] F2. 代码审查 — `unspecified-high`
  Output: `Files [1/1 clean] — only ui/app.py modified | VERDICT: APPROVE`

---

## Must Have
- 标签页切换：配置 tab 和日志 tab
- 底部控制面板始终可见（不随 tab 切换）
- 日志面板 min_lines 从 10 减到 5，max_lines 从 30 减到 15
- 窗口缩小到 750×600
- 全局 padding 从 20 减到 12

## Must NOT Have
- 不修改 src/ 目录
- 不修改 ui/ 除 app.py 外的其他文件
- 不删除现有任何功能
