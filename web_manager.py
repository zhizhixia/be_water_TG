from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock

from src.config import Settings
from src.sender import TelegramSender
from ui.message_manager import MessageManager
from ui.send_loop import SendState, send_loop

logger = logging.getLogger(__name__)


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


class LogQueueHandler(logging.Handler):
    def __init__(self, event_bus: EventBus) -> None:
        super().__init__()
        self._event_bus = event_bus
        self.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "info"
            msg = self.format(record)
            self._event_bus._publish({
                "type": "log", "data": {"level": level, "message": msg},
            })
        except Exception:
            logger.exception("LogQueueHandler 发布事件失败")


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
            if target not in legal:
                # 自环（target == current）也按非法处理，保证并发重复
                # 调用 transition(STARTING) 只成功一次（test_concurrent_start_no_two_loops）。
                # 如需对 STOPPING/PAUSED 等做幂等容忍，可后续在白名单显式加入自环。
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
                manager=self,  # send_loop 通过 manager 读写状态（任务 6 接通）
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
