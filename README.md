# Be Water — Telegram 自动消息发送工具

基于 [Telethon](https://github.com/LonamiWebs/Telethon) 的 Telegram 用户账号自动消息发送工具。从本地 txt 文件随机选取消息，按可配置的随机间隔发送至指定群组，支持 FloodWait 自动处理、网络重试和 Session 持久化。

> **Be Water, my friend.** — Bruce Lee  
> 如水般适应环境，安静地流动。

## 功能

- 使用 Telegram 用户账号（非 Bot）发送消息
- 目标群组通过 username 指定
- 消息内容来源于本地 txt 文件（中/英文逗号分隔均可）
- 随机选消息，不连续发送相同内容
- 随机发送间隔（可配置范围）
- FloodWait 自动等待恢复
- 网络异常自动重试（最多 3 次，间隔递增）
- 首次登录保存 session，后续自动复用
- Ctrl+C 优雅退出
- 可选 HTTP 代理支持

## 项目结构

```
be_water_TG/
├── main.py                  # 入口 —— 异步主循环
├── messages.txt             # 消息文件（中/英文逗号分隔）
├── requirements.txt         # 依赖清单
├── .env.example             # 配置模板
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── config.py            # 配置加载（.env → dataclass）
│   ├── logger.py            # 日志配置
│   ├── message_loader.py    # 消息文件加载与解析
│   ├── selector.py          # 随机选择 + 去重逻辑
│   ├── sender.py            # Telethon 客户端管理与消息发送
│   └── interval.py          # 随机间隔生成（秒 → 中文格式化）
└── tests/
    ├── __init__.py
    ├── test_message_loader.py
    ├── test_selector.py
    └── test_interval.py
```

## 快速开始

### 前置条件

- Python 3.7+（推荐 3.11+）
- Telegram 用户账号（非 Bot）
- `api_id` 和 `api_hash`（从 [my.telegram.org](https://my.telegram.org) 获取）

### 安装

```bash
# 克隆
git clone https://gitee.com/zhizhixiazh/be_water_TG.git
cd be_water_TG

# 安装依赖
pip install -r requirements.txt
```

### 配置

```bash
# 复制配置模板
cp .env.example .env
```

编辑 `.env`：

```env
# Telegram API 凭据 (从 https://my.telegram.org 获取)
API_ID=你的api_id
API_HASH=你的api_hash

# 手机号 (含国际区号，如 +8613800138000)
PHONE=+86xxxxxxxxxxx

# 目标群组 username（不含 @）
TARGET_GROUP=your_group_username

# 发送间隔范围（秒）
MIN_INTERVAL=60
MAX_INTERVAL=180

# HTTP 代理（可选，国内环境可能需要）
# PROXY_HOST=127.0.0.1
# PROXY_PORT=7890
```

编辑 `messages.txt`，用中文或英文逗号分隔消息：

```
你好,今天天气不错,记得喝水,学习加油
```

### 运行

```bash
python main.py
```

首次运行会提示输入验证码，之后会自动缓存 session 文件无需再次输入。

## 运行测试

```bash
pytest tests/ -v
```

## 注意事项

- ⚠️ **风控风险**：过于频繁的消息发送可能导致 Telegram 账号被限制或封禁
- ⏱️ **建议间隔**：`MIN_INTERVAL` 不低于 60 秒
- 🛑 **FloodWait**：遇到限流时程序会自动等待，请勿手动中断
- 📜 **合规使用**：确保你的消息发送行为符合 Telegram 服务条款和目标群组规则
- 🔐 **Session 安全**：`sender_session.session` 文件包含登录凭据，请勿分享或提交到仓库
