from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections import deque
from concurrent.futures import Future
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


class SendLoopManager:
    def __init__(self) -> None:
        self._event_bus = EventBus()
        self._state = SendState()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def send_state(self) -> SendState:
        return self._state

    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        sender: TelegramSender,
        settings: Settings,
        message_manager: MessageManager,
        ai_sender=None,
    ) -> None:
        if self._running:
            logger.warning("发送循环已在运行")
            return

        self._state.stopped = False
        self._state.paused = False
        self._state.total_count = 0
        self._state.per_group_counts.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._run_loop,
            args=(sender, settings, message_manager, ai_sender),
            daemon=True,
        )
        self._thread.start()
        logger.info("发送循环已启动")

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
            await self._event_bus.emit_status("running")
            await send_loop(
                sender=sender,
                settings=settings,
                state=self._state,
                message_manager=message_manager,
                event_bus=self._event_bus,
                ai_sender=ai_sender,
            )

        try:
            loop.run_until_complete(_start())
        except Exception as ex:
            logger.exception("发送循环异常退出: %s", ex)
        finally:
            self._running = False
            try:
                loop.run_until_complete(self._event_bus.emit_status("idle"))
            except Exception:
                pass
            loop.close()
            self._loop = None

    def pause(self) -> None:
        self._state.paused = True
        logger.info("发送循环暂停中...")

    def resume(self) -> None:
        self._state.paused = False
        logger.info("发送循环已恢复")

    def stop(self) -> None:
        self._state.stopped = True
        logger.info("发送循环停止中...")

    def submit_code(self, code: str) -> None:
        self._event_bus.submit_code(code)
