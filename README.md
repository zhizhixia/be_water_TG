# Telegram Auto-Sender / Telegram 灌水工具

[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/Flet-0.85-orange)](https://flet.dev/)
[![Telethon](https://img.shields.io/badge/Telethon-1.43-purple)](https://github.com/LonamiWebs/Telethon)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

基于 Flet + Telethon 的 Telegram 多群组自动灌水工具，带现代化标签页 GUI。支持 TXT 随机选句 / AI 智能聊天两种模式，支持启动/暂停/恢复/停止全流程控制。

A Telegram auto-sender with tabbed GUI, built with Flet + Telethon. Supports TXT random messages and AI-powered chat (DeepSeek). Full Start/Pause/Resume/Stop control.

---

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows-blue?logo=windows" alt="Windows">
  <img src="https://img.shields.io/badge/GUI-Flet_Tabs-blueviolet" alt="Flet">
  <img src="https://img.shields.io/badge/API-Telegram_User-blue?logo=telegram" alt="Telegram">
  <img src="https://img.shields.io/badge/AI-DeepSeek-green" alt="DeepSeek">
</p>

## ✨ Features / 功能

- 🖥️ **标签页 GUI** — 配置和日志分标签切换，界面紧凑
- 🤖 **AI 智能聊天** — 读取群聊上下文，通过 DeepSeek API 生成真人般的回复
- 📁 **TXT 随机选句** — 从逗号分隔文件中随机选取消息，避免连续重复
- 👥 **多群组** — 同时向多个群组发送，每群组独立消息源
- 🎮 **全流程控制** — 启动 / 暂停 / 恢复 / 停止，暂停后自动推进状态
- 📊 **实时反馈** — 发送计数 + 群组计数 + 倒计时实时显示
- 🛑 **停止确认** — 停止前弹出确认对话框，防误触
- 🔐 **自动登录** — Session 文件复用，首次验证码后无需重复输入
- 🌐 **代理支持** — HTTP 代理（`PROXY_HOST` + `PROXY_PORT`）
- ⏱️ **随机间隔** — 可配置发送间隔，模拟真实用户行为
- 🔄 **重试逻辑** — 3 次重试，指数退避（30s/60s/120s），AI 失败自动回退 TXT
- 🛡️ **FloodWait 处理** — 触发限流时自动等待

---

## 🚀 Quick Start / 快速开始

### 环境 / Prerequisites

- Python 3.12+（推荐 conda 环境）
- Telegram 用户账号（非 Bot）
- `api_id` 和 `api_hash` 从 [my.telegram.org](https://my.telegram.org) 获取

### 安装 / Installation

```bash
# 创建 conda 环境
conda create -n be_water python=3.13 -y
conda activate be_water

# 安装依赖
pip install -r requirements.txt
```

或直接双击 `run.bat`（会自动使用 `D:\miniconda\envs\be_water\python.exe`）。

### 配置 / Configuration

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# Telegram API
API_ID=your_api_id
API_HASH=your_api_hash
PHONE=+8613800138000

# 目标群组（逗号分隔，支持中英文逗号）
TARGET_GROUPS=https://t.me/group1, https://t.me/group2

# 发送间隔（秒）
MIN_INTERVAL=20
MAX_INTERVAL=30

# 代理（可选，HTTP only）
PROXY_HOST=127.0.0.1
PROXY_PORT=7890

# 消息文件映射（可选，群组:文件路径）
MESSAGE_FILES=group1:messages.txt,group2:messages2.txt

# AI 聊天模式（可选）
AI_ENABLED=false
AI_API_KEY=sk-your-deepseek-api-key
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
AI_PROMPT=你是一个普通群聊参与者，请根据对话上下文自然地回复消息
AI_CONTEXT_COUNT=5
```

消息文件格式（逗号分隔，支持中英文逗号）：

```
你好,今天天气不错,记得喝水,学习加油
```

### 运行 / Run

```bash
python main.py          # 桌面 GUI
python main.py --web    # 浏览器模式
run.bat                 # 双击运行
```

---

## 📸 Screenshot / 截图

```
┌──────────────────────────────────────────────────┐
│              Telegram 灌水工具                     │
├──────────────────────────────────────────────────┤
│  [⚙️ 配置]  [📋 日志]                              │
│                                                  │
│  ── ⚙️ 配置 Tab ──                                │
│  API ID:         [______]                        │
│  API Hash:       [______]                        │
│  Phone:          [______]                        │
│  Target Groups:  [_____________]                 │
│  Interval:       [20] [30]                       │
│  Proxy:          [host] [port]                   │
│                                                  │
│  ── 🤖 AI 智能聊天 ──                             │
│  [🔘 AI 智能聊天模式]                              │
│  API Key:   [sk-xxx●●●●●]                        │
│  Base URL:  [https://api.deepseek.com/v1]        │
│  Model:     [deepseek-chat]                      │
│  Prompt:    [你是一个普通群聊参与者...]             │
│                                                  │
│  📁 消息文件                                      │
│  group1:   [messages1.txt]                       │
│  group2:   [messages2.txt]                       │
│                                                  │
│  [📂 加载配置]  [💾 保存配置]                       │
├──────────────────────────────────────────────────┤
│  [▶ 开始] [⏸ 暂停] [▶ 继续] [⏹ 停止]  运行中...    │
└──────────────────────────────────────────────────┘
```

---

## 📁 Project Structure / 项目结构

```
be_water_TG/
├── main.py                    # 入口 / Flet 启动器
├── run.bat                    # Windows 双击启动
├── messages.txt               # 默认消息库
├── requirements.txt           # Python 依赖
├── .env.example               # 配置模板
├── .gitignore
│
├── src/                       # 核心逻辑
│   ├── config.py              # Settings dataclass + .env 读写
│   ├── sender.py              # Telethon 客户端封装
│   ├── ai_client.py           # DeepSeek LLM 客户端
│   ├── ai_sender.py           # AI 消息生成（上下文 + 记忆）
│   ├── message_loader.py      # 消息文件解析
│   ├── selector.py            # 随机选句 + 防重复
│   ├── interval.py            # 随机间隔 + 中文时间格式化
│   ├── group_parser.py        # 群组链接解析（中英文逗号）
│   └── logger.py              # 日志工厂
│
├── ui/                        # Flet GUI
│   ├── app.py                 # 主窗口 + 标签页 + 桥接逻辑
│   ├── config_form.py         # 配置表单 + AI 开关
│   ├── control_panel.py       # 控制按钮 + 状态机 + 停止确认
│   ├── status_panel.py        # 日志面板 + 计数器 + 倒计时 + 验证码
│   ├── send_loop.py           # 多群组异步发送（TXT/AI 双模式）
│   └── message_manager.py     # 每群组消息选择器
│
└── tests/                     # Pytest 测试（66 条）
    ├── test_config_compat.py  # 向后兼容测试
    ├── test_group_parser.py   # 链接解析测试
    ├── test_interval.py       # 间隔测试
    ├── test_message_loader.py # 消息加载测试
    ├── test_selector.py       # 选择器测试
    ├── test_control_panel.py  # 状态机测试
    └── test_ai_client.py      # AI 客户端测试
```

---

## 🧪 Testing / 测试

```bash
pytest tests/ -v
# 66 passed
```

---

## ⚙️ Control Flow / 控制流程

```
[GUI 模式选择]
       │
       ├── TXT 模式 ──→ MessageSelector（随机选句）
       │
       └── AI 模式 ──→ 获取群聊上下文 → DeepSeek API → 生成回复
                         ↓ 失败
                       回退 TXT
       │
       ▼
  send_loop() ───┬─→ group A: 获取消息 → 发送
       ▲         ├─→ group B: 获取消息 → 发送
       │         ├─→ ...等待随机间隔（带倒计时）...
       │         └─→ 重复
       │
  ┌────┴─────────┐
  │ Pause        │ → 完成当前轮次 → PAUSING → PAUSED
  │ Resume       │ → 从暂停处继续
  │ Stop         │ → 确认对话框 → 退出循环，断开连接
  └──────────────┘
```

---

## 🔧 Dependencies / 依赖

| Package | Version | Purpose |
|---------|---------|---------|
| `telethon` | ≥1.34.0 | Telegram MTProto 客户端 |
| `flet` | ≥0.84.0 | Material Design GUI 框架 |
| `python-dotenv` | ≥0.19.0 | .env 配置管理 |
| `python-socks[asyncio]` | ≥2.0.0 | HTTP 代理支持 |
| `openai` | ≥1.0.0 | DeepSeek AI API 调用 |
| `pytest` | ≥7.0.0 | 测试框架 |

---

## ⚠️ Notes / 注意事项

- **发送频率**：频繁发送可能触发 FloodWait。建议 `MIN_INTERVAL` ≥ 20s。
- **账号安全**：使用**用户账号**而非 Bot，滥用可能导致账号受限。
- **Session 文件**：`sender_session.session` 含登录令牌——**切勿分享**。
- **代理**：当前仅支持 HTTP 代理（`PROXY_HOST` + `PROXY_PORT`），不支持 SOCKS5。
- **AI 模式**：需配置 DeepSeek API Key。AI 调用失败自动回退到 TXT 文件模式。
- **合规**：请确保使用方式符合 Telegram 服务条款。

---

## 📄 License / 许可

MIT License
