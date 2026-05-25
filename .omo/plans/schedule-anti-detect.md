# 定时运行窗口 + 反检测增强

## TL;DR

> **Quick Summary**: 新增可自定义的定时运行窗口（默认 8:00-11:00, 14:00-18:00），窗口外自动暂停、窗口内自动恢复；增加群组间随机间隔和 AI 回复随机 emoji 尾巴，让发送模式更接近真人。

## TODOs

- [x] 1. `src/config.py` + `.env.example` — Settings 新增时间段字段
- [x] 2. `ui/send_loop.py` — 定时窗口检查 + 群组间随机间隔 + emoji 尾巴
- [x] 3. `ui/config_form.py` — 定时窗口 UI
- [x] F1. pytest 66/66 + 导入验证 ✅
- [x] F2. 代码审查 ✅
