# AGENTS.md — be_water_TG

## 环境

- **Conda 环境**: `be_water`，Python 3.13，路径 `D:\miniconda3\envs\be_water\python.exe`
- 双击 `run.bat` 即可启动 Web UI（浏览器自动打开 http://127.0.0.1:5000）

## 项目概要

Telegram 多群组自动灌水工具，Flask（Web GUI）+ Telethon（MTProto 客户端）+ python-dotenv（配置）。支持 AI 聊天模式（DeepSeek API）、定时运行窗口、反检测增强。Python 3.12+，仅 Windows 平台。

## 命令

```bash
# 启动 Web UI
python main.py

# 运行全部测试
pytest tests/ -v

# 仅跑不依赖 openai/flet 的测试
pytest tests/ -v --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py
```

## 架构

```
main.py              → Flask 入口（启动开发服务器）
web_app.py           → Flask 应用 + 路由注册（/api/config, /api/start 等）
web_manager.py       → EventBus + SendLoopManager + LogQueueHandler
templates/
  base.html          → 基础布局（侧栏 + 主内容区）
  index.html         → 配置面板 + 运行日志面板
static/
  css/style.css      → 暗色主题样式
  js/app.js          → SSE 连接、表单交互、UI 控制

src/config.py        → Settings dataclass + load/save .env（配置校验）
src/sender.py        → Telethon 客户端封装（connect/login/send/disconnect）
src/selector.py      → 随机选消息，避免连续重复
src/interval.py      → 随机间隔生成 + 中文时间格式化
src/group_parser.py  → 群组链接解析（@username / t.me / https 三种格式）
src/message_loader.py → 消息文件解析（逗号分隔）
src/logger.py        → 日志工厂函数（含 handler 去重）
src/ai_client.py     → DeepSeek LLM 客户端（openai SDK 封装）
src/ai_sender.py     → AI 消息生成器（群聊上下文 + 短期记忆 + 去重）
ui/send_loop.py      → 多群组异步发送主循环（SendState 状态机）
ui/message_manager.py → 每群组独立 MessageSelector 管理器
```

### Web 架构说明

- **实时通信**：Server-Sent Events (SSE)，端点 GET /api/events
- **发送循环**：后台 threading.Thread 运行 asyncio event loop，通过 EventBus 桥接日志到 SSE
- **状态管理**：SendLoopManager 全局单例管理发送循环生命周期
- **日志桥接**：LogQueueHandler 将 Python logging 自动路由到 Web 前端

**控制流**：双击 run.bat → Flask 启动 → 浏览器打开配置页 → Start → 后台线程启动 send_loop → SSE 推送实时日志到浏览器终端。

### 路由一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 主页面 |
| GET | `/api/config` | 加载 .env 配置 |
| POST | `/api/config` | 保存配置到 .env |
| POST | `/api/start` | 启动发送循环 |
| POST | `/api/pause` | 暂停（完成当前轮次后） |
| POST | `/api/resume` | 恢复发送 |
| POST | `/api/stop` | 停止发送 |
| GET | `/api/events` | SSE 实时推送端点 |
| POST | `/api/code` | 提交 Telegram 验证码 |

## 陷阱与注意事项

### run.bat 硬编码 Python 路径
`run.bat:24` 写死了 `D:\miniconda3\envs\be_water\python.exe`。修改或部署到其他机器时需要调整。

### 代理配置
代理类型通过 `PROXY_TYPE` 环境变量控制（默认 `"http"`）。`src/sender.py:42` 读取 `settings.proxy_type`，支持任意 Telethon 支持的代理类型（http、socks5 等）。`requirements.txt` 已包含 `python-socks[asyncio]`，SOCKS5 可正常使用。需同时设置 `PROXY_HOST` 和 `PROXY_PORT`，缺一不可。

### 发送循环线程模型
send_loop 运行在后台 daemon 线程中。Flask 主进程退出时线程自动终止。不要在同一个浏览器打开多个标签页操作同一个进程（无隔离）。

### 页面刷新不中断发送
页面刷新后 SSE 重新连接，发送循环继续在后台运行。需要在侧栏重新查看状态。

### TARGET_GROUP 向后兼容
`.env` 中 `TARGET_GROUP`（单数）已废弃但保留兼容。`config.py` 的 `load_settings()` 和 `save_settings()` 都处理了新旧两种 key 的互转逻辑。新增功能只使用 `TARGET_GROUPS`（复数）。

### MESSAGE_FILES 分隔符
格式使用 `|` 分隔群组和文件路径。旧格式 `:` 仍兼容。

### Session 文件安全
登录后生成的 `sender_session.session` 包含登录令牌，`.gitignore` 中已排除。首次运行需要手机验证码（Web 界面内输入）。

### AI 模式需手动安装 openai
`openai>=1.0.0` 在 `requirements.txt` 中。跑 AI 相关测试前确认 `pip install openai`。
