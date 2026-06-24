# Web UI Redesign — 设计文档

## 概述

将 Telegram 灌水工具从 Flet 桌面 GUI 重构为 Flask + Jinja2 的 Web 界面。保持 `src/` 核心逻辑不变，替换 `ui/` 层。

## 动机

- Flet 前端交互体验不佳，响应迟缓
- 桌面 GUI 限制了部署和使用方式
- 需要更美观、响应式的前端体验

## 技术选型

| 层 | 技术 | 理由 |
|---|------|------|
| 后端框架 | Flask | 用户指定，零新增依赖 |
| 实时推送 | SSE (Server-Sent Events) | 单向推送足够，无需 WebSocket |
| 模板引擎 | Jinja2 | Flask 原生集成 |
| 前端 | 原生 CSS3 + ES6 | 避免构建工具链 |
| 异步运行时 | asyncio in background thread | Flask 是 WSGI，发送循环需独立线程 |

## 架构

### 新文件结构

```
be_water_TG/
├── web_app.py                    # Flask 应用 + 路由注册
├── web_manager.py                # SendLoopManager 发送循环生命周期
├── templates/
│   ├── base.html                 # 基础布局（侧栏 + 主内容区）
│   └── index.html                # 主页面
├── static/
│   ├── css/style.css             # 暗色主题样式
│   └── js/app.js                 # SSE 连接、表单交互
```

### 修改的现有文件

- `ui/send_loop.py` — 移除 ft.Page 依赖，改为通过 EventBus 报告状态
- `main.py` — 从 Flet 改为启动 Flask 开发服务器

### 保持不变的现有文件

`src/` 目录下全部文件（config、sender、ai_client、ai_sender、selector、interval、group_parser、message_loader、logger）不做修改。`ui/message_manager.py` 也不做修改。

### 废弃的旧文件

`ui/app.py`、`ui/config_form.py`、`ui/control_panel.py`、`ui/status_panel.py` 不再需要，保留但不加载。

## 后端设计

### web_app.py 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 渲染主页面 |
| GET | `/api/config` | 从 .env 加载配置返回 JSON |
| POST | `/api/config` | 保存配置到 .env |
| POST | `/api/start` | 启动发送循环 |
| POST | `/api/pause` | 暂停（完成当前轮次后） |
| POST | `/api/resume` | 恢复发送 |
| POST | `/api/stop` | 停止发送 |
| GET | `/api/events` | SSE 端点 |
| POST | `/api/code` | 提交 Telegram 验证码 |

### web_manager.py — SendLoopManager

全局单例，管理发送循环全生命周期。线程模型：

```
Flask 主线程                    后台线程
┌─────────────┐              ┌──────────────────────┐
│ POST /start │──启动线程──→│ asyncio.run(send_loop)│
│ POST /stop  │──修改状态──→│ SendState.stopped=True│
│ GET /events │←──消费队列──│ event_queue (queue.Queue)
└─────────────┘              └──────────────────────┘
```

核心接口：

- `start(sender, settings, message_manager, ai_sender=None)` — 在后台线程中创建 asyncio event loop，启动 send_loop
- `pause()` — 设置 SendState.paused = True
- `resume()` — 设置 SendState.paused = False
- `stop()` — 设置 SendState.stopped = True，等待线程退出
- `submit_code(code: str)` — 通过 Future 传递验证码
- `is_running() -> bool` — 判断是否正在运行
- `get_event_queue() -> queue.Queue` — 返回线程安全队列供 SSE 消费

### EventBus

EventBus 是 send_loop.py 和 SSE 之间的桥梁，运行在后台 asyncio 线程中。内部使用 `asyncio.Queue` 暂存事件，同时通过一个专用任务将队列内容转发到线程安全的 `queue.Queue`（供 Flask SSE 端点消费）。

接口：

```python
class EventBus:
    async def emit_log(self, level: str, message: str)
    async def emit_counter(self, total: int, per_group: dict)
    async def emit_countdown(self, seconds: int)
    async def emit_status(self, state: str)  # idle|running|pausing|paused
```

### 日志桥接

注册 `LogQueueHandler` 到根 logger，将所有 logging 输出自动写入 EventBus 的队列：

```python
class LogQueueHandler(logging.Handler):
    """将 Python logging 输出路由到 SSE 的事件队列。"""
    def __init__(self, event_bus: EventBus):
        ...
    def emit(self, record: logging.LogRecord):
        # 写入 event_bus 的队列
```

这样 `logger.info("...")` 调用自动出现在前端日志区，无需手动调用 emit_log。

### SSE 事件类型

SSE 端点 `GET /api/events` 持续推送：

| 事件 | 数据 | 说明 |
|------|------|------|
| `log` | `{"level": "info"|"warning"|"error", "message": "..."}` | 追加日志行 |
| `status` | `{"state": "idle"|"running"|"pausing"|"paused"}` | 更新状态指示器 |
| `counter` | `{"total": int, "per_group": {"group": count}}` | 更新发送计数 |
| `countdown` | `{"seconds": int}` | 更新倒计时，0 时隐藏 |
| `code_required` | `{}` | 弹出验证码输入框 |

### send_loop.py 改动

当前签名：

```python
async def send_loop(page, sender, settings, state, message_manager, status_panel=None, ai_sender=None)
```

改为：

```python
async def send_loop(sender, settings, state, message_manager, event_bus=None, ai_sender=None)
```

改动点：
- 移除所有 `page.update()` 调用和 `page` 参数
- 移除 `status_panel` 参数，替换为 `event_bus`
- `add_log()` → `event_bus.emit_log()`
- `update_counter()` → `event_bus.emit_counter()`
- `update_countdown()` → `event_bus.emit_countdown()`
- 核心发送逻辑（重试、FloodWait 处理、群组遍历）不变

### 验证码处理

1. send_loop 需要验证码时 → 调用 `event_bus.emit_log("info", "请输入验证码...")` → SSE 推送 `code_required`
2. 前端显示输入框 → 用户提交 → `POST /api/code {code: "12345"}`
3. web_app 接收 → `manager.submit_code("12345")` → Future set_result
4. sender 等待的 `code_callback` 返回代码，继续执行

## 前端设计

### 页面布局

左侧边栏 (220px) + 右侧主内容区的 dashboard 结构。

侧栏固定显示：
- 应用名称 + Telegram 图标
- 状态指示圆点 + 状态文字
- 导航菜单：⚙ 配置 / 📋 运行日志
- 操作按钮：▶ 开始 / ⏸ 暂停 / ▶ 继续 / ⏹ 停止（根据状态自动显隐）
- 底部统计：已发送 N 条 / 下一轮倒计时

配置面板（六个 `<details>` 折叠区域）：
1. 基础配置：API ID、API Hash、手机号、目标群组输入框
2. 发送设置：最小/最大间隔、代理地址/端口/代理类型下拉框
3. AI 配置：开关切换、API Key、Base URL、Model、Prompt 文本框、上下文数
4. 定时运行：开关切换、上午开始/结束、下午开始/结束
5. 反检测增强：开关切换、打字延迟、思考延迟、潜水概率
6. 消息文件：根据目标群组动态生成路径输入行

操作按钮：保存配置 / 加载配置（底部）

运行日志面板：
- 顶部分组计数器（总发送数 + 各群组独立计数）
- 终端风格日志区（黑底 #0d1117，绿字 #3fb950，等宽字体，自动滚动到底部）
- 验证码输入框（需要时显示，带提交按钮）

### 配色方案

| 用途 | 色值 |
|------|------|
| 页面背景 | #0a0a14 |
| 侧栏背景 | #0f0f1e |
| 卡片背景 | rgba(255,255,255,0.04) |
| 输入框背景 | #1e1e36 |
| 输入框边框 | rgba(255,255,255,0.1) |
| 主强调色 | #06d6a0 |
| 蓝色按钮 | #4a90d9 |
| 红色（停止）| #ef4444 |
| 橙色（暂停）| #f59e0b |
| 正文文字 | #e2e8f0 |
| 次要文字 | #64748b |
| 终端背景 | #0d1117 |
| 终端文字 | #3fb950 |

### JavaScript 逻辑

- 页面加载后建立 `EventSource('/api/events')`
- `onmessage` 根据 event.type 分发到对应 handler：
  - `log` → 追加行到日志区
  - `status` → 更新状态圆点颜色 + 按钮显隐
  - `counter` → 更新计数显示
  - `countdown` → 更新倒计时，0 时隐藏
  - `code_required` → 显示验证码输入框
- 表单提交用 `fetch()` POST JSON，不用 form submit（避免页面刷新）
- 日志区自动滚动：内容追加时 scrollTop = scrollHeight，用户手动滚动上翻时暂停自动滚动

## 边界情况

- **FloodWait**: 保持现有重试逻辑，不影响
- **连接断开**: sender.py 已有异常处理，传递到 event_bus
- **多标签页**: 不处理隔离，各自有独立 SSE 连接
- **页面刷新**: 正在运行的发送循环不中断（后台线程独立），刷新后前端重新连接 SSE 获取最新状态
- **浏览器关闭**: SSE 断开，发送循环继续运行直到显式 stop
- **`logging` 模块去重**: 避免 GUIHandler 和 LogQueueHandler 双重输出（保留原有的 RotatingFileHandler + StreamHandler）

## 未涉及（后续考虑）

- HTTPS/SSL
- 多用户支持
- 持久化消息队列
- 移动端深度适配
