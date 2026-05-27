# 代码结构与规范修复

## TODOs

- [ ] 1. `src/interval.py` + `src/logger.py` — 添加 `from __future__ import annotations`
  **Agent**: `quick`

- [ ] 2. `src/sender.py` — 统一日志模式：`logger = logging.getLogger(__name__)` 替代 `setup_logger`
  **Agent**: `quick`

- [ ] 3. `src/message_loader.py` — 添加 logger + 日志记录
  **Agent**: `quick`

- [ ] 4. `src/ai_sender.py` — 修复子串误匹配：`text in own_history` → 显式比较
  **Agent**: `quick`

- [ ] 5. `src/sender.py` — 废弃字段替换：`target_group` → `target_groups[0]`
  **Agent**: `quick`

- [ ] 6. `ui/status_panel.py` — 条件链改为 dict 映射
  **Agent**: `quick`

- [ ] 7. `ui/send_loop.py` — 循环超时保护：窗口外等待加最大次数
  **Agent**: `quick`

## Final Verification
- [ ] F1. pytest 66/66 + 导入验证
