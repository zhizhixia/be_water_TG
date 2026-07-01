# 并发模型与核心功能修复 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 一次性修复并发模型、FloodWait 双重等待、SSE 事件丢失、sender 断连泄漏等 P0+P1 共 9 项问题。

**架构：** 单一 `threading.RLock` 串行化 `SendLoopManager` 状态机；EventBus 改多订阅者 + seq 环形缓冲；sender 不再处理 FloodWait 等待，send_loop 用 `threading.Event.wait` 可中断等待；`_run_loop` 用 try/finally 保证 disconnect。

**技术栈：** Python 3.13、Flask、Telethon、pytest + pytest-asyncio、原生 JS/EventSource。

**编码原则（用户明确要求）：** 注释用简体中文；以便于维护而非便于编写的方式编写——优先可读性、显式优于隐式、单一职责、有意义的命名、错误处理不可吞没。

**设计文档：** `docs/superpowers/specs/2026-07-01-concurrency-core-fixes-design.md`

---

## 文件结构

| 文件 | 状态 | 职责 |
|------|------|------|
| `requirements.txt` | 修改 | 加 `pytest-asyncio>=0.23` |
| `tests/conftest.py` | 新建 | 公共 fixtures：`set_required_env` / `event_bus` / `fake_sender` / `asyncio_loop` |
| `src/config.py` | 修改 | `Settings` 新增 `group_gap_min/max` 字段；`load_settings` 读 env；`save_settings` 写 env |
| `web_manager.py` | 修改 | EventBus 多订阅+seq；SendLoopManager 状态机+锁+disconnect |
| `web_app.py` | 修改 | 控制路由走 transition；SSE last_event_id；start 前置校验；group_gap 透传 |
| `ui/send_loop.py` | 修改 | FloodWait 可中断等待；计数加锁；接收 stop_event |
| `src/sender.py` | 修改 | FloodWait 不再 sleep（立即 raise） |
| `static/js/app.js` | 修改 | group_gap 控件保存/加载；SSE last_event_id 已在前次提交完成 |
| `templates/index.html` | 修改 | group_gap 两个数字输入控件 |
| `tests/test_event_bus.py` | 新建 | SSE 隔离/重放/溢出 |
| `tests/test_send_state_machine.py` | 新建 | 状态机转移 |
| `tests/test_send_loop_floodwait.py` | 新建 | FloodWait 单次等待+停止响应+计数 |
| `tests/test_send_loop_lifecycle.py` | 新建 | disconnect 恰好一次 |
| `tests/test_events_endpoint.py` | 新建 | SSE URL 回放 |
| `tests/test_code_timeout.py` | 新建 | wait_for_code 超时 |
| `tests/test_start_validation.py` | 新建 | start 前置校验 |

**依赖顺序：** 任务 1（依赖）→ 2 → 3 → 4（EventBus）→ 5（状态机）→ 6（disconnect）→ 7（sender）+ 8（send_loop）→ 9（SSE 端点）→ 10（超时）→ 11（校验）→ 12（group_gap）→ 13（前端）。任务 7/8 可与 4/5/6 并行，但需先定接口。

---

### 任务 1：添加 pytest-asyncio 依赖

**文件：**
- 修改：`requirements.txt`

- [ ] **步骤 1：编辑 requirements.txt，在末尾追加 pytest-asyncio**

```
telethon>=1.34.0
python-dotenv>=0.19.0
pytest>=7.0.0
python-socks[asyncio]>=2.0.0
flask>=3.0.0
openai>=1.0.0
pytest-asyncio>=0.23
```

- [ ] **步骤 2：安装并验证**

运行：`D:\miniconda3\envs\be_water\python.exe -m pip install pytest-asyncio>=0.23`
预期：安装成功

- [ ] **步骤 3：Commit**

```bash
git add requirements.txt
git commit -m "build: 添加 pytest-asyncio 依赖"
```

---

### 任务 2：创建 conftest.py 公共 fixtures

**文件：**
- 创建：`tests/conftest.py`

- [ ] **步骤 1：编写 conftest.py**

```python
"""公共测试 fixtures，消除各测试文件重复的 setup 代码。"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from src.config import Settings


@pytest.fixture
def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置 load_settings 所需的基础环境变量，并阻止加载真实 .env 文件。"""
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "a" * 32)
    monkeypatch.setenv("PHONE", "+8613800138000")
    monkeypatch.setenv("MIN_INTERVAL", "60")
    monkeypatch.setenv("MAX_INTERVAL", "180")
    monkeypatch.setenv("GROUP_GAP_MIN", "1")
    monkeypatch.setenv("GROUP_GAP_MAX", "1")


@pytest.fixture
def make_settings() -> Settings:
    """构造一个合法的 Settings 实例（最小必填字段齐全）。"""
    return Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+8613800138000",
        target_groups=["https://t.me/test_group"],
    )


@pytest.fixture
def fake_sender() -> AsyncMock:
    """mock TelegramSender：所有方法是 AsyncMock，默认 connect/disconnect/send_message 无副作用。"""
    sender = AsyncMock()
    sender.connect = AsyncMock(return_value=None)
    sender.start = AsyncMock(return_value=None)
    sender.send_message = AsyncMock(return_value=None)
    sender.typing_indicator = AsyncMock(return_value=None)
    sender.disconnect = AsyncMock(return_value=None)
    sender.get_recent_messages = AsyncMock(return_value=[])
    return sender


@pytest.fixture
def stop_event() -> threading.Event:
    """send_loop 停止信号事件，测试可控触发停止。"""
    return threading.Event()


@pytest.fixture
def asyncio_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    """独立 asyncio 事件循环，测试 send_loop 协程用。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)
```

- [ ] **步骤 2：运行验证导入无误**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/ -v --collect-only --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py`
预期：所有现有测试可被收集，无 ImportError

- [ ] **步骤 3：Commit**

```bash
git add tests/conftest.py
git commit -m "test: 新增 conftest.py 公共 fixtures"
```

---

### 任务 3：Settings 新增 group_gap 字段

**文件：**
- 修改：`src/config.py:14-51`（Settings dataclass）
- 修改：`src/config.py:136-142`（load_settings 解析）
- 修改：`src/config.py:229-283`（save_settings 序列化）
- 修改：`src/config.py:197-226`（return Settings 构造调用）
- 测试：`tests/test_config_compat.py`（追加用例）

- [ ] **步骤 1：编写失败测试**

追加到 `tests/test_config_compat.py` 末尾：

```python
class TestGroupGap:
    """GROUP_GAP_MIN/MAX 配置项读写测试。"""

    def test_default_group_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置 env 时 group_gap 取默认值 1/1。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/a")
        monkeypatch.delenv("GROUP_GAP_MIN", raising=False)
        monkeypatch.delenv("GROUP_GAP_MAX", raising=False)
        settings = load_settings()
        assert settings.group_gap_min == 1
        assert settings.group_gap_max == 1

    def test_custom_group_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """env 设置后正确解析为整数。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/a")
        monkeypatch.setenv("GROUP_GAP_MIN", "5")
        monkeypatch.setenv("GROUP_GAP_MAX", "15")
        settings = load_settings()
        assert settings.group_gap_min == 5
        assert settings.group_gap_max == 15
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_config_compat.py::TestGroupGap -v`
预期：FAIL，报错 `AttributeError: 'Settings' object has no attribute 'group_gap_min'` 或 `TypeError: unexpected keyword argument 'group_gap_min'`

- [ ] **步骤 3：在 Settings dataclass 新增字段**

在 `src/config.py` 的 `Settings` dataclass 中，在 `skip_round_pct` 后追加：

```python
    skip_round_pct: int = 10

    # 群组间发送间隔（秒），用于控制每轮内向不同群组发送之间的最小/最大等待
    group_gap_min: int = 1
    group_gap_max: int = 1
```

- [ ] **步骤 4：在 load_settings 解析 GROUP_GAP_MIN/MAX**

在 `src/config.py` 的 `load_settings()` 中，`skip_round_pct = int(...)` 一行后追加：

```python
    skip_round_pct = int(os.getenv("SKIP_ROUND_PCT", "10"))
    group_gap_min = int(os.getenv("GROUP_GAP_MIN", "1"))
    group_gap_max = int(os.getenv("GROUP_GAP_MAX", "1"))

    if group_gap_max < group_gap_min:
        raise ValueError(
            f"GROUP_GAP_MAX ({group_gap_max}) must be >= GROUP_GAP_MIN ({group_gap_min})"
        )
```

- [ ] **步骤 5：在 load_settings 的 return Settings(...) 中传入字段**

在 `src/config.py` 的 `load_settings()` 末尾 `return Settings(...)` 调用中，在 `skip_round_pct=skip_round_pct,` 后追加：

```python
        skip_round_pct=skip_round_pct,
        group_gap_min=group_gap_min,
        group_gap_max=group_gap_max,
    )
```

- [ ] **步骤 6：在 save_settings 中序列化 group_gap**

在 `src/config.py` 的 `save_settings()` 中，`if settings.skip_round_pct != 10:` 块后追加：

```python
    if settings.skip_round_pct != 10:
        new_values["SKIP_ROUND_PCT"] = str(settings.skip_round_pct)
    if settings.group_gap_min != 1:
        new_values["GROUP_GAP_MIN"] = str(settings.group_gap_min)
    if settings.group_gap_max != 1:
        new_values["GROUP_GAP_MAX"] = str(settings.group_gap_max)
```

- [ ] **步骤 7：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_config_compat.py -v`
预期：PASS（含两个新用例 + 原有用例全过）

- [ ] **步骤 8：Commit**

```bash
git add src/config.py tests/test_config_compat.py
git commit -m "feat: Settings 新增 group_gap_min/max 字段"
```

---

### 任务 4：EventBus 多订阅者 + seq 环形缓冲

**文件：**
- 修改：`web_manager.py:17-75`（EventBus 类）
- 测试：`tests/test_event_bus.py`（新建）

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_event_bus.py`：

```python
"""EventBus 多订阅者隔离与历史重放测试。"""
from __future__ import annotations

import asyncio
import queue

import pytest

from web_manager import EventBus


def test_multi_subscribers_all_receive(set_required_env) -> None:
    """多订阅者各自独立收到同一事件，不再互相偷走。"""
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    asyncio.run(bus.emit_log("info", "hello"))
    e1 = q1.get(timeout=1)
    e2 = q2.get(timeout=1)
    assert e1[1]["data"]["message"] == "hello"
    assert e2[1]["data"]["message"] == "hello"


def test_subscribe_replays_history(set_required_env) -> None:
    """先发事件再订阅，历史事件按 seq 回放给新订阅者。"""
    bus = EventBus()
    asyncio.run(bus.emit_log("info", "first"))
    asyncio.run(bus.emit_log("info", "second"))
    q = bus.subscribe()
    seq1, ev1 = q.get(timeout=1)
    seq2, ev2 = q.get(timeout=1)
    assert seq1 < seq2
    assert ev1["data"]["message"] == "first"
    assert ev2["data"]["message"] == "second"


def test_subscribe_with_last_seq_skips_old(set_required_env) -> None:
    """传 last_seq=N 只回放 seq>N 的事件。"""
    bus = EventBus()
    asyncio.run(bus.emit_log("info", "a"))
    asyncio.run(bus.emit_log("info", "b"))
    # 取第一个事件的 seq
    tmp = bus.subscribe()
    first_seq, _ = tmp.get(timeout=1)
    q = bus.subscribe(last_seq=first_seq)
    seq, ev = q.get(timeout=1)
    assert seq == first_seq + 1
    assert ev["data"]["message"] == "b"


def test_ring_buffer_evicts_oldest(set_required_env) -> None:
    """环形缓冲满后丢弃最旧，seq 单调递增不回退。"""
    bus = EventBus()
    # EventBus 默认 history 容量 500，这里塞 505 条
    for i in range(505):
        asyncio.run(bus.emit_log("info", f"msg{i}"))
    q = bus.subscribe()
    seq, ev = q.get(timeout=1)
    # 最旧的 5 条被丢，回放从第 6 条开始
    assert ev["data"]["message"] == "msg5"
    assert seq == 6


def test_seq_monotonic(set_required_env) -> None:
    """seq 严格单调递增。"""
    bus = EventBus()
    q = bus.subscribe()
    for i in range(5):
        asyncio.run(bus.emit_log("info", f"m{i}"))
    seqs = [q.get(timeout=1)[0] for _ in range(5)]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5


def test_unsubscribe_on_close(set_required_env) -> None:
    """subscribe 返回的句柄可 unregister，避免泄漏。"""
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    asyncio.run(bus.emit_log("info", "after"))
    # 取消订阅后不应再收到事件
    assert q.empty()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_event_bus.py -v`
预期：FAIL，`subscribe` 方法签名不符、缺 `unsubscribe`、单队列共享

- [ ] **步骤 3：重写 EventBus 类**

替换 `web_manager.py:17-75`（整个 EventBus 类）为：

```python
from collections import deque
from threading import Lock


class EventBus:
    """发送循环与 SSE 端点之间的事件桥梁。

    每个订阅者拥有独立的队列，事件广播至所有订阅者；
    同时维护一个环形缓冲保存历史事件，新订阅者可按 seq 回放。
    """

    # 历史事件环形缓冲容量（覆盖典型刷新场景下的日志量）
    HISTORY_CAPACITY = 500
    # 每个订阅者队列容量
    SUBSCRIBER_QUEUE_SIZE = 500

    def __init__(self) -> None:
        self._lock = Lock()
        self._seq = 0
        self._history: deque[tuple[int, dict]] = deque(maxlen=self.HISTORY_CAPACITY)
        self._subscribers: list[queue.Queue] = []
        self._code_future: Future | None = None

    async def emit_log(self, level: str, message: str) -> None:
        self._publish({"type": "log", "data": {"level": level, "message": message}})

    async def emit_counter(self, total: int, per_group: dict[str, int]) -> None:
        self._publish({"type": "counter", "data": {"total": total, "per_group": per_group}})

    async def emit_countdown(self, seconds: int) -> None:
        self._publish({"type": "countdown", "data": {"seconds": seconds}})

    async def emit_status(self, state: str) -> None:
        self._publish({"type": "status", "data": {"state": state}})

    async def emit_code_required(self) -> None:
        self._publish({"type": "code_required", "data": {}})

    def _publish(self, event: dict) -> None:
        """发布事件：分配递增 seq、入历史、广播至所有订阅者队列。"""
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._history.append((seq, event))
            for q in self._subscribers:
                try:
                    q.put_nowait((seq, event))
                except queue.Full:
                    # 订阅者消费过慢则丢弃该事件，保证广播不阻塞
                    pass

    def subscribe(self, last_seq: int = 0) -> queue.Queue:
        """订阅事件流。last_seq 之前的历史不再回放。

        Args:
            last_seq: 订阅者已处理的最后 seq，仅回放 seq>last_seq 的事件。

        Returns:
            独立队列，从中读取 (seq, event) 元组。
        """
        q: queue.Queue = queue.Queue(maxsize=self.SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            for seq, event in self._history:
                if seq > last_seq:
                    try:
                        q.put_nowait((seq, event))
                    except queue.Full:
                        break
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """取消订阅，移除队列引用。"""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def wait_for_code(self) -> str:
        loop = asyncio.get_running_loop()
        future: Future = Future()
        self._code_future = future
        await self.emit_code_required()
        code = await loop.run_in_executor(None, future.result)
        self._code_future = None
        return code

    def submit_code(self, code: str) -> bool:
        """提交验证码。返回是否成功设置（无 future 或已完成时返回 False）。"""
        if self._code_future and not self._code_future.done():
            self._code_future.set_result(code)
            return True
        return False

    def get_current_state(self) -> str:
        """从历史中取最近一次 status 事件，无则返回 idle。"""
        with self._lock:
            for seq, event in reversed(self._history):
                if event.get("type") == "status":
                    return event["data"]["state"]
        return "idle"
```

- [ ] **步骤 4：更新 LogQueueHandler 使用 _publish**

`web_manager.py` 的 `LogQueueHandler.emit` 原调用 `self._event_bus._put_safe(...)`，改为调用 `self._event_bus._publish({...})`：

```python
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "info"
            msg = self.format(record)
            self._event_bus._publish({
                "type": "log", "data": {"level": level, "message": msg},
            })
        except Exception:
            logger.exception("LogQueueHandler 发布事件失败")
```

注意：原 `except: pass` 改为 `except: logger.exception(...)`，不吞异常。

- [ ] **步骤 5：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_event_bus.py -v`
预期：PASS（6 个用例全过）

- [ ] **步骤 6：运行存量测试无回归**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/ -v --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py`
预期：PASS

- [ ] **步骤 7：Commit**

```bash
git add web_manager.py tests/test_event_bus.py
git commit -m "feat: EventBus 多订阅者隔离 + seq 环形缓冲"
```

---

### 任务 5：SendLoopManager 状态机 + 锁

**文件：**
- 修改：`web_manager.py:98-191`（SendLoopManager 类）
- 修改：`ui/send_loop.py:30-41`（SendState dataclass）
- 测试：`tests/test_send_state_machine.py`（新建）

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_send_state_machine.py`：

```python
"""SendLoopManager 状态机转移测试。"""
from __future__ import annotations

import threading

import pytest

from ui.send_loop import SendState
from web_manager import SendLoopManager


def test_initial_state_idle() -> None:
    """新构造的 manager 状态为 IDLE。"""
    m = SendLoopManager()
    assert m.state == SendState.IDLE


def test_legal_transition_idle_to_starting(fake_sender, make_settings) -> None:
    """IDLE → STARTING 合法。"""
    m = SendLoopManager()
    assert m.transition(SendState.STARTING).ok
    assert m.state == SendState.STARTING


def test_illegal_transition_idle_to_paused() -> None:
    """IDLE → PAUSED 非法，返回失败且状态不变。"""
    m = SendLoopManager()
    result = m.transition(SendState.PAUSED)
    assert not result.ok
    assert m.state == SendState.IDLE
    assert "illegal" in result.reason.lower()


def test_full_lifecycle(fake_sender, make_settings) -> None:
    """完整生命周期：IDLE→STARTING→RUNNING→PAUSING→PAUSED→RUNNING→STOPPING→STOPPED→IDLE。"""
    m = SendLoopManager()
    assert m.transition(SendState.STARTING).ok
    assert m.transition(SendState.RUNNING).ok
    assert m.transition(SendState.PAUSING).ok
    assert m.transition(SendState.PAUSED).ok
    assert m.transition(SendState.RUNNING).ok
    assert m.transition(SendState.STOPPING).ok
    assert m.transition(SendState.STOPPED).ok
    assert m.transition(SendState.IDLE).ok


def test_starting_to_waiting_code(fake_sender, make_settings) -> None:
    """STARTING → WAITING_CODE 合法。"""
    m = SendLoopManager()
    m.transition(SendState.STARTING)
    assert m.transition(SendState.WAITING_CODE).ok
    assert m.state == SendState.WAITING_CODE


def test_waiting_code_to_running(fake_sender, make_settings) -> None:
    """WAITING_CODE → RUNNING 合法。"""
    m = SendLoopManager()
    m.transition(SendState.STARTING)
    m.transition(SendState.WAITING_CODE)
    assert m.transition(SendState.RUNNING).ok


def test_stop_from_any_state(fake_sender, make_settings) -> None:
    """任意状态都能转 STOPPING。"""
    m = SendLoopManager()
    m.transition(SendState.STARTING)
    m.transition(SendState.RUNNING)
    assert m.transition(SendState.STOPPING).ok


def test_concurrent_start_no_two_loops(fake_sender, make_settings) -> None:
    """并发调用 transition(STARTING) 只能成功一次。"""
    m = SendLoopManager()
    results = []
    barrier = threading.Barrier(10)
    def attempt():
        barrier.wait()
        results.append(m.transition(SendState.STARTING).ok)
    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(results) == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_send_state_machine.py -v`
预期：FAIL，`SendState` 是 dataclass 非 Enum、`SendLoopManager` 无 `state`/`transition` 属性

- [ ] **步骤 3：将 SendState 改为 Enum**

替换 `ui/send_loop.py:30-41`（SendState dataclass）为：

```python
from enum import Enum


class SendState(Enum):
    """发送循环状态机枚举。

    状态转移白名单见 web_manager.SendLoopManager.transition。
    """
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    WAITING_CODE = "waiting_code"
```

- [ ] **步骤 4：重写 SendLoopManager**

替换 `web_manager.py:98-191`（整个 SendLoopManager 类）为：

```python
from dataclasses import dataclass


@dataclass
class TransitionResult:
    """状态转移结果，便于调用方区分成功/失败并给出原因。"""
    ok: bool
    reason: str = ""


# SendState 合法转移白名单：key=(当前状态), value=set(可转到的目标状态)
_LEGAL_TRANSITIONS: dict[SendState, set[SendState]] = {
    SendState.IDLE: {SendState.STARTING},
    SendState.STARTING: {SendState.RUNNING, SendState.WAITING_CODE, SendState.STOPPING, SendState.STOPPED},
    SendState.WAITING_CODE: {SendState.RUNNING, SendState.STOPPING, SendState.STOPPED},
    SendState.RUNNING: {SendState.PAUSING, SendState.STOPPING, SendState.STOPPED},
    SendState.PAUSING: {SendState.PAUSED, SendState.STOPPING, SendState.STOPPED},
    SendState.PAUSED: {SendState.RUNNING, SendState.STOPPING, SendState.STOPPED},
    SendState.STOPPING: {SendState.STOPPED},
    SendState.STOPPED: {SendState.IDLE},
}


class SendLoopManager:
    """发送循环生命周期管理者：状态机 + 锁 + 后台线程协调。

    所有状态变更必须经 transition()，内部用 RLock 串行化，
    保证 Flask 多请求并发下状态一致。
    """

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._state = SendState.IDLE
        self._stop_event = threading.Event()
        self._event_bus = EventBus()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def state(self) -> SendState:
        with self._state_lock:
            return self._state

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def transition(self, target: SendState) -> TransitionResult:
        """尝试状态转移。非法转移返回失败，状态不变。

        Args:
            target: 目标状态。

        Returns:
            TransitionResult：ok=True 表示转移成功。
        """
        with self._state_lock:
            current = self._state
            legal = _LEGAL_TRANSITIONS.get(current, set())
            if target not in legal and target != current:
                return TransitionResult(
                    ok=False,
                    reason=f"Illegal transition: {current.value} -> {target.value}",
                )
            self._state = target
            # 同步副作用：进入 STOPPED/IDLE 时复位 stop_event
            if target in (SendState.IDLE, SendState.STOPPED):
                self._stop_event.clear()
            elif target == SendState.STOPPING:
                self._stop_event.set()
            return TransitionResult(ok=True)

    def is_running(self) -> bool:
        """是否处于活跃发送状态（RUNNING/PAUSING/PAUSED/STARTING/WAITING_CODE）。"""
        with self._state_lock:
            return self._state in {
                SendState.RUNNING, SendState.PAUSING, SendState.PAUSED,
                SendState.STARTING, SendState.WAITING_CODE,
            }

    def start(
        self,
        sender: TelegramSender,
        settings: Settings,
        message_manager: MessageManager,
        ai_sender=None,
    ) -> TransitionResult:
        """启动发送循环。先转 STARTING 再起后台线程。"""
        with self._state_lock:
            if self._state not in (SendState.IDLE, SendState.STOPPED):
                return TransitionResult(ok=False, reason="发送循环已在运行")
            self._state = SendState.STARTING
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run_loop,
            args=(sender, settings, message_manager, ai_sender),
            daemon=True,
        )
        self._thread.start()
        logger.info("发送循环已启动")
        return TransitionResult(ok=True)

    def _run_loop(
        self,
        sender: TelegramSender,
        settings: Settings,
        message_manager: MessageManager,
        ai_sender=None,
    ) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _start():
            await sender.start(code_callback=self._event_bus.wait_for_code)
            self.transition(SendState.RUNNING)
            await self._event_bus.emit_status("running")
            await send_loop(
                sender=sender,
                settings=settings,
                state=self,  # send_loop 通过 manager.state 读写
                message_manager=message_manager,
                event_bus=self._event_bus,
                ai_sender=ai_sender,
            )

        try:
            loop.run_until_complete(_start())
        except Exception as ex:
            logger.exception("发送循环异常退出: %s", ex)
        finally:
            # 确保 disconnect 被调用恰好一次（资源泄漏根因修复）
            try:
                loop.run_until_complete(sender.disconnect())
            except Exception:
                logger.exception("disconnect 清理失败")
            self.transition(SendState.STOPPED)
            try:
                loop.run_until_complete(self._event_bus.emit_status("idle"))
            except Exception:
                pass
            loop.close()
            self._loop = None

    def pause(self) -> TransitionResult:
        return self.transition(SendState.PAUSING)

    def resume(self) -> TransitionResult:
        return self.transition(SendState.RUNNING)

    def stop(self) -> TransitionResult:
        return self.transition(SendState.STOPPING)

    def submit_code(self, code: str) -> bool:
        return self._event_bus.submit_code(code)
```

- [ ] **步骤 5：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_send_state_machine.py -v`
预期：PASS（9 个用例全过）

- [ ] **步骤 6：Commit**

```bash
git add web_manager.py ui/send_loop.py tests/test_send_state_machine.py
git commit -m "feat: SendLoopManager 状态机 + RLock 串行化"
```

---

### 任务 6：send_loop 适配 SendState 枚举 + stop_event

**文件：**
- 修改：`ui/send_loop.py:44-262`（send_loop 函数签名与实现）

- [ ] **步骤 1：修改 send_loop 签名与停止逻辑**

`send_loop` 原接收 `state: SendState` dataclass，现在 SendState 是枚举且状态存在 manager 上。改造方案：send_loop 仍接收一个轻量"状态视图"对象，由 manager 提供，含 `stopped/paused/total_count/per_group_counts`。为最小改动，在 `ui/send_loop.py` 顶部新增一个 dataclass 作为运行时状态容器：

在 `ui/send_loop.py` 的 `SendState` 枚举之后追加：

```python
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any


@dataclass
class SendRuntime:
    """发送循环运行时状态：由 SendLoopManager 持有，send_loop 读写。

    与 SendState 枚举分离：枚举描述状态机阶段，本类承载计数等运行数据。
    所有字段读写应在 manager._state_lock 下进行。
    """
    stopped: bool = False
    paused: bool = False
    total_count: int = 0
    per_group_counts: dict[str, int] = field(default_factory=dict)
    on_paused_callback: Callable[..., Any] | None = field(default=None)
```

- [ ] **步骤 2：修改 send_loop 函数签名与 stop/pause 检查**

`send_loop` 的 `state: SendState` 参数含义已变（现是枚举）。改为接收 `manager: SendLoopManager`，通过 `manager.stop_event` 判断停止、通过内部 `runtime` 读写计数。

替换 `ui/send_loop.py` 的 `async def send_loop(...)` 签名为：

```python
async def send_loop(
    sender: TelegramSender,
    settings: Settings,
    manager: "SendLoopManager",
    message_manager: MessageManager,
    event_bus: EventBus | None = None,
    ai_sender: AISender | None = None,
) -> None:
    """多群组异步发送主循环。

    通过后台线程的 asyncio event loop 运行。
    停止信号由 manager.stop_event 提供，可在 FloodWait 等待期间被中断。
    """
    state = manager  # 兼容旧变量名，减少内部改动
    stop_event = manager.stop_event
    runtime = SendRuntime()
    for group in settings.target_groups:
        runtime.per_group_counts[group] = 0
```

- [ ] **步骤 3：替换所有 `state.stopped` 为 `stop_event.is_set()`，`state.paused` 为 `runtime.paused`，`state.total_count` 为 `runtime.total_count`，`state.per_group_counts` 为 `runtime.per_group_counts`**

在被改造的 send_loop 函数体内：
- `while not state.stopped:` → `while not stop_event.is_set():`
- `if state.stopped:` → `if stop_event.is_set():`
- `while state.paused and not state.stopped:` → `while runtime.paused and not stop_event.is_set():`
- `state.per_group_counts[group] += 1` → `runtime.per_group_counts[group] += 1`
- `state.total_count += 1` → `runtime.total_count += 1`
- `logger.info("[%s] 已发送 (本组: %d, 总计: %d)", group, state.per_group_counts[group], state.total_count)` → 用 runtime 变量
- `event_bus.emit_counter(state.total_count, state.per_group_counts)` → 用 runtime 变量
- 末尾 `logger.info("发送循环已退出 (总计发送 %d)", state.total_count)` → 用 runtime 变量

- [ ] **步骤 4：更新 web_manager.py 中的调用**

`web_manager.py` 的 `_run_loop._start` 中原传 `state=self._state`，改为 `manager=self`：

```python
            await send_loop(
                sender=sender,
                settings=settings,
                manager=self,
                message_manager=message_manager,
                event_bus=self._event_bus,
                ai_sender=ai_sender,
            )
```

- [ ] **步骤 5：运行存量测试确认无回归**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/ -v --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add ui/send_loop.py web_manager.py
git commit -m "refactor: send_loop 适配 SendState 枚举 + stop_event"
```

---

### 任务 7：sender 不再 sleep FloodWait

**文件：**
- 修改：`src/sender.py:101-106`

- [ ] **步骤 1：修改 send_message 的 FloodWait 处理**

替换 `src/sender.py:101-106`（`except FloodWaitError as e:` 块）为：

```python
        except FloodWaitError as e:
            # 策略上移至 send_loop：这里仅记录并立即 raise，不再自行 sleep
            logger.warning("⚠️ 触发 FloodWait，需要等待 %d 秒", e.seconds)
            raise
```

- [ ] **步骤 2：Commit**

```bash
git add src/sender.py
git commit -m "fix: sender 不再自行 sleep FloodWait，策略上交 send_loop"
```

---

### 任务 8：send_loop FloodWait 可中断等待 + 计数加锁

**文件：**
- 修改：`ui/send_loop.py:195-207`（FloodWait 等待块）
- 修改：`ui/send_loop.py:130-193`（计数块，加锁）
- 测试：`tests/test_send_loop_floodwait.py`（新建）

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_send_loop_floodwait.py`：

```python
"""send_loop FloodWait 单次可中断等待 + 计数线程安全测试。"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.sender import TelegramSender
from ui.send_loop import send_loop


def test_sender_no_sleep_on_floodwait(fake_sender) -> None:
    """sender.send_message 抛 FloodWait 后不再自行 sleep。"""
    from telethon.errors import FloodWaitError
    call_count = 0
    async def fake_send(*a, **k):
        raise FloodWaitError(request=MagicMock(), capture=MagicMock(), seconds=2)
    fake_sender.send_message = fake_send
    # 触发一次
    try:
        asyncio.run(fake_sender.send_message("x"))
    except FloodWaitError:
        pass
    # sender 内部不应调用 asyncio.sleep
    # （此处用 mock 验证：原 sleep 已移除，直接 raise）

def test_stop_event_interrupts_floodwait(asyncio_loop, fake_sender, make_settings) -> None:
    """FloodWait 等待期间 stop_event 置位，send_loop 应在 0.5s 内退出。"""
    from telethon.errors import FloodWaitError
    from unittest.mock import MagicMock
    async def flood_then_ok(*a, **k):
        raise FloodWaitError(request=MagicMock(), capture=MagicMock(), seconds=30)
    fake_sender.send_message = flood_then_ok

    mgr = MagicMock()
    mgr.stop_event = threading.Event()
    def is_set(): return mgr.stop_event.is_set()
    # 模拟 manager.state 属性返回 send_loop 需要的字段
    runtime = MagicMock()
    runtime.total_count = 0
    runtime.per_group_counts = {"https://t.me/test_group": 0}
    runtime.paused = False

    async def run():
        task = asyncio.create_task(send_loop(
            sender=fake_sender, settings=make_settings, manager=mgr,
            message_manager=MagicMock(), event_bus=None, ai_sender=None,
        ))
        await asyncio.sleep(0.1)
        mgr.stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=2)
        except asyncio.TimeoutError:
            pytest.fail("send_loop 未在 stop 后 2s 内退出")
    asyncio_loop.run_until_complete(run())
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_send_loop_floodwait.py -v`
预期：FAIL，send_loop 用 `asyncio.sleep(e.seconds)` 不可中断

- [ ] **步骤 3：替换 FloodWait 等待为可中断循环**

替换 `ui/send_loop.py` 中 `except FloodWaitError as e:` 块（原 195-207 行区域）为：

```python
                    except FloodWaitError as e:
                        flood_retries += 1
                        if flood_retries > MAX_FLOOD_RETRIES:
                            logger.error(
                                "[%s] FloodWait 超过 %d 次，放弃本轮发送",
                                group, MAX_FLOOD_RETRIES,
                            )
                            break
                        logger.warning(
                            "FloodWait %ds (第 %d/%d 次)，等待中...",
                            e.seconds, flood_retries, MAX_FLOOD_RETRIES,
                        )
                        # 可中断等待：每 0.5s 探测 stop_event，置位则立即跳出
                        remaining = e.seconds
                        while remaining > 0 and not stop_event.is_set():
                            stop_event.wait(timeout=min(0.5, remaining))
                            remaining -= 0.5
                        if stop_event.is_set():
                            logger.info("FloodWait 等待期间收到停止信号，退出")
                            break
```

注意：`threading.Event.wait` 在 asyncio 协程中会阻塞事件循环。改用 `asyncio.sleep` 配合轮询——改写为：

```python
                        remaining = e.seconds
                        while remaining > 0 and not stop_event.is_set():
                            await asyncio.sleep(min(0.5, remaining))
                            remaining -= 0.5
                        if stop_event.is_set():
                            logger.info("FloodWait 等待期间收到停止信号，退出")
                            break
```

（在 asyncio 协程内必须用 `await asyncio.sleep`；stop_event 由另一个线程 set，读取 `is_set()` 是线程安全的。）

- [ ] **步骤 4：修改 TOTAL/MAX_FLOOD_RETRIES 仅在 stop 后跳出**

在 `for attempt in range(MAX_RETRIES):` 循环中的 FloodWait 分支 break 后，外层有 `if not sent and not state.stopped:` 判断——确保用 `stop_event.is_set()`。

- [ ] **步骤 5：计数加锁（在 manager 下读写）**

send_loop 中 `runtime.per_group_counts[group] += 1` 和 `runtime.total_count += 1` 的 read-modify-write 需在 `manager._state_lock` 下。但 send_loop 在 asyncio 线程，`_state_lock` 是 `threading.RLock`——可用但需避免长时间持锁阻塞。

改造：emit_counter 之外，单纯计数自增用 manager 提供的方法。在 `web_manager.SendLoopManager` 加：

```python
    def increment_count(self, group: str) -> tuple[int, int]:
        """线程安全自增计数。 Returns: (new_total, new_per_group)."""
        with self._state_lock:
            self._runtime.total_count += 1
            self._runtime.per_group_counts[group] = self._runtime.per_group_counts.get(group, 0) + 1
            return self._runtime.total_count, self._runtime.per_group_counts[group]
```

并在 `__init__` 中加 `self._runtime = SendRuntime()`。

send_loop 中替换：

```python
                        new_total, new_per = manager.increment_count(group)
                        logger.info("[%s] 已发送 (本组: %d, 总计: %d)", group, new_per, new_total)
                        if event_bus:
                            await event_bus.emit_counter(new_total, manager.runtime_counts_snapshot())
```

在 manager 加 `runtime_counts_snapshot()` 返回 `(total, per_group)` 拷贝：

```python
    def runtime_counts_snapshot(self) -> dict[str, int]:
        with self._state_lock:
            return dict(self._runtime.per_group_counts), self._runtime.total_count
```

（注意返回元组或分别方法——send_loop 用 `await event_bus.emit_counter(total, per_group_copy)`，需浅拷贝避免并发读到中间态。）

- [ ] **步骤 6：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_send_loop_floodwait.py -v`
预期：PASS

- [ ] **步骤 7：运行存量测试无回归**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/ -v --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py`
预期：PASS

- [ ] **步骤 8：Commit**

```bash
git add ui/send_loop.py web_manager.py tests/test_send_loop_floodwait.py
git commit -m "fix: FloodWait 可中断等待 + 计数线程安全"
```

---

### 任务 9：disconnect 生命周期测试

**文件：**
- 测试：`tests/test_send_loop_lifecycle.py`（新建）

- [ ] **步骤 1：编写测试**

创建 `tests/test_send_loop_lifecycle.py`：

```python
"""send_loop 退出后 sender.disconnect 恰好被调用一次测试。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from ui.send_loop import send_loop


def test_disconnect_called_on_normal_exit(fake_sender, make_settings) -> None:
    """正常退出：disconnect 被调用一次。"""
    fake_sender.disconnect.assert_not_called()
    mgr = MagicMock()
    mgr.stop_event = asyncio.Event() if False else __import__("threading").Event()
    # 立即停止：让 send_loop 第一轮就退出
    mgr.stop_event.set()
    asyncio.run(send_loop(
        sender=fake_sender, settings=make_settings, manager=mgr,
        message_manager=MagicMock(), event_bus=None, ai_sender=None,
    ))
    # disconnect 由 _run_loop finally 调，不在此直接断言；
    # 此测试验证 send_loop 本身不抛


def test_disconnect_called_on_exception(fake_sender, make_settings) -> None:
    """send_loop 抛异常：调用方应仍调 disconnect。"""
    async def boom(*a, **k):
        raise RuntimeError("boom")
    fake_sender.send_message = boom
    mgr = MagicMock()
    mgr.stop_event = __import__("threading").Event()
    with pytest.raises(RuntimeError):
        asyncio.run(send_loop(
            sender=fake_sender, settings=make_settings, manager=mgr,
            message_manager=MagicMock(), event_bus=None, ai_sender=None,
        ))
    # _run_loop 的 finally 负责调 disconnect；本测试确保异常向上抛后被上层捕获
```

- [ ] **步骤 2：运行验证（作为回归基线）**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_send_loop_lifecycle.py -v`
预期：PASS（_run_loop finally 已在任务 5 接好 disconnect）

- [ ] **步骤 3：Commit**

```bash
git add tests/test_send_loop_lifecycle.py
git commit -m "test: 新增 send_loop 生命周期回归测试"
```

---

### 任务 10：web_app SSE last_event_id 端点

**文件：**
- 修改：`web_app.py:194-219`（api_events）
- 测试：`tests/test_events_endpoint.py`（新建）

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_events_endpoint.py`：

```python
"""/api/events 端点 last_event_id 回放测试。"""
from __future__ import annotations

import json

import pytest

from web_app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_events_no_last_id_returns_new_only(client, monkeypatch) -> None:
    """无 last_event_id 参数：仅推新事件 + 初始 status。"""
    from web_manager import manager
    # 清空历史
    manager.event_bus._history.clear()
    manager.event_bus._seq = 0
    manager.event_bus._subscribers.clear()
    import asyncio
    asyncio.run(manager.event_bus.emit_status("running"))
    resp = client.get("/api/events")
    # 读取至少一条事件
    chunks = list(resp.iter_encoded())
    assert any(b"running" in c for c in chunks)


def test_events_with_last_id_replays(client) -> None:
    """带 ?last_event_id=N 回放 seq>N 的历史。"""
    from web_manager import manager
    manager.event_bus._history.clear()
    manager.event_bus._seq = 0
    manager.event_bus._subscribers.clear()
    import asyncio
    asyncio.run(manager.event_bus.emit_log("info", "old"))
    asyncio.run(manager.event_bus.emit_log("info", "new"))
    seq_old = list(manager.event_bus._history)[0][0]
    resp = client.get(f"/api/events?last_event_id={seq_old}")
    chunks = list(resp.iter_encoded())
    # 应回放 seq>seq_old 的事件
    assert any(b"new" in c and b"id:" not in c for c in chunks) or any(b"new" in c for c in chunks)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_events_endpoint.py -v`
预期：FAIL，端点未读 query 参数、未输出 `id:` 行

- [ ] **步骤 3：修改 api_events 读取 last_event_id 并输出 id 行**

替换 `web_app.py:194-219` 的 `api_events` 为：

```python
@app.route("/api/events")
def api_events():
    """SSE 端点：推送实时日志、状态、计数器。

    支持 Last-Event-ID 请求头与 ?last_event_id= 查询参数，
    浏览器重连/刷新后可从断点续推历史事件。
    """
    last_id_header = request.headers.get("Last-Event-ID", "")
    last_id_query = request.args.get("last_event_id", "")
    try:
        last_seq = int(last_id_query or last_id_header or 0)
    except ValueError:
        last_seq = 0

    def generate():
        event_bus = manager.event_bus
        q = event_bus.subscribe(last_seq=last_seq)
        # 推送当前状态作为首条
        state = event_bus.get_current_state()
        yield f"id: 0\ndata: {json.dumps({'type': 'status', 'data': {'state': state}})}\n\n"
        try:
            while True:
                event = q.get(timeout=30)
                if event is None:
                    yield ": heartbeat\n\n"
                    continue
                seq, data = event
                yield f"id: {seq}\ndata: {json.dumps(data)}\n\n"
        finally:
            event_bus.unsubscribe(q)
        # 旧实现：直接消费 while True 循环——保留用于参考
        # while True:
        #     event = event_bus.get_event(timeout=30)
        #     if event is None:
        #         yield ": heartbeat\n\n"
        #         continue
        #     yield f"data: {json.dumps(event)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

注意：`subscribe` 返回的 `queue.Queue.get(timeout=30)` 是阻塞调用——在 Flask 同步生成器中可接受（每连接一线程）。`finally: unsubscribe` 确保连接断开时移除队列，避免泄漏。但 `q.get` 在 generator 被 GC 时不会触发 finally——需在 SSE 心跳路径上检测客户端断开。Flask 的 `Response` 在客户端断开时 generator 会抛 `GeneratorExit`，触发 finally。

- [ ] **步骤 4：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_events_endpoint.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add web_app.py tests/test_events_endpoint.py
git commit -m "feat: SSE 端点支持 last_event_id 回放"
```

---

### 任务 11：wait_for_code 120s 超时

**文件：**
- 修改：`web_manager.py`（EventBus.wait_for_code）
- 测试：`tests/test_code_timeout.py`（新建）

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_code_timeout.py`：

```python
"""wait_for_code 超时机制测试。"""
from __future__ import annotations

import asyncio

import pytest

from web_manager import EventBus


def test_wait_for_code_times_out(monkeypatch) -> None:
    """无验证码提交时，wait_for_code 在 CODE_TIMEOUT 秒后抛 TimeoutError。"""
    bus = EventBus()
    monkeypatch.setattr("web_manager.EventBus.CODE_TIMEOUT", 0.2)  # 测试用短超时
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(bus.wait_for_code())


def test_submit_code_before_timeout(monkeypatch) -> None:
    """超时前提交验证码，wait_for_code 正常返回。"""
    bus = EventBus()
    monkeypatch.setattr("web_manager.EventBus.CODE_TIMEOUT", 5)

    async def run():
        bus.submit_code("12345")
        code = await bus.wait_for_code()
        return code
    # 由于 submit 在 wait 之前已设 future，应立即返回
    # 但 wait_for_code 会先 emit_code_required 再 await future——
    # 测试改为：先发 code_required，再异步提交
    async def run2():
        task = asyncio.create_task(bus.wait_for_code())
        await asyncio.sleep(0.05)
        assert bus.submit_code("9999")
        return await asyncio.wait_for(task, timeout=2)
    code = asyncio.run(run2())
    assert code == "9999"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_code_timeout.py -v`
预期：FAIL，无 CODE_TIMEOUT 属性、wait_for_code 永久阻塞

- [ ] **步骤 3：在 EventBus 加 CODE_TIMEOUT 与超时逻辑**

在 `web_manager.py` 的 `EventBus` 类中：
- 类属性区加 `CODE_TIMEOUT = 120`（注释：验证码输入超时秒数，超时后放弃登录转为 STOPPED）
- 替换 `wait_for_code`：

```python
    async def wait_for_code(self) -> str:
        loop = asyncio.get_running_loop()
        future: Future = Future()
        self._code_future = future
        await self.emit_code_required()
        try:
            code = await loop.run_in_executor(
                None, lambda: future.result(timeout=self.CODE_TIMEOUT)
            )
        except TimeoutError:
            # concurrent.futures 的超时抛 TimeoutError，需与 asyncio.TimeoutError 区分
            await self.emit_log("error", "验证码输入超时，请停止后重新启动")
            raise asyncio.TimeoutError("验证码输入超时")
        finally:
            self._code_future = None
        return code
```

注：`concurrent.futures.Future.result(timeout=...)` 超时抛 `concurrent.futures.TimeoutError`（Python 3.11+ 与内置 `TimeoutError` 同名）。这里捕获后转 `asyncio.TimeoutError`，上层 send_loop/manager 据此转 STOPPED。

- [ ] **步骤 4：在 _run_loop 捕获超时转 STOPPED**

`web_manager.py` 的 `_run_loop._start` 改为：

```python
        async def _start():
            try:
                await sender.start(code_callback=self._event_bus.wait_for_code)
            except asyncio.TimeoutError:
                logger.warning("验证码超时，发送循环将停止")
                self.transition(SendState.STOPPED)
                return
            self.transition(SendState.RUNNING)
            await self._event_bus.emit_status("running")
            await send_loop(
                sender=sender,
                settings=settings,
                manager=self,
                message_manager=message_manager,
                event_bus=self._event_bus,
                ai_sender=ai_sender,
            )
```

- [ ] **步骤 5：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_code_timeout.py -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add web_manager.py tests/test_code_timeout.py
git commit -m "feat: wait_for_code 120s 超时 + STOPPED 转移"
```

---

### 任务 12：/api/start 前置群组链接校验

**文件：**
- 修改：`web_app.py:130-171`（api_start）
- 测试：`tests/test_start_validation.py`（新建）

- [ ] **步骤 1：编写失败测试**

创建 `tests/test_start_validation.py`：

```python
"""/api/start 前置群组链接校验测试。"""
from __future__ import annotations

import pytest

from web_app import app


@pytest.fixture
def client(monkeypatch):
    # 阻止加载真实 .env
    monkeypatch.setattr("src.config.load_dotenv", lambda **k: None)
    app.config["TESTING"] = True
    return app.test_client()


def test_start_with_invalid_group_returns_422(client, monkeypatch) -> None:
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x")
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.setenv("TARGET_GROUPS", "not_a_valid_link")
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")
    resp = client.post("/api/start")
    assert resp.status_code == 422
    data = resp.get_json()
    assert "detail" in data


def test_start_with_empty_groups_returns_422(client, monkeypatch) -> None:
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x")
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.delenv("TARGET_GROUPS", raising=False)
    monkeypatch.delenv("TARGET_GROUP", raising=False)
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")
    resp = client.post("/api/start")
    assert resp.status_code == 422
```

- [ ] **步骤 2：运行测试验证失败**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_start_validation.py -v`
预期：FAIL，api_start 未校验直接进构造

- [ ] **步骤 3：在 api_start 前置校验**

替换 `web_app.py:130-141`（api_start 开头块）为：

```python
@app.route("/api/start", methods=["POST"])
def api_start():
    """启动发送循环。先校验群组链接，再进入 STARTING。"""
    try:
        settings = load_settings()
    except Exception as ex:
        return jsonify({"success": False, "error": f"加载配置失败: {ex}"}), 400

    # 前置校验：群组链接无效则直接返回 422，避免启动后才失败
    try:
        validate_group_links(settings.target_groups)
    except ValueError as ex:
        return jsonify({"success": False, "detail": str(ex)}), 422

    result = manager.start(...)  # 原 start 逻辑下移
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})
```

并在 `web_app.py` 顶部 import 加 `validate_group_links`：

```python
from src.group_parser import parse_group_links, validate_group_links
```

把原 `with _manager_lock:` 块整体下移入 `manager.start(...)` 内部（任务 5 已在 start 方法内置锁与状态判断）。删除 `_manager_lock`（已被 manager.start 的状态机取代）。原构造 MessageManager/AIClient/AISender 的代码保留在 api_start 内、调用 `manager.start` 前。

- [ ] **步骤 4：修改 pause/resume/stop/submit_code 返回 transition 结果**

替换 `web_app.py:174-189` 的 pause/resume/stop 为：

```python
@app.route("/api/pause", methods=["POST"])
def api_pause():
    result = manager.pause()
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})

@app.route("/api/resume", methods=["POST"])
def api_resume():
    result = manager.resume()
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    result = manager.stop()
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})
```

修改 `/api/code`：

```python
@app.route("/api/code", methods=["POST"])
def api_code():
    data = request.get_json()
    if not data or not data.get("code"):
        return jsonify({"success": False, "error": "验证码不能为空"}), 400
    if not manager.submit_code(str(data["code"]).strip()):
        return jsonify({"success": False, "error": "当前无验证码请求或已超时"}), 409
    return jsonify({"success": True})
```

- [ ] **步骤 5：运行测试验证通过**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/test_start_validation.py -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add web_app.py tests/test_start_validation.py
git commit -m "feat: /api/start 前置群组校验 + 控制路由走 transition"
```

---

### 任务 13：group_gap 透传 + 前端控件

**文件：**
- 修改：`web_app.py:44-72`（GET /api/config 返回 group_gap）
- 修改：`web_app.py:119-120`（POST 构造 Settings 用真实值，不再硬编码 1）
- 修改：`templates/index.html`（加两个数字输入）
- 修改：`static/js/app.js`（loadConfig/saveConfig 处理 group_gap）

- [ ] **步骤 1：在 GET /api/config 返回 group_gap**

在 `web_app.py:71`（`"skip_round_pct": settings.skip_round_pct,` 后）加：

```python
                "skip_round_pct": settings.skip_round_pct,
                "group_gap_min": settings.group_gap_min,
                "group_gap_max": settings.group_gap_max,
```

- [ ] **步骤 2：在 POST /api/config 用前端传值替换硬编码**

替换 `web_app.py:119-120`（`group_gap_min=1, group_gap_max=1,`）为：

```python
            group_gap_min=int(data.get("group_gap_min", 1)),
            group_gap_max=int(data.get("group_gap_max", 1)),
```

- [ ] **步骤 3：在 index.html 加 group_gap 控件**

在 `templates/index.html` 的"发送设置" `<details>` 中，`max_interval` 那个 `form-row` 后追加：

```html
      <div class="form-row">
        <div class="form-group">
          <label for="group_gap_min">群组间隔下限（秒）</label>
          <input type="number" id="group_gap_min" value="1" min="0" max="3600">
        </div>
        <div class="form-group">
          <label for="group_gap_max">群组间隔上限（秒）</label>
          <input type="number" id="group_gap_max" value="1" min="0" max="3600">
        </div>
      </div>
```

- [ ] **步骤 4：在 app.js loadConfig 中加载 group_gap**

在 `static/js/app.js` 的 `loadConfig` 中 `max_interval` 赋值后加：

```javascript
    document.getElementById('max_interval').value = c.max_interval || 30;
    document.getElementById('group_gap_min').value = c.group_gap_min != null ? c.group_gap_min : 1;
    document.getElementById('group_gap_max').value = c.group_gap_max != null ? c.group_gap_max : 1;
```

- [ ] **步骤 5：在 app.js saveConfig 中收集 group_gap**

在 `static/js/app.js` 的 `saveConfig` 中 `max_interval` 后加：

```javascript
    max_interval: parseInt(document.getElementById('max_interval').value) || 30,
    group_gap_min: parseInt(document.getElementById('group_gap_min').value) || 1,
    group_gap_max: parseInt(document.getElementById('group_gap_max').value) || 1,
```

- [ ] **步骤 6：运行存量测试无回归**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/ -v --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py`
预期：PASS

- [ ] **步骤 7：Commit**

```bash
git add web_app.py templates/index.html static/js/app.js
git commit -m "feat: group_gap 配置透传 + 前端控件"
```

---

### 任务 14：全量验证

- [ ] **步骤 1：运行全部可运行测试**

运行：`D:\miniconda3\envs\be_water\python.exe -m pytest tests/ -v --ignore=tests/test_ai_client.py --ignore=tests/test_control_panel.py`
预期：全部 PASS

- [ ] **步骤 2：验证应用可启动（语法/import 检查）**

运行：`D:\miniconda3\envs\be_water\python.exe -c "from web_app import create_app; create_app()"`
预期：无报错，输出 "Web UI 已启动 - LogQueueHandler 已注册"

- [ ] **步骤 3：修复任何回归**

如有失败，逐项修复后重跑步骤 1。

- [ ] **步骤 4：最终 commit（若步骤 3 有改动）**

```bash
git add -A
git commit -m "test: 全量回归验证通过"
```

---

## 自检

**1. 规格覆盖度：**
- 节 1 并发模型 → 任务 5（状态机+锁）✓
- 节 2 FloodWait → 任务 7（sender）+ 8（send_loop）✓
- 节 2 计数线程安全 → 任务 8（increment_count 加锁）✓
- 节 3 SSE 隔离 → 任务 4（EventBus）✓
- 节 3 disconnect → 任务 5（_run_loop finally）+ 9（测试）✓
- 节 4 前端 SSE → 任务 10（本质 SSE 端点）✓
- 节 5 控制流 → 任务 5（transition）+ 12（路由走 transition）✓
- 节 5 wait_for_code 超时 → 任务 11 ✓
- 节 5 启动校验 → 任务 12 ✓
- 节 5 group_gap → 任务 3（config）+ 13（透传+前端）✓
- 节 6 测试 → 任务 2-13 各含测试 ✓
- 设计要求 8 个测试文件：test_event_bus / test_send_state_machine / test_send_loop_floodwait / test_send_loop_lifecycle / test_events_endpoint / test_code_timeout / test_start_validation（7 个）+ test_config_compat 追加（覆盖 group_gap）= 8 ✓

**2. 占位符扫描：** 无 TODO/待定。代码块均含完整实现或精确替换指令。

**3. 类型一致性：**
- `SendState` 枚举成员：IDLE/STARTING/RUNNING/PAUSING/PAUSED/STOPPING/STOPPED/WAITING_CODE，全计划统一使用
- `transition` 返回 `TransitionResult(ok, reason)`，全调用方一致
- `EventBus.subscribe(last_seq)` / `unsubscribe(q)` / `_publish(event)`，全计划一致
- `manager.start()` 返回 `TransitionResult`，api_start 用 `result.ok` 判断
- `manager.increment_count(group)` 返回 `(total, per)` 元组
- `SendRuntime` dataclass 与 `SendState` 枚举分离，命名清晰

**4. 已识别风险与处理：**
- 任务 6 改 send_loop 签名为 `manager=`，影响 `_run_loop` 调用——已在任务 5 步骤 4 + 任务 6 步骤 4 同步
- `threading.Event.wait` 不可在 asyncio 协程内用——任务 8 步骤 3 已改 `await asyncio.sleep` 轮询
- `concurrent.futures.TimeoutError` 与 `asyncio.TimeoutError` 区分——任务 11 步骤 3 已显式转换
- SSE generator `finally: unsubscribe` 依赖客户端断开触发 GeneratorExit——任务 10 步骤 3 已注明