# 高优先级优化修复

## TODOs

- [x] 1. `sender.py` — 新增公开方法 `get_recent_messages()`，消除私有成员访问
- [x] 2. `ai_sender.py` — 修复私有成员访问 + None 安全
- [x] 3. `send_loop.py` — `on_paused_callback` 防重复触发 + AI 回复截断
- [x] 4. `app.py` — 日志文案修正 + 删除 control_panel 死代码
- [x] 5. `status_panel.py` — 移除未使用的 `import sys`
- [x] 6. `sender.py` — 代理类型支持 PROXY_TYPE 环境变量
- [x] 7. `ai_sender.py` — 上下文格式增强
- [x] F1. pytest 66/66 + 导入验证 ✅
- [x] F2. 代码审查 ✅
