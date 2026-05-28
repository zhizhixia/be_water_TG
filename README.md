# Telegram Auto-Sender / Telegram 灌水工具

[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/Flet-0.85-orange)](https://flet.dev/)
[![Telethon](https://img.shields.io/badge/Telethon-1.43-purple)](https://github.com/LonamiWebs/Telethon)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![AI](https://img.shields.io/badge/AI-DeepSeek-brightgreen)](https://deepseek.com)

基于 Flet + Telethon 的 Telegram 多群组智能灌水工具。支持 TXT 随机选句 / AI 深度聊天两种模式，内置真人行为模拟引擎，最大程度降低风控风险。

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows-blue?logo=windows" alt="Windows">
  <img src="https://img.shields.io/badge/GUI-Flet_Tabs-blueviolet" alt="Flet">
  <img src="https://img.shields.io/badge/API-User_Account-blue?logo=telegram" alt="TG">
  <img src="https://img.shields.io/badge/Proxy-HTTP/SOCKS5-orange" alt="Proxy">
</p>

---

## ✨ 功能

### 🎛 发送模式
| 模式 | 说明 |
|------|------|
| **TXT 随机选句** | 逗号分隔文本文件，随机抽取，避免连续重复 |
| **🤖 AI 智能聊天** | 读取群聊上下文 → DeepSeek API 生成真人式回复 |

### 🕵️ 真人行为模拟
| 功能 | 描述 |
|------|------|
| 打字模拟 | 发送前先显示"正在输入..." 3-8s |
| 思考延迟 | 每轮开始前随机等待 5-25s |
| 潜水回合 | 随机概率整轮跳过不发言 |
| 消息长度波动 | AI 回复偶尔截短，避免固定风格 |
| Emoji 尾巴 | AI 回复 30% 概率随机追加表情 |
| 定时窗口 | 自定义工作时间段，之外自动暂停 |
| 🤖 AI 去重 | 上条消息仍在上下文时自动跳过 |

### 🛡 稳定可靠
- 状态机驱动 — 启动/暂停/恢复/停止，暂停后自动完成当前轮次
- 停止确认对话框，防误触
- 发送计数 + 群组计数 + 倒计时实时显示
- 3 次重试 + 指数退避，AI 失败自动回退 TXT
- FloodWait 自动等待，代理支持 HTTP/SOCKS5

---

## 🚀 快速开始

### 环境
- Python 3.12+
- Telegram 用户账号
- [api_id / api_hash](https://my.telegram.org)

### 安装

```bash
git clone https://github.com/yourname/be_water_TG
cd be_water_TG
conda create -n be_water python=3.13 -y
conda activate be_water
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
```

```env
# 必填
API_ID=12345
API_HASH=abc123def456
PHONE=+8613800138000
TARGET_GROUPS=https://t.me/group1, https://t.me/group2
MIN_INTERVAL=20
MAX_INTERVAL=30

# 可选：代理
PROXY_HOST=127.0.0.1
PROXY_PORT=7890
PROXY_TYPE=http          # http 或 socks5

# 可选：消息文件
MESSAGE_FILES=https://t.me/group1|messages.txt

# 可选：AI 模式
AI_ENABLED=true
AI_API_KEY=sk-your-key
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
AI_PROMPT=你是普通群聊参与者，请根据上下文自然回复
AI_CONTEXT_COUNT=5

# 可选：定时窗口
SCHEDULE_ENABLED=false
SCHEDULE_MORNING_START=08:00
SCHEDULE_MORNING_END=11:00
SCHEDULE_AFTERNOON_START=14:00
SCHEDULE_AFTERNOON_END=18:00

# 可选：反检测增强
ANTI_DETECT=false
TYPING_DELAY_MIN=3
TYPING_DELAY_MAX=8
THINKING_DELAY_MIN=5
THINKING_DELAY_MAX=25
SKIP_ROUND_PCT=10
```

### 运行

```bash
python main.py          # 桌面 GUI
python main.py --web    # 浏览器模式
```

---

## 📁 项目结构

```
be_water_TG/
├── main.py
├── run.bat
├── requirements.txt
├── .env.example
│
├── src/                       # 核心
│   ├── config.py              # Settings + .env 读写
│   ├── sender.py              # Telethon 客户端
│   ├── ai_client.py           # DeepSeek SDK 封装
│   ├── ai_sender.py           # 上下文 + 记忆 + 去重
│   ├── message_loader.py      # 文件解析
│   ├── selector.py            # 随机选句
│   ├── interval.py            # 间隔 + 时间格式化
│   ├── group_parser.py        # 链接解析
│   └── logger.py              # 日志
│
├── ui/                        # Flet GUI
│   ├── app.py                 # 主窗口 + 桥接
│   ├── config_form.py         # 配置表单
│   ├── control_panel.py       # 按钮 + 状态机
│   ├── status_panel.py        # 日志 + 计数 + 倒计时
│   ├── send_loop.py           # 发送主循环
│   └── message_manager.py     # 消息选择器
│
└── tests/                     # 66 条测试
    ├── test_config_compat.py
    ├── test_group_parser.py
    ├── test_interval.py
    ├── test_message_loader.py
    ├── test_selector.py
    ├── test_control_panel.py
    └── test_ai_client.py
```

---

## 🧪 测试

```bash
pytest tests/ -v
# 66 passed
```

---

## ⚙️ 控制流程

```
[开始]
  │
  ├── 定时窗口检查 → 不在窗口则等待
  ├── 思考延迟 (5-25s)
  ├── 潜水判断 → 10% 概率整轮跳过
  │
  ├─ TXT 模式 ─→ 随机选句
  │
  └─ AI 模式 ──→ 去重检查 (上条仍在? → 跳过)
          │       ↓ 通过
          │     获取群聊上下文
          │       ↓
          │     DeepSeek 生成回复
          │       ↓ 失败 → 回退 TXT
          │
          ▼
    打字模拟 (3-8s "正在输入...")
          │
          ▼
    发送消息 → 计数更新 → 随机间隔
          │
          └── 循环 ←──┘

[暂停] → 完成当前轮 → PAUSED
[继续] → 恢复发送
[停止] → 确认对话框 → 断开
```

---

## 🔧 依赖

| Package | 用途 |
|---------|------|
| `telethon` ≥1.34 | Telegram 客户端 |
| `flet` ≥0.84 | GUI 框架 |
| `python-dotenv` | 配置管理 |
| `python-socks[asyncio]` | SOCKS5 代理 |
| `openai` ≥1.0 | AI API |
| `pytest` ≥7 | 测试 |

---

## ⚠️ 注意

- 使用**用户账号**，滥用可能被限制
- `sender_session.session` 含令牌，**勿分享**
- AI 模式需自行申请 DeepSeek API Key
- 最低发送间隔建议 ≥20s

## 📄 License

MIT
