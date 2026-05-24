# AGENTS.md — be_water_TG

## 环境

- **Conda 环境**: `be_water`，Python 3.13，路径 `D:\miniconda\envs\be_water\python.exe`
- 双击 `run.bat` 即可启动 GUI

## 项目概要

Telegram 多群组自动灌水工具，Flet（桌面 GUI）+ Telethon（MTProto 客户端）+ python-dotenv（配置）。Python 3.12+，仅 Windows 平台。

## 命令

```bash
# 运行桌面 GUI
python main.py

# 浏览器模式
python main.py --web

# 运行全部测试（47 条，约 0.15s）
pytest tests/ -v

# 安装依赖
pip install -r requirements.txt
```

## 架构

```
main.py              → Flet 入口（--web 用 ft.app / 桌面用 ft.run）
ui/app.py            → 主窗口布局 + 组件连线 + 发送逻辑
ui/config_form.py    → 左侧配置表单（加载/保存 .env）
ui/control_panel.py  → 底部按钮面板（AppState 状态机）
ui/status_panel.py   → 右侧日志面板 + 验证码输入（GUIHandler 桥接 logging）
ui/send_loop.py      → 多群组异步发送主循环（SendState 状态机）
ui/message_manager.py → 每群组独立 MessageSelector 管理器

src/config.py        → Settings dataclass + load/save .env
src/sender.py        → Telethon 客户端封装（connect/login/send/disconnect）
src/selector.py      → 随机选消息，避免连续重复
src/interval.py      → 随机间隔生成 + 中文时间格式化
src/group_parser.py  → 群组链接解析（@username / t.me / https 三种格式 + 中英文逗号）
src/message_loader.py → 消息文件解析（逗号分隔）
src/logger.py        → 日志工厂函数（含 handler 去重）
```

**控制流**：GUI Start → load_settings → MessageManager（加载消息文件）→ TelegramSender.start（登录）→ send_loop 异步循环（每轮向所有群组各发一条 → 随机间隔 → 重复）。

**发送循环**：`send_loop.py` 由 `page.run_task()` 运行在 Flet 事件循环上，禁止直接 `asyncio.run()`。暂停 = 完成当前轮次后挂起；停止 = 立即退出循环并断开连接。重试策略：3 次，退避 [30, 60, 120] 秒。

## 代码风格

- 全部文件使用 `from __future__ import annotations`
- 类型注解用 Python 3.12 语法：`str | None`（不用 `Optional[str]`）、`list[str]`（不用 `List[str]`）
- `dataclass` 用于配置和状态对象（Settings、SendState）
- 每个模块顶部：`logger = logging.getLogger(__name__)`
- 日志中文输出
- 注释为中文
- 测试用 pytest，`monkeypatch` 隔离环境变量，`tmp_path` 创建临时文件
- 无 formatter/linter 配置 — 手动保持一致即可

## 陷阱与注意事项

### 代理类型硬编码
`src/sender.py:43` 中代理类型固定为 `("http", ...)`，不支持 SOCKS5。README 声称支持 SOCKS5 是假的 — 如需 SOCKS5，需自行修改该行。

### run.bat 硬编码 Python 路径
`run.bat:24` 写死了 `D:\miniconda\envs\be_water\python.exe`。修改或部署到其他机器时需要调整。

### TARGET_GROUP 向后兼容
`.env` 中 `TARGET_GROUP`（单数）已废弃但保留兼容。`config.py` 的 `load_settings()` 和 `save_settings()` 都处理了新旧两种 key 的互转逻辑。新增功能只使用 `TARGET_GROUPS`（复数）。

### 中文逗号支持
群组链接和消息文件都支持中文逗号（`，`）作为分隔符。`parse_group_links()` 和 `load_messages()` 都在内部做了 `replace("，", ",")` 转换。

### Session 文件安全
登录后生成的 `sender_session.session` 包含登录令牌，`.gitignore` 中已排除。首次运行需要手机验证码（GUI 内输入或 CLI `input()`）。

### Flet 异步模型
所有 UI 回调在 Flet 事件循环上运行。长时间操作（如发送循环）用 `page.run_task()` 执行。不要在 Flet 回调中调用 `asyncio.run()`。

### pytest 配置
`pytest.ini` 禁用了 `remotedata` 插件（`-p no:remotedata`），测试不依赖网络。

### 不直接修改 .env.pytest
测试中通过 `monkeypatch.setattr("src.config.load_dotenv", ...)` 阻断 `.env` 文件加载，然后用 `monkeypatch.setenv()` 注入环境变量 — 不要尝试创建 `.env.pytest`。

## 关键文件

| 文件 | 作用 |
|------|------|
| `src/config.py:36 load_settings()` | 从环境变量构建 Settings，含所有校验逻辑 |
| `ui/send_loop.py:36 send_loop()` | 发送主循环，通过 SendState.stopped/paused 控制 |
| `ui/app.py:80 start_sending()` | 串联配置加载→登录→发送循环的入口 |
| `ui/status_panel.py:11 GUIHandler` | 将 Python logging 桥接到 Flet GUI |
