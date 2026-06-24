from __future__ import annotations

import asyncio
import logging
import queue
import threading
from concurrent.futures import Future

from src.config import Settings
from src.sender import TelegramSender
from ui.message_manager import MessageManager
from ui.send_loop import SendState, send_loop

logger = logging.getLogger(__name__)


class EventBus:
    """发送循环和 SSE 端点之间的事件桥梁。

    运行在后台 asyncio 线程中，通过线程安全的 queue.Queue
    将事件传递给 Flask 的 SSE 端点。
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=500)
        self._code_future: Future | None = None
        self._state = "idle"

    async def emit_log(self, level: str, message: str) -> None:
        self._put_safe({"type": "log", "data": {"level": level, "message": message}})

    async def emit_counter(self, total: int, per_group: dict[str, int]) -> None:
        self._put_safe({"type": "counter", "data": {"total": total, "per_group": per_group}})

    async def emit_countdown(self, seconds: int) -> None:
        self._put_safe({"type": "countdown", "data": {"seconds": seconds}})

    async def emit_status(self, state: str) -> None:
        self._state = state
        self._put_safe({"type": "status", "data": {"state": state}})

    async def emit_code_required(self) -> None:
        self._put_safe({"type": "code_required", "data": {}})

    async def wait_for_code(self) -> str:
        loop = asyncio.get_running_loop()
        future: Future = Future()
        self._code_future = future
        await self.emit_code_required()
        code = await loop.run_in_executor(None, future.result)
        self._code_future = None
        return code

    def submit_code(self, code: str) -> None:
        if self._code_future and not self._code_future.done():
            self._code_future.set_result(code)

    def get_event(self, timeout: float = 30) -> dict | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_current_state(self) -> str:
        return self._state

    def _put_safe(self, event: dict) -> None:
        try:
            self._queue.put(event, timeout=1)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put(event, timeout=1)
            except queue.Full:
                pass


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
            self._event_bus._put_safe({
                "type": "log", "data": {"level": level, "message": msg},
            })
        except Exception:
            pass


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
