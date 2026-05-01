# Telegram Auto-Sender / Telegram 灌水工具

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/Flet-0.84-orange)](https://flet.dev/)
[![Telethon](https://img.shields.io/badge/Telethon-1.43-purple)](https://github.com/LonamiWebs/Telethon)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

A Telegram auto-sender with a modern GUI, built with Flet + Telethon. Send messages to multiple groups at random intervals with full control (start/pause/resume/stop).

基于 Flet + Telethon 的 Telegram 多群组自动灌水工具，带现代化 GUI 界面，支持启动/暂停/恢复/停止。

---

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows-blue?logo=windows" alt="Windows">
  <img src="https://img.shields.io/badge/GUI-Flet_Material_Design-blueviolet" alt="Flet">
  <img src="https://img.shields.io/badge/API-Telegram_User-blue?logo=telegram" alt="Telegram">
</p>

## ✨ Features / 功能

- 🖥️ **Modern GUI** — Material Design interface with system dark/light theme
- 👥 **Multi-Group** — Send to multiple Telegram groups in one session
- 📁 **Per-Group Messages** — Each group has its own message file
- 🎮 **Full Control** — Start / Pause / Resume / Stop buttons
- 📋 **Real-time Log** — Terminal output displayed live in GUI
- 🔐 **Auto-Login** — Session file reused, no repeated phone verification
- 🌐 **Proxy Support** — HTTP/SOCKS5 proxy for users behind firewalls
- ⏱️ **Random Interval** — Configurable send intervals to mimic human behavior
- 🔄 **Retry Logic** — 3-attempt retry with exponential backoff (30s/60s/120s)
- 🛡️ **FloodWait Handling** — Auto-wait when Telegram rate-limits

---

## 📸 Screenshot / 截图

```
┌─────────────────────────────────────────────────────────┐
│              Telegram 灌水工具                            │
├──────────────────────┬──────────────────────────────────┤
│  ⚙️ Config            │  📋 Log                          │
│                      │                                  │
│  API ID: [______]    │  [18:53:46] Connecting...        │
│  API Hash: [______]  │  [18:53:47] Logged in via session│
│  Phone: [______]     │  [18:53:48] ✅ t.me/group1: ...  │
│  Target Groups:      │  [18:54:10] ⏳ Next in 25s...    │
│  [https://t.me/a,    │                                  │
│   https://t.me/b]    │                                  │
│  Interval: [20][30]  │                                  │
│  Proxy: [host][port] │                                  │
│                      │                                  │
│  📁 Message files    │                                  │
│  group1: [path.txt]  │                                  │
│  group2: [path.txt]  │                                  │
│                      │                                  │
│  [📂 Load] [💾 Save] │                                  │
├──────────────────────┴──────────────────────────────────┤
│   [▶ Start] [⏸ Pause] [▶ Resume] [⏹ Stop]   Running... │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start / 快速开始

### Prerequisites / 前置条件

- Python 3.12+
- Telegram user account (not bot)
- `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org)

### Installation / 安装

```bash
git clone <your-repo-url>
cd Be_water
pip install -r requirements.txt
```

### Configuration / 配置

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
API_ID=your_api_id
API_HASH=your_api_hash
PHONE=+8613800138000
TARGET_GROUPS=https://t.me/group1, https://t.me/group2
MIN_INTERVAL=20
MAX_INTERVAL=30

# Optional: HTTP proxy
PROXY_HOST=127.0.0.1
PROXY_PORT=7890

# Optional: message file mapping
MESSAGE_FILES=group1:messages.txt,group2:messages2.txt
```

Prepare your message file(s) — comma-separated:

```
Hello world, How are you, Good morning
```

### Run / 运行

```bash
# Desktop app
python main.py

# Browser mode
python main.py --web

# Or double-click
run.bat
```

## 📁 Project Structure / 项目结构

```
Be_water/
├── main.py                    # Entry point / Flet launcher
├── run.bat                    # Double-click launcher (Windows)
├── messages.txt               # Default message corpus
├── requirements.txt           # Python dependencies
├── .env.example               # Configuration template
├── .gitignore
│
├── src/                       # Core logic
│   ├── config.py              # Settings dataclass + .env load/save
│   ├── sender.py              # Telethon client wrapper
│   ├── message_loader.py      # Message file parsing
│   ├── selector.py            # Random selection + dedup
│   ├── interval.py            # Random interval generator
│   ├── group_parser.py        # Group link parsing (CN/EN commas)
│   └── logger.py              # Logging setup
│
├── ui/                        # Flet GUI
│   ├── app.py                 # Main window + component wiring
│   ├── config_form.py         # Configuration input form
│   ├── control_panel.py       # Start/Pause/Resume/Stop buttons
│   ├── status_panel.py        # Real-time log + code input
│   ├── send_loop.py           # Multi-group async send orchestrator
│   └── message_manager.py     # Per-group message selector manager
│
└── tests/                     # Pytest tests (47 cases)
    ├── test_config_compat.py  # Backward compatibility tests
    ├── test_group_parser.py   # Link parsing tests
    ├── test_interval.py
    ├── test_message_loader.py
    └── test_selector.py
```

## 🧪 Testing / 测试

```bash
pytest tests/ -v
# 47 passed in 0.15s
```

## ⚙️ Control Flow / 控制流程

```
[GUI Start Button]
       │
       ▼
  load_settings() → Settings from .env
       │
       ▼
  MessageManager() → per-group MessageSelector
       │
       ▼
  TelegramSender.start() → connect + login
       │
       ▼
  send_loop() ───┬─→ group A: select → send
       ▲         ├─→ group B: select → send
       │         ├─→ ...wait interval...
       │         └─→ repeat
       │
  ┌────┴─────┐
  │ Pause    │ → completes current round, then suspends
  │ Resume   │ → continues from where it stopped
  │ Stop     │ → exits loop, disconnects
  └──────────┘
```

## 🔧 Dependencies / 依赖

| Package | Version | Purpose |
|---------|---------|---------|
| `telethon` | ≥1.34.0 | Telegram MTProto client |
| `flet` | ≥0.84.0 | Material Design GUI framework |
| `python-dotenv` | ≥0.19.0 | .env configuration |
| `python-socks[asyncio]` | ≥2.0.0 | SOCKS5/HTTP proxy support |
| `pytest` | ≥7.0.0 | Testing framework |

## ⚠️ Notes / 注意事项

- **Rate Limiting**: Frequent sending may trigger Telegram FloodWait. Recommended `MIN_INTERVAL` ≥ 20s.
- **Account Safety**: This uses a **user account**. Abusing it may lead to account restrictions.
- **Session File**: `sender_session.session` contains login tokens — **never share it**.
- **Compliance**: Ensure your usage complies with Telegram's Terms of Service.

## 📄 License / 许可

MIT License

---

**Made with ⚡ by Sisyphus**
