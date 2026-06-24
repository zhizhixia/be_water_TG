# Web UI Redesign 实现计划

> **面向 AI 代理的工作者：** 内联执行，逐任务实现。步骤使用复选框（`- [ ]`）追踪进度。

**目标：** 将 Flet 桌面 GUI 重构为 Flask + Jinja2 Web 界面，保持 src/ 核心逻辑不变。

**架构：** Flask SSE 后端 + EventBus 桥接发送循环 + 原生 JS 前端。后台线程跑 asyncio send_loop，通过 queue.Queue 向 Flask SSE 端点传递事件。

**技术栈：** Flask 3.x, Jinja2, SSE, 原生 CSS3/ES6, Telethon, asyncio

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `web_manager.py` | EventBus + SendLoopManager (发送循环生命周期) |
| `ui/send_loop.py` | 修改：移除 ft.Page，接入 EventBus |
| `static/css/style.css` | 暗色主题样式 |
| `templates/base.html` | 侧栏+主内容区布局 |
| `templates/index.html` | 配置+运行双面板 |
| `static/js/app.js` | SSE 连接、表单交互、UI 状态 |
| `web_app.py` | Flask 路由注册 |
| `main.py` | 修改：Flask 入口 |

---

### 任务 1：web_manager.py — EventBus + SendLoopManager

**文件：** 创建 `C:\Users\xzh\Desktop\be_water_TG\web_manager.py`

- [ ] **步骤 1：实现 EventBus 类**

EventBus 是 send_loop 和 SSE 之间的桥梁：
- `emit_log(level, message)` → 放入线程安全队列
- `emit_counter(total, per_group)` → 同上
- `emit_countdown(seconds)` → 同上
- `emit_status(state)` → 同上
- `get_event()` → 被 SSE 端点消费，非阻塞获取事件

使用 `queue.Queue` 跨线程传递。事件格式为 `{"type": "...", "data": {...}}`。

- [ ] **步骤 2：实现 LogQueueHandler**

继承 `logging.Handler`，将 Python logging 路由到 EventBus。

```python
class LogQueueHandler(logging.Handler):
    def __init__(self, event_bus: EventBus):
        super().__init__()
        self._event_bus = event_bus
    def emit(self, record):
        level = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "info"
        self._event_bus.emit_log(level, self.format(record))
```

- [ ] **步骤 3：实现 SendLoopManager**

管理发送循环全生命周期：
- `start(settings, message_manager, code_callback)` → 后台线程启动 asyncio
- `pause()` / `resume()` / `stop()`
- `submit_code(code)` → 通过 Future 传递验证码
- `get_event_queue()` → 返回 `queue.Queue`

### 任务 2：修改 ui/send_loop.py

**文件：** 修改 `C:\Users\xzh\Desktop\be_water_TG\ui\send_loop.py`

- [ ] **步骤 1：修改函数签名**

去掉 `page: ft.Page` 和 `status_panel`，改为 `event_bus` 参数。

```python
async def send_loop(
    sender: TelegramSender,
    settings: Settings,
    state: SendState,
    message_manager: MessageManager,
    event_bus: EventBus | None = None,
    ai_sender: AISender | None = None,
) -> None:
```

- [ ] **步骤 2：替换所有 page.update() 和 status_panel 调用**

查找替换：
- `page.update()` → 删除
- `status_panel.add_log(x, y)` → `event_bus.emit_log(x, y)`
- `status_panel.update_counter(x, y)` → `event_bus.emit_counter(x, y)`
- `status_panel.update_countdown(x)` → `event_bus.emit_countdown(x)`

- [ ] **步骤 3：移除 flet 导入**

删除 `import flet as ft` 和 `TYPE_CHECKING` 中的 `StatusPanel` 导入。

### 任务 3：static/css/style.css

**文件：** 创建 `C:\Users\xzh\Desktop\be_water_TG\static\css\style.css`

- [ ] **步骤 1：完整暗色主题样式**

涵盖：
- 全局重置、flexbox 布局
- 左侧边栏 (220px) + 主内容区
- 配置表单样式（折叠面板、输入框、开关）
- 终端日志区（黑底绿字、等宽字体）
- 控制按钮（主题色、悬停效果）
- 状态指示器（圆点动画）
- 验证码输入弹窗
- 响应式适配

### 任务 4：templates/base.html

**文件：** 创建 `C:\Users\xzh\Desktop\be_water_TG\templates\base.html`

- [ ] **步骤 1：HTML 骨架 + 侧栏布局**

包含：
- DOCTYPE html, meta viewport
- CSS 和 JS 引用
- 侧栏：Logo、状态指示器、导航菜单、控制按钮、统计信息
- 主内容区：`{% block content %}{% endblock %}`
- JS 尾部加载

### 任务 5：templates/index.html

**文件：** 创建 `C:\Users\xzh\Desktop\be_water_TG\templates\index.html`

- [ ] **步骤 1：配置面板**

扩展 `base.html`，包含六个折叠区域（基础、发送、AI、定时、反检测、消息文件），每个区域对应 `<details>`。

- [ ] **步骤 2：运行日志面板**

扩展 `base.html`，包含计数器、终端日志区、验证码输入框。

放在 `{% block content %}` 内，通过 JS 控制选项卡切换显示。

### 任务 6：static/js/app.js

**文件：** 创建 `C:\Users\xzh\Desktop\be_water_TG\static\js\app.js`

- [ ] **步骤 1：SSE 连接**

```javascript
const evtSource = new EventSource('/api/events');
evtSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    switch(data.type) {
        case 'log': appendLog(data.level, data.message); break;
        case 'status': updateStatus(data.state); break;
        case 'counter': updateCounter(data.total, data.per_group); break;
        case 'countdown': updateCountdown(data.seconds); break;
        case 'code_required': showCodeInput(); break;
    }
};
```

- [ ] **步骤 2：表单交互**

使用 `fetch()` POST JSON 到 `/api/config`（保存）/ `/api/config` GET（加载）。

切换 tab 时隐藏/显示对应 panel。

- [ ] **步骤 3：控制按钮**

点击 → `POST /api/start` 等，根据返回状态更新 UI。

- [ ] **步骤 4：日志自动滚动**

内容追加时 scrollTop = scrollHeight，用户上翻时暂停。

### 任务 7：web_app.py

**文件：** 创建 `C:\Users\xzh\Desktop\be_water_TG\web_app.py`

- [ ] **步骤 1：Flask 应用初始化 + 路由**

所有路由注册：
```python
app = Flask(__name__)

@app.route("/")
def index(): ...

@app.route("/api/config", methods=["GET", "POST"])
def api_config(): ...

@app.route("/api/start", methods=["POST"])
def api_start(): ...

@app.route("/api/pause", methods=["POST"])
def api_pause(): ...

@app.route("/api/resume", methods=["POST"])
def api_resume(): ...

@app.route("/api/stop", methods=["POST"])
def api_stop(): ...

@app.route("/api/events")
def api_events(): ...

@app.route("/api/code", methods=["POST"])
def api_code(): ...
```

- [ ] **步骤 2：SSE 端点实现**

使用 `Response` + `text/event-stream` content type + generator function。

- [ ] **步骤 3：启动逻辑**

注册 LogQueueHandler 到根 logger，应用启动时初始化 SendLoopManager。

### 任务 8：修改 main.py

**文件：** 修改 `C:\Users\xzh\Desktop\be_water_TG\main.py`

- [ ] **步骤 1：改为 Flask 入口**

```python
if __name__ == "__main__":
    setup_root_logger()
    from web_app import app
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
```

### 任务 9：验证

- [ ] **步骤 1：安装 Flask**

```bash
pip install flask
```

- [ ] **步骤 2：启动检查**

```bash
python main.py
```

访问 http://127.0.0.1:5000 确认页面正常渲染。

- [ ] **步骤 3：检查所有路由**

确认 /api/config GET/POST、/api/events SSE 连接均正常。
