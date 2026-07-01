# 并发模型与核心功能修复设计

- **日期**：2026-07-01
- **范围**：P0 功能错误 + P1 架构缺陷共 9 项
- **状态**：已通过用户分节批准（节 1-6 全部方案 A）
- **不涉及**：P2 可维护性重构、P3 健壮性微调、P4 旧模块测试补齐、P5 死代码清理

## 背景

`AGENTS.md` 描述的并发模型存在三类问题：状态机无锁导致控制 API 各自为政、SSE 单队列导致多标签页事件丢失、sender 与 send_loop 对 FloodWait 双重处理导致等待两倍时长。这些问题互相牵连，逐项打补丁会造成不一致。本设计统一重构并发模型，一次性解决 P0+P1 全部 9 项。

## 决策摘要

| 决策点 | 选择 | 关键理由 |
|--------|------|---------|
| 状态机锁 | 单一 `threading.RLock` + 显式状态枚举 | 并发量极小，单锁足以覆盖；双锁收益微不抵复杂度 |
| FloodWait 等待 | sender 不 sleep，send_loop 统一可中断等待 | sender 保持纯封装；`Event.wait` 可探测 stop |
| 计数线程安全 | 复用状态机锁 | 无需另加锁 |
| SSE 隔离 | 每连接独立队列 + seq 环形缓冲 + Last-Event-ID | 同时修复刷新丢历史日志 |
| sender 断连 | `_run_loop` try/finally 调 disconnect | 资源泄漏根因修复 |
| 前端改动 | 最小靶向，不重构 app.js | YAGNI，整体拆分属 P2 |
| 控制流 | 状态转移白名单 + 非法转移返回 409 | 锁的必然补充 |
| wait_for_code | 120s 超时 → STOPPED + SSE 错误事件 | 真正解决永久阻塞 |
| 启动校验 | start 闸门前置 validate_group_links | 失败提前到首次保存 |
| 测试基础设施 | 新增 conftest.py + pytest-asyncio | 顺手消除 P5 第 28 项重复 setup |

## 架构

### 1. 并发模型重构

`SendLoopManager` 引入：

```python
_state_lock = threading.RLock()
_state: SendState  # 枚举，见下方转移图
_stop_event = threading.Event()
_code_future: asyncio.Future | None
```

所有控制 API（`start / pause / resume / stop / submit_code`）必须 `with self._state_lock:` 才能读写 `_state`。`send_loop` 协程通过 `_stop_event` 接收停止信号；`state.total_count` 与 `state.per_group_counts` 在同一锁下读写。

EventBus 的连接隔离与 seq/history 独立处理，不纳入 `_state_lock`——环形缓冲由 EventLoop 自己负责同步。

### 2. FloodWait 单次等待 + 停止响应

修改 `src/sender.py:101-106`：`send_message` 捕获 `FloodWaitError` 后**立即 raise**，不再 `await asyncio.sleep(e.seconds)`。FloodWait 策略上移至 `send_loop`。

`ui/send_loop.py:196-207` 的等待逻辑改为：

```python
stop_event = manager.stop_event
woke = stop_event.wait(timeout=e.seconds)
if woke:
    # 用户点了停止，跳出等待并转 STOPPED
    await manager.transition(SendState.STOPPED)
    return
# 否则正常继续下一轮
```

实现为循环累计等待：`remaining = e.seconds; while remaining > 0 and not woke: woke = stop_event.wait(timeout=min(0.5, remaining)); remaining -= 0.5`。每 0.5s 探测一次 stop_event，最长停止响应延迟 0.5s。

### 3. SSE 隔离 + 历史重放

`web_manager.EventBus` 重构为：

```python
_seq: int = 0
_history: deque(maxlen=500)  # 环形缓冲，存 (seq, event_data)
_subscribers: list[Queue]

def publish(self, data):
    with self._lock:
        self._seq += 1
        self._history.append((self._seq, data))
        for q in self._subscribers:
            q.put((self._seq, data))  # 非阻塞 put_nowait，满则丢

def subscribe(self, last_seq: int = -1) -> Queue:
    q = Queue(maxsize=500)
    with self._lock:
        for seq, data in self._history:
            if seq > last_seq:
                q.put_nowait((seq, data))
        self._subscribers.append(q)
    return q
```

`/api/events` 端点读取 `Last-Event-ID` 请求头（或 `?last_event_id=` 查询参数兼容 localStorage 方案），调 `subscribe(last_seq)`。每条 SSE 消息首行 `id: <seq>`。

### 4. sender 断连泄漏修复

`web_manager.py:_run_loop` 改为：

```python
async def _run_loop(self, settings, ...):
    sender = TelegramSender(settings)
    try:
        await sender.connect(...)
        await send_loop(sender, ...)
    finally:
        await sender.disconnect()
        await manager.transition(SendState.STOPPED)
```

异常、stop、正常退出三种路径都走 finally，disconnect 被调用恰好一次。

### 5. 控制流统一化

`SendLoopManager.transition(target_state)` 是唯一的状态变更入口，内部 `_state_lock` 串行化。状态转移白名单：

```
IDLE ──start()──> STARTING
STARTING ──connect_ok──> RUNNING                    (快速通道)
STARTING ──need_code──> WAITING_CODE ──submit_code──> RUNNING
STARTING ──code_timeout_120s──> STOPPED
RUNNING ──pause()──> PAUSING ──round_done──> PAUSED
PAUSED  ──resume()──> RUNNING
任意    ──stop()──>  STOPPING ──loop_exited──> STOPPED ──reset──> IDLE
```

非法转移返回 `409 + {"current": <state>, "target": <state>}`。

`wait_for_code` 的 `code_future` 由 send_loop 线程内 `asyncio.wait_for(future, timeout=120)` 守护；超时后状态转 STOPPED 并 publish SSE 错误事件 `"验证码超时，请停止后重启"`。

`group_gap_min/max` 补到 `Settings` 序列化字段（已有运行时读取，只缺 `/api/config` GET/POST 透传）；前端节 6 加控件。

`/api/start` 进入 `STARTING` 前调 `src.group_parser.validate_group_links(settings.target_groups)`，无效抛 `ValueError("群组链接无效: <详情>")` → `422 + {"detail": <详情>}`。

## 前端改动（templates/index.html + static/js/app.js）

不重构 app.js 整体结构。仅三处靶向改动：

1. **SSE 重连**：`onmessage` 里 `localStorage.setItem('sse_last_id', e.lastEventId)`；新建 `EventSource` 时 URL 加 `?last_event_id=<id>`（兼容浏览器原生 `Last-Event-ID` 头的备用方案）
2. **group_gap 控件**：仿 `MIN_INTERVAL` 写法加两个数字输入（label "群组间隔下限/上限"，默认 1，范围 0-3600，单位秒）+ 表单保存逻辑
3. **校验错误反馈**：`POST /api/start` 返回 4xx 时不启动且显示 toast；`submitCode` 检查响应，失败时不隐藏输入框并显示原因

### 前后端接口契约（写死）

- SSE 每条消息按标准格式：`id: <seq>\ndata: <json>\n\n`
- `GET /api/events?last_event_id=<seq>` → 从 seq+1 开始推送；无参数 → 仅推新事件
- `POST /api/start` 失败返回 `422 + {"detail": <群组链接详情>}`
- `POST /api/submit_code` 超时后返回 `409 + {"current": "STOPPED"}`

## 测试策略

**仅针对新逻辑加回归测试**，不补原有零覆盖模块的旧测试。新增 `conftest.py` 集中 `set_required_env` / `event_bus` / `state_machine` / `fake_sender`（mock Telethon）/ `asyncio loop` fixtures，顺手消除 P5 第 28 项重复 setup。`requirements.txt` 加 `pytest-asyncio>=0.23`。

### 新增测试文件

| 文件 | 覆盖修复项 | 测试要点 |
|------|-----------|---------|
| `tests/test_send_state_machine.py` | 节 1 + 节 5 状态机 | 7 条合法转移、6 条典型非法转移返回 409、并发 start 不产生两个循环 |
| `tests/test_send_loop_floodwait.py` | 节 2 FloodWait | sender 不再 sleep（assert await 调用次数=0）、send_loop 单次等待、stop_event 置位后 ≤0.5s 退出 |
| `tests/test_send_loop_floodwait.py` | 节 2 计数 | 并发 pause/resume/stop 期间 `/api/status` 读取计数无撕裂 |
| `tests/test_event_bus.py` | 节 3 SSE | 多订阅者均收到事件、订阅后回放历史、环形缓冲溢出丢弃最旧且 seq 不回退、Last-Event-ID 边界 |
| `tests/test_send_loop_lifecycle.py` | 节 3 disconnect | 正常/异常/stop 退出后 disconnect 被调用恰好一次 |
| `tests/test_events_endpoint.py` | 节 4 SSE URL | `?last_event_id=N` 正确回放、无参数走新事件、过期 seq 返回最新 |
| `tests/test_code_timeout.py` | 节 5 wait_for_code | 120s 后状态转 STOPPED、SSE 错误事件发出、submit_code 在超时后返回 409 |
| `tests/test_start_validation.py` | 节 5 启动校验 | 无效群组链接 → 422 + 详情、有效 → 200、空链接 → 422 |

### 明确排除

- 不补 `src/ai_client.py`、`src/ai_sender.py`、`src/sender.py` 真实 Telethon 调用测试
- 不补 `static/js/app.js` 前端测试
- 不删/改 `tests/test_control_panel.py`（P5 死代码，本次不动）
- 不拆分 `src/config.py`（P2 范围）
- 不拆分 `static/js/app.js`（P2 范围）

## 依赖与风险

### 新增依赖

- `pytest-asyncio>=0.23`（加入 requirements.txt）

### 已知风险

1. **状态机覆盖面广**：所有控制 API 都走 `transition`，遗漏任一会绕过锁。测试矩阵需覆盖全 5 个端点
2. **环形缓冲 500 条上限**：极端高并发日志场景可能丢历史。本项目单发送循环日志频率低，500 条远超实际需求
3. **EventBus 替换为多订阅者广播后内存**：每订阅者独立 500 大小队列，最多几十 KB，可接受
4. **stop 精度 0.5s**：每 0.5s 探测一次 stop_event，用户体感即时。如需更精细可改 0.1s 步长，但 polling 频率提高无显著收益

## 不在本次范围（明确标注防漂移）

- P2 `src/config.py` 拆分（359 行超警戒，但纯可维护性）
- P2 `static/js/app.js` 拆分（330 行超警戒，但纯可维护性）
- P3 异常吞没日志化、AI 空串兜底、should_skip 误判
- P4 `web_app / send_loop / sender / ai_sender` 旧逻辑覆盖提升
- P5 `SendState.on_paused_callback` 死代码清理
- P5 `test_control_panel.py` 死代码测试清理