from __future__ import annotations

import asyncio
import logging
import queue
import re
import sys
import threading
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock
from typing import Any, Mapping

from src.config import Settings
from src.lottery_integration import LotteryDashboardService, LotteryEventBus
from src.sender import TelegramSender
from src.state_store import StateStore
from ui.message_manager import MessageManager
from ui.send_loop import SendRuntime, SendState, send_loop

logger = logging.getLogger(__name__)


_SSE_SENSITIVE_FIELD_PARTS = (
    "api_key",
    "api-key",
    "apikey",
    "token",
    "password",
    "callback",
    "phone",
)
_SSE_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?P<field>api[_ -]?key|token|password|callback(?:[_ -]?url)?|phone)"
    r"(?P<separator>\s*[:=]\s*)(?P<value>[^\s,;，；]+)",
    re.IGNORECASE,
)
_SSE_PHONE_NUMBER = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_SSE_REDACTED_VALUE = "[已脱敏]"


def _redact_sse_text(value: str) -> str:
    """替换文本中以字段赋值形式出现的敏感值和手机号。"""
    value = _SSE_SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('field')}{match.group('separator')}{_SSE_REDACTED_VALUE}",
        value,
    )
    return _SSE_PHONE_NUMBER.sub(_SSE_REDACTED_VALUE, value)


def _redact_sse_value(value: Any) -> Any:
    """递归移除 SSE 载荷中的敏感字段，并脱敏自由文本。"""
    if isinstance(value, Mapping):
        return {
            key: _redact_sse_value(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in _SSE_SENSITIVE_FIELD_PARTS)
        }
    if isinstance(value, list):
        return [_redact_sse_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sse_value(item) for item in value)
    if isinstance(value, str):
        return _redact_sse_text(value)
    return value


class EventBus:
    """发送循环与 SSE 端点之间的事件桥梁。

    每个订阅者拥有独立的队列，事件广播至所有订阅者；
    同时维护一个环形缓冲保存历史事件，新订阅者可按 seq 回放。
    """

    # 历史事件环形缓冲容量（覆盖典型刷新场景下的日志量）
    HISTORY_CAPACITY = 500
    # 每个订阅者队列容量
    SUBSCRIBER_QUEUE_SIZE = 500
    # 验证码输入超时秒数——超时后放弃登录并转 STOPPED
    CODE_TIMEOUT = 120

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

    async def emit_group_state(self, state: dict) -> None:
        self._publish({"type": "group_state", "data": state})

    async def emit_decision(self, group: str, action: str, reason: str) -> None:
        self._publish(
            {
                "type": "decision",
                "data": {"group": group, "action": action, "reason": reason},
            }
        )

    async def emit_alert(self, group: str, code: str, message: str) -> None:
        self._publish(
            {
                "type": "alert",
                "data": {"group": group, "code": code, "message": message},
            }
        )

    async def emit_health(self, state: str, message: str = "") -> None:
        self._publish({"type": "health", "data": {"state": state, "message": message}})

    def _publish(self, event: dict) -> None:
        """脱敏后发布事件：分配递增 seq、入历史、广播至所有订阅者队列。"""
        event = _redact_sse_value(event)
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
        try:
            code = await loop.run_in_executor(
                None, lambda: future.result(timeout=self.CODE_TIMEOUT)
            )
        except TimeoutError:
            # concurrent.futures.Future.result 超时抛 TimeoutError（Python 3.13 中等同于 asyncio.TimeoutError）
            # 转换为 asyncio.TimeoutError 以便上层统一处理
            await self.emit_log("error", "验证码输入超时，请停止后重新启动")
            raise asyncio.TimeoutError("验证码输入超时")
        finally:
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
        except Exception as e:
            # 注意：不走 logger.exception/print 经 logging 路由——LogQueueHandler 已挂载到
            # root logger，再次 print 触发的 emit 会无限递归。直接写 stderr 保证安全。
            print(f"LogQueueHandler 发布事件失败: {e}", file=sys.stderr)


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
    SendState.STOPPED: {SendState.IDLE, SendState.STARTING},
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
        self._runtime = SendRuntime()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._state_store: StateStore | None = None
        self._active_settings: Settings | None = None
        self.lottery_dashboard = LotteryDashboardService()
        self._lottery_events = LotteryEventBus(lambda: None)

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

    def increment_count(
        self, group: str, persisted_count: int | None = None
    ) -> tuple[int, dict[str, int]]:
        """线程安全自增计数。

        Args:
            group: 目标群组。

        Returns:
            (new_total, new_per_group) 元组。
        """
        with self._state_lock:
            previous = self._runtime.per_group_counts.get(group, 0)
            new_value = persisted_count if persisted_count is not None else previous + 1
            self._runtime.per_group_counts[group] = new_value
            self._runtime.total_count += max(0, new_value - previous)
            return self._runtime.total_count, dict(self._runtime.per_group_counts)

    def set_persisted_count(self, group: str, count: int) -> None:
        with self._state_lock:
            previous = self._runtime.per_group_counts.get(group, 0)
            self._runtime.per_group_counts[group] = count
            self._runtime.total_count += count - previous

    def runtime_counts_snapshot(self) -> tuple[int, dict[str, int]]:
        """线程安全快照：返回 (total, per_group_dict_copy)。"""
        with self._state_lock:
            return self._runtime.total_count, dict(self._runtime.per_group_counts)

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
        """启动发送循环。通过 transition 单一入口转 STARTING 再起后台线程。"""
        try:
            state_store = StateStore(settings.state_db_path)
        except Exception as exc:
            return TransitionResult(ok=False, reason=f"State store unavailable: {exc}")
        result = self.transition(SendState.STARTING)
        if not result.ok:
            return result
        self._active_settings = settings
        self._state_store = state_store
        self._runtime = SendRuntime()
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
            try:
                await sender.start(code_callback=self._event_bus.wait_for_code)
            except asyncio.TimeoutError:
                logger.warning("验证码超时，发送循环将停止")
                # 不进入 send_loop，直接退出 → _run_loop finally 会 transition(STOPPED)
                return
            run_result = self.transition(SendState.RUNNING)
            if not run_result.ok:
                logger.warning("启动中途状态已变，中止 send_loop: %s", run_result.reason)
                return
            await self._event_bus.emit_status("running")
            await send_loop(
                sender=sender,
                settings=settings,
                manager=self,
                message_manager=message_manager,
                event_bus=self._event_bus,
                ai_sender=ai_sender,
                state_store=self._state_store,
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
            self._thread = None
            self._active_settings = None
            self._state_store = None

    def pause(self) -> TransitionResult:
        return self.transition(SendState.PAUSING)

    def resume(self) -> TransitionResult:
        return self.transition(SendState.RUNNING)

    def stop(self) -> TransitionResult:
        return self.transition(SendState.STOPPING)

    def request_emergency_stop(self) -> None:
        """Fail closed when durable accounting can no longer be guaranteed."""
        with self._state_lock:
            self._stop_event.set()
            if self._state not in (SendState.STOPPING, SendState.STOPPED, SendState.IDLE):
                self._state = SendState.STOPPING

    def dashboard_snapshot(self) -> dict:
        with self._state_lock:
            state = self._state.value
            total = self._runtime.total_count
            per_group = dict(self._runtime.per_group_counts)
            settings = self._active_settings
            store = self._state_store
        groups = settings.target_groups if settings else list(per_group)
        group_states = (
            [StateStore.serialize_state(item) for item in store.list_group_states(groups)]
            if store
            else []
        )
        return {
            "state": state,
            "total": total,
            "per_group": per_group,
            "groups": group_states,
            "alerts": [item for item in group_states if item["paused"]],
        }

    def audit_events(self, limit: int = 100) -> list[dict]:
        if self._state_store is None:
            return []
        return [
            {
                "seq": event.seq,
                "occurred_at": event.occurred_at,
                "group": event.group,
                "event_type": event.event_type,
                "reason": event.reason,
                "metadata": event.metadata,
            }
            for event in self._state_store.list_audit(limit)
        ]

    def resume_group(self, group: str) -> dict:
        if self._state_store is None:
            raise RuntimeError("State store is not initialized")
        return StateStore.serialize_state(self._state_store.resume_group(group))

    def submit_code(self, code: str) -> bool:
        return self._event_bus.submit_code(code)

    async def emit_lottery_event(self, event_type: str, data: dict) -> None:
        """发布脱敏后的抽奖 SSE 事件，供扫描和签到适配器调用。"""
        if event_type not in {"lottery_alert", "lottery_updated", "scan_health", "checkin_health"}:
            raise ValueError("不支持的抽奖事件类型")
        self._event_bus._publish(self._lottery_events.publish(event_type, data))
