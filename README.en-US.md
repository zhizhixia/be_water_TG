

# Telegram Auto-Sender / Telegram Mass-Messaging Tool

[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/Flet-0.85-orange)](https://flet.dev/)
[![Telethon](https://img.shields.io/badge/Telethon-1.43-purple)](https://github.com/LonamiWebs/Telethon)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![AI](https://img.shields.io/badge/AI-DeepSeek-brightgreen)](https://deepseek.com)

A Telegram multi-group smart mass-messaging tool based on Flet + Telethon. Supports two modes: random TXT sentence selection / AI deep chat. Features a built-in human-like behavior simulation engine to minimize the risk of account restrictions.

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows-blue?logo=windows" alt="Windows">
  <img src="https://img.shields.io/badge/GUI-Flet_Tabs-blueviolet" alt="Flet">
  <img src="https://img.shields.io/badge/API-User_Account-blue?logo=telegram" alt="TG">
  <img src="https://img.shields.io/badge/Proxy-HTTP/SOCKS5-orange" alt="Proxy">
</p>

---

## ✨ Features

### 🎛 Sending Modes
| Mode | Description |
|------|-------------|
| **TXT Random Selection** | Comma-separated text file, randomly selected to avoid consecutive repeats |
| **🤖 AI Smart Chat** | Reads group chat context → DeepSeek API generates human-like replies |

### 🕵️ Human-like Behavior Simulation
| Feature | Description |
|---------|-------------|
| Typing Simulation | Shows "Typing..." for 3-8s before sending |
| Thinking Delay | Randomly waits 5-25s before each round |
| Lurking Rounds | Random probability to skip a whole round without speaking |
| Message Length Variation | AI replies occasionally shortened to avoid a fixed style |
| Emoji Suffix | 30% chance for AI replies to randomly append an emoji |
| Scheduled Windows | Custom working hours; automatically pauses outside them |
| 🤖 AI Deduplication | Automatically skips if the last message is still in context |

### 🛡 Stability & Reliability
- State machine driven — Start/Pause/Resume/Stop; automatically completes the current round after pausing
- Stop confirmation dialog to prevent accidental clicks
- Real-time display of sent count + group count + countdown
- 3 retries with exponential backoff; automatically falls back to TXT if AI fails
- Automatic `FloodWait` handling; proxy support for HTTP/SOCKS5

---

## 🚀 Quick Start

### Environment
- Python 3.12+
- A Telegram user account
- [api_id / api_hash](https://my.telegram.org)

### Installation

```bash
git clone https://github.com/yourname/be_water_TG
cd be_water_TG
conda create -n be_water python=3.13 -y
conda activate be_water
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

```env
# Required
API_ID=12345
API_HASH=abc123def456
PHONE=+8613800138000
TARGET_GROUPS=https://t.me/group1, https://t.me/group2
MIN_INTERVAL=20
MAX_INTERVAL=30

# Optional: Proxy
PROXY_HOST=127.0.0.1
PROXY_PORT=7890
PROXY_TYPE=http          # http or socks5

# Optional: Message Files
MESSAGE_FILES=https://t.me/group1|messages.txt

# Optional: AI Mode
AI_ENABLED=true
AI_API_KEY=sk-your-key
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
AI_PROMPT=You are a regular group chat participant. Reply naturally based on the context.
AI_CONTEXT_COUNT=5

# Optional: Scheduled Windows
SCHEDULE_ENABLED=false
SCHEDULE_MORNING_START=08:00
SCHEDULE_MORNING_END=11:00
SCHEDULE_AFTERNOON_START=14:00
SCHEDULE_AFTERNOON_END=18:00

# Optional: Anti-Detection Enhancement
ANTI_DETECT=false
TYPING_DELAY_MIN=3
TYPING_DELAY_MAX=8
THINKING_DELAY_MIN=5
THINKING_DELAY_MAX=25
SKIP_ROUND_PCT=10
```

### Running

```bash
python main.py          # Desktop GUI
python main.py --web    # Browser mode
```

---

## 📁 Project Structure

```
be_water_TG/
├── main.py
├── run.bat
├── requirements.txt
├── .env.example
│
├── src/                       # Core
│   ├── config.py              # Settings + .env read/write
│   ├── sender.py              # Telethon client
│   ├── ai_client.py           # DeepSeek SDK wrapper
│   ├── ai_sender.py           # Context + memory + dedup
│   ├── message_loader.py      # File parsing
│   ├── selector.py            # Random selection
│   ├── interval.py            # Intervals + time formatting
│   ├── group_parser.py        # Link parsing
│   └── logger.py              # Logging
│
├── ui/                        # Flet GUI
│   ├── app.py                 # Main window + bridge
│   ├── config_form.py         # Config form
│   ├── control_panel.py       # Buttons + state machine
│   ├── status_panel.py        # Logs + counts + countdown
│   ├── send_loop.py           # Main send loop
│   └── message_manager.py     # Message selector
│
└── tests/                     # 66 tests
    ├── test_config_compat.py
    ├── test_group_parser.py
    ├── test_interval.py
    ├── test_message_loader.py
    ├── test_selector.py
    ├── test_control_panel.py
    └── test_ai_client.py
```

---

## 🧪 Testing

```bash
pytest tests/ -v
# 66 passed
```

---

## ⚙️ Control Flow

```
[Start]
  │
  ├── Check scheduled window → Wait if outside window
  ├── Thinking delay (5-25s)
  ├── Lurking check → 10% chance to skip whole round
  │
  ├─ TXT Mode ─→ Random sentence selection
  │
  └─ AI Mode ──→ Dedup check (last msg still in context? → Skip)
          │       ↓ Pass
          │     Fetch group chat context
          │       ↓
          │     DeepSeek generates reply
          │       ↓ Fail → Fallback to TXT
          │
          ▼
  Typing simulation (3-8s "Typing...")
          │
          ▼
  Send message → Update count → Random interval
          │
          └── Loop ←──┘

[Pause] → Finish current round → PAUSED
[Resume] → Resume sending
[Stop] → Confirmation dialog → Disconnect
```

---

## 🔧 Dependencies

| Package | Purpose |
|---------|---------|
| `telethon` ≥1.34 | Telegram client |
| `flet` ≥0.84 | GUI framework |
| `python-dotenv` | Configuration management |
| `python-socks[asyncio]` | SOCKS5 proxy |
| `openai` ≥1.0 | AI API client |
| `pytest` ≥7 | Testing |

---

## ⚠️ Notes / Warnings

- Use a **user account**; misuse may lead to restrictions
- `sender_session.session` contains tokens; **do not share it**
- AI mode requires you to apply for your own DeepSeek API Key
- Minimum send interval is recommended to be ≥20s

## 📄 License

MIT
