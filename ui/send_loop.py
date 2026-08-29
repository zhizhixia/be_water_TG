from __future__ import annotations

import asyncio
import logging
import random
import re
from time import monotonic
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from enum import Enum
from typing import TYPE_CHECKING, Any

from telethon.errors import ChannelPrivateError, FloodWaitError, RPCError

from src.activity_observer import ActivityObserver, Observation
from src.config import Settings
from src.message_generator import MessageGenerator
from src.participation_policy import (
    CandidateKind,
    ParticipationCandidate,
    ParticipationPolicy,
)
from src.safety_guard import SafetyGuard
from src.sender import TelegramSender
from src.state_store import PauseKind, StateStore
from src.lottery_store import LotteryStore
from src.daily_task_scheduler import DailyTaskScheduler
from src.daily_checkin_service import DailyCheckinService
from src.instance_lock import InstanceLock
from src.lottery_detector import detect_with_ai
from src.lottery_executor import LotteryExecutor
from src.lottery_fingerprint import base_fingerprint
from src.lottery_models import ParticipationStatus
from src.lottery_planner import plan as plan_lottery
from src.lottery_queue import LotteryQueue
from src.lottery_message_source import current_day_pinned
from ui.message_manager import MessageManager

if TYPE_CHECKING:
    from src.ai_sender import AISender
    from web_manager import EventBus, SendLoopManager

logger = logging.getLogger(__name__)

RETRY_DELAYS = [30, 60, 120]
MAX_RETRIES = 3
OBSERVATION_INTERVAL = 10


class SendState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    WAITING_CODE = "waiting_code"


@dataclass
class SendRuntime:
    stopped: bool = False
    paused: bool = False
    total_count: int = 0
    per_group_counts: dict[str, int] = field(default_factory=dict)
    on_paused_callback: Callable[..., Any] | None = field(default=None)


@dataclass(frozen=True)
class PendingCandidate:
    candidate: ParticipationCandidate
    text: str
    source: str
    due_at: float


def _in_schedule(settings: Settings, now: time | None = None) -> bool:
    if not settings.schedule_enabled:
        return True
    current = now or datetime.now().time()
    return (
        time.fromisoformat(settings.schedule_morning_start)
        <= current
        <= time.fromisoformat(settings.schedule_morning_end)
    ) or (
        time.fromisoformat(settings.schedule_afternoon_start)
        <= current
        <= time.fromisoformat(settings.schedule_afternoon_end)
    )


async def _interruptible_wait(
    seconds: float,
    stop_event: Any,
    event_bus: EventBus | None = None,
) -> bool:
    """Wait in short slices. Return False when stop was requested."""
    remaining = max(0.0, float(seconds))
    while remaining > 0 and not stop_event.is_set():
        step = min(0.5, remaining)
        await asyncio.sleep(step)
        remaining -= step
        if event_bus and remaining >= 0:
            await event_bus.emit_countdown(int(remaining))
    return not stop_event.is_set()


def _candidate_from_observation(observation: Observation) -> ParticipationCandidate | None:
    if observation.new_messages:
        latest = observation.new_messages[-1]
        text = latest.text.strip()
        question_markers = (
            "?",
            "？",
            "吗",
            "如何",
            "怎么",
            "为什么",
            "谁",
            "哪",
            "求解决",
            "求助",
            "请教",
            "怎么办",
            "能否",
            "可不可以",
            "有没有人知道",
        )
        kind = (
            CandidateKind.QUESTION
            if any(marker in text for marker in question_markers)
            else CandidateKind.DISCUSSION
        )
        compact = re.sub(r"\s+", "", text.casefold())
        low_signal = {
            "哈", "哈哈", "哈哈哈", "嗯", "哦", "ok", "好的", "收到", "谢谢", "早", "晚安",
        }
        relevant = (
            kind is CandidateKind.QUESTION
            or (
                len(compact) >= 6
                and compact not in low_signal
                and not compact.startswith(("http://", "https://"))
            )
        )
        return ParticipationCandidate(
            group=observation.group,
            kind=kind,
            relevant=relevant,
            trigger_message_id=latest.message_id,
        )
    if observation.idle and not observation.latest_message_is_self:
        return ParticipationCandidate(observation.group, CandidateKind.IDLE)
    return None


async def _classify_candidate_with_ai(
    ai_sender: AISender | None,
    candidate: ParticipationCandidate,
    text: str,
    context_count: int,
) -> ParticipationCandidate:
    """Prefer semantic classification and retain a deterministic fallback."""
    if ai_sender is None or candidate.kind is CandidateKind.IDLE:
        return candidate
    classifier = getattr(ai_sender, "classify_message", None)
    if not callable(classifier):
        return candidate
    try:
        classification = await classifier(candidate.group, text, context_count)
    except Exception:
        logger.warning(
            "AI classification failed; using deterministic fallback [%s]",
            candidate.group,
            exc_info=True,
        )
        return candidate
    if isinstance(classification, CandidateKind):
        classification = classification.value
    normalized = str(classification).strip().casefold()
    if normalized == CandidateKind.QUESTION.value:
        return replace(candidate, kind=CandidateKind.QUESTION, relevant=True)
    if normalized == CandidateKind.DISCUSSION.value:
        return replace(candidate, kind=CandidateKind.DISCUSSION, relevant=True)
    if normalized == "irrelevant":
        return replace(candidate, relevant=False)
    logger.warning(
        "Unknown AI classification %r; using deterministic fallback [%s]",
        classification,
        candidate.group,
    )
    return candidate


async def _send_with_retry(
    sender: TelegramSender,
    group: str,
    message: str,
    stop_event: Any,
    event_bus: EventBus | None,
) -> bool:
    for attempt in range(MAX_RETRIES):
        try:
            await sender.send_message(message, target_group=group)
            return True
        except FloodWaitError as exc:
            logger.warning("FloodWait %ds; entering global cooldown", exc.seconds)
            if event_bus:
                await event_bus.emit_alert(
                    group, "flood_wait", f"Telegram requested {exc.seconds}s cooldown"
                )
            if not await _interruptible_wait(exc.seconds, stop_event, event_bus):
                return False
        except (ConnectionError, OSError) as exc:
            if attempt >= MAX_RETRIES - 1:
                logger.error("Network retries exhausted [%s]: %s", group, exc)
                return False
            delay = RETRY_DELAYS[attempt]
            logger.warning("Network error [%s], retrying in %ds: %s", group, delay, exc)
            if not await _interruptible_wait(delay, stop_event, event_bus):
                return False
        except RPCError as exc:
            logger.error("Telegram RPC error [%s]: %s", group, exc)
            return False
        except Exception:
            logger.exception("Unexpected send error [%s]", group)
            return False
    return False


async def send_loop(
    sender: TelegramSender,
    settings: Settings,
    manager: SendLoopManager,
    message_manager: MessageManager,
    event_bus: EventBus | None = None,
    ai_sender: AISender | None = None,
    state_store: StateStore | None = None,
) -> None:
    """Observe authorized groups and send only policy-approved candidates."""
    stop_event = manager.stop_event
    store = state_store or StateStore(settings.state_db_path)
    observer = ActivityObserver(store, settings.idle_threshold_minutes)
    policy = ParticipationPolicy()
    safety = SafetyGuard()
    generator = MessageGenerator(ai_sender, message_manager)

    # 先取得单实例锁，再把配置入口解析并迁移为稳定 peer_id；后续状态、观察和
    # 普通参与均不得再回退到 URL/用户名。
    instance_lock = InstanceLock(settings.phone, settings.state_db_path)
    if not instance_lock.acquire():
        manager.request_emergency_stop()
        return
    groups = list(settings.target_groups)
    checkin_allowed: set[str] | None = None
    lottery_store: LotteryStore | None = None
    if (settings.lottery_enabled or settings.daily_checkin_enabled) and callable(getattr(sender, "resolve_group_identity", None)):
        try:
            identities = [await sender.resolve_group_identity(group) for group in settings.target_groups]
            store.migrate_group_identities(identities)
            groups = [str(identity.peer_id) for identity in identities]
            lottery_store = LotteryStore(settings.state_db_path)
            clock = type("ShanghaiClock", (), {"now": staticmethod(lambda: datetime.now().astimezone())})()
            peers = DailyTaskScheduler(lottery_store, clock).ensure_batch(
                [identity.peer_id for identity in identities], settings.schedule_enabled, _in_schedule(settings)
            )
            checkin_allowed = {str(peer_id) for peer_id in peers}
            if settings.daily_checkin_enabled:
                checkin = DailyCheckinService(
                    lottery_store, sender, clock, settings.daily_limit, store,
                    emergency_stop=manager.request_emergency_stop,
                )
                for peer_id in peers:
                    if stop_event.is_set():
                        break
                    result = await checkin.attempt(peer_id, str(peer_id))
                    if result not in {"COMPLETED", "ALREADY_ATTEMPTED"}:
                        checkin_allowed.discard(str(peer_id))
            if event_bus:
                await event_bus.emit_health("checkin_ready", "Daily check-in batch initialized")
        except Exception as exc:
            logger.exception("Lottery/check-in bootstrap failed")
            manager.request_emergency_stop()
            if event_bus:
                await event_bus.emit_alert("system", "lottery_bootstrap_failed", str(exc))
            return

    store.reconcile_pending_reservations(groups, settings.daily_limit)
    for state in store.list_group_states(groups):
        manager.set_persisted_count(state.group, state.sent_count)

    if settings.lottery_enabled and lottery_store is not None:
        lottery_queue = LotteryQueue()
        executor = LotteryExecutor(sender, lottery_store)

        async def process_lottery_message(message: Any) -> None:
            if stop_event.is_set() or lottery_store.is_frozen(message.peer_id):
                return
            detection = await detect_with_ai(message, ai_sender if callable(getattr(ai_sender, "analyze", None)) else None)
            if not detection.is_lottery:
                return
            trust = await sender.get_publisher_trust(message.peer_id, message.sender_id)
            message = replace(message, publisher_trust=trust)
            fingerprint = base_fingerprint(message)
            if lottery_store.is_duplicate(message.peer_id, message.message_id, fingerprint, message.deadline):
                return
            lottery_id = lottery_store.record_entry(message.peer_id, message.message_id, fingerprint, message.deadline, ParticipationStatus.DETECTED.value)
            lottery_plan = plan_lottery(message)
            if lottery_plan.manual_reason:
                lottery_store.set_entry_status(lottery_id, ParticipationStatus.MANUAL_REQUIRED)
                return
            async def run_plan() -> None:
                result = await executor.execute(message, lottery_plan.actions, lambda: sender.get_message(message.peer_id, message.message_id), lottery_id, stop_event)
                if result == "RESULT_UNKNOWN":
                    lottery_store.freeze(message.peer_id, result, lottery_id)
            await lottery_queue.put(run_plan)

        for peer in (int(group) for group in groups):
            if stop_event.is_set():
                break
            try:
                pinned, _complete = await sender.get_current_pinned_messages(peer)
                for message in current_day_pinned(pinned, clock.now().astimezone().date().isoformat()):
                    await process_lottery_message(replace(message, source="pinned_today"))
                cursor = lottery_store.scan_state(peer).get("cursor_message_id")
                if cursor is None:
                    latest = await sender.get_latest_message_id(peer)
                    lottery_store.commit_scan_batch(peer, latest or 0, [])
                else:
                    messages = await sender.get_messages_after(peer, int(cursor), 500, None)
                    for message in messages:
                        await process_lottery_message(message)
                    if messages:
                        lottery_store.commit_scan_batch(peer, max(message.message_id for message in messages), messages)
            except Exception:
                logger.exception("Lottery processing failed [%s]", peer)
                if event_bus:
                    await event_bus.emit_alert(str(peer), "lottery_group_failed", "lottery processing failed")
        while not stop_event.is_set() and await lottery_queue.run_once():
            pass

    pending: dict[str, PendingCandidate] = {}

    async def apply_safety(group: str, observation: Observation) -> bool:
        """Persist a safety decision and return whether the group is blocked."""
        safety_decision = safety.inspect(observation.new_messages)
        if safety_decision.should_pause:
            paused = store.pause_group(group, PauseKind.SAFETY, safety_decision.reason)
            if event_bus:
                await event_bus.emit_alert(group, "safety_pause", safety_decision.reason)
                await event_bus.emit_group_state(StateStore.serialize_state(paused))
            return True
        if safety_decision.should_audit:
            store.record_audit(group, "safety_notice", safety_decision.reason)
            if event_bus:
                await event_bus.emit_decision(group, "notice", safety_decision.reason)
        return False

    async def pause_inaccessible(group: str) -> None:
        reason = "账号无法读取该群：可能未加入、已被移出，或群组链接为私有"
        paused = store.pause_group(group, PauseKind.MANUAL, reason)
        logger.warning("Pausing inaccessible group [%s]: %s", group, reason)
        if event_bus:
            await event_bus.emit_alert(group, "group_inaccessible", reason)
            await event_bus.emit_group_state(StateStore.serialize_state(paused))

    async def apply_flood_wait(group: str, exc: FloodWaitError) -> None:
        """Enter the same global cooldown for send and permission rate limits."""
        logger.warning(
            "FloodWait %ds while checking permissions [%s]",
            exc.seconds,
            group,
        )
        if event_bus:
            await event_bus.emit_alert(
                group,
                "flood_wait",
                f"Telegram requested {exc.seconds}s cooldown",
            )
        await _interruptible_wait(exc.seconds, stop_event, event_bus)

    while not stop_event.is_set():
        if manager.state == SendState.PAUSING:
            manager.transition(SendState.PAUSED)
            if event_bus:
                await event_bus.emit_status("paused")
        while manager.state == SendState.PAUSED and not stop_event.is_set():
            await asyncio.sleep(0.5)
        if stop_event.is_set():
            break

        if not _in_schedule(settings):
            if event_bus:
                await event_bus.emit_health("scheduled_wait", "Outside configured work window")
            await _interruptible_wait(60, stop_event, event_bus)
            continue

        # Resolve due candidates first. Their wait is represented by due_at,
        # so other groups continue to be observed while one group is waiting.
        for group, item in list(pending.items()):
            if stop_event.is_set() or manager.state in (SendState.PAUSING, SendState.PAUSED):
                break
            if item.due_at > monotonic():
                continue
            pending.pop(group, None)
            try:
                recheck = await observer.observe(sender, group)
            except FloodWaitError as exc:
                await apply_flood_wait(group, exc)
                continue
            except ChannelPrivateError:
                await pause_inaccessible(group)
                continue
            except Exception:
                logger.exception("Failed to re-observe group [%s]", group)
                store.record_audit(group, "candidate_cancelled", "recheck_failed")
                continue
            if await apply_safety(group, recheck):
                continue
            if recheck.new_messages or recheck.latest_message_is_self:
                store.record_audit(group, "candidate_cancelled", "activity_changed_during_wait")
                if event_bus:
                    await event_bus.emit_decision(group, "cancel", "activity_changed_during_wait")
                continue
            if not policy.revalidate(store.get_group_state(group), settings).allowed:
                store.record_audit(group, "candidate_cancelled", "revalidation_failed")
                if event_bus:
                    await event_bus.emit_decision(group, "cancel", "revalidation_failed")
                continue
            try:
                store.reserve_send(group, settings.daily_limit)
            except Exception as exc:
                logger.warning("Unable to reserve send quota [%s]: %s", group, exc)
                store.record_audit(group, "candidate_cancelled", "quota_reservation_failed")
                if event_bus:
                    await event_bus.emit_group_state(
                        StateStore.serialize_state(store.get_group_state(group))
                    )
                continue
            sent = await _send_with_retry(sender, group, item.text, stop_event, event_bus)
            if not sent:
                try:
                    store.release_send_reservation(group)
                except Exception:
                    logger.exception("Failed to release send reservation [%s]", group)
                    manager.request_emergency_stop()
                store.record_audit(group, "send_failed", "network_or_rpc")
                continue
            try:
                persisted = store.confirm_send(group, settings.daily_limit)
            except Exception as exc:
                logger.exception("State confirmation failed after send [%s]", group)
                if event_bus:
                    await event_bus.emit_alert(group, "persistence_failure", str(exc))
                manager.request_emergency_stop()
                break
            try:
                generator.commit(group, item.text, item.source)
            except Exception:
                logger.exception("Failed to commit sent-message history [%s]", group)
                store.record_audit(group, "history_commit_failed", item.source)
            total, per_group = manager.increment_count(group, persisted.sent_count)
            store.record_audit(
                group, "sent", item.source, {"count": persisted.sent_count}
            )
            if event_bus:
                await event_bus.emit_counter(total, per_group)
                await event_bus.emit_group_state(StateStore.serialize_state(persisted))

        for group in groups:
            if checkin_allowed is not None and group not in checkin_allowed:
                continue
            if stop_event.is_set() or manager.state in (SendState.PAUSING, SendState.PAUSED):
                break
            if group in pending:
                continue
            persisted_state = store.get_group_state(group)
            if persisted_state.paused:
                continue
            try:
                observation = await observer.observe(sender, group)
            except FloodWaitError as exc:
                await apply_flood_wait(group, exc)
                continue
            except ChannelPrivateError:
                await pause_inaccessible(group)
                continue
            except Exception:
                logger.exception("Failed to observe group [%s]", group)
                if event_bus:
                    await event_bus.emit_decision(group, "skip", "observation_failed")
                continue

            if await apply_safety(group, observation):
                continue

            current_state = store.get_group_state(group)
            if (
                not observation.new_messages
                and observation.latest_message_is_self
                and current_state.last_outbound_at
            ):
                previous_check = current_state.last_unanswered_checked_at
                unanswered = store.mark_unanswered(
                    group, window_minutes=settings.idle_threshold_minutes
                )
                if unanswered.last_unanswered_checked_at != previous_check:
                    store.record_audit(
                        group,
                        "unanswered",
                        f"consecutive:{unanswered.consecutive_unanswered}",
                    )
                    if event_bus:
                        await event_bus.emit_group_state(
                            StateStore.serialize_state(unanswered)
                        )

            candidate = _candidate_from_observation(observation)
            if candidate is None:
                continue
            if settings.ai_enabled and observation.new_messages:
                candidate = await _classify_candidate_with_ai(
                    ai_sender,
                    candidate,
                    observation.new_messages[-1].text,
                    settings.ai_context_count,
                )
            state = store.get_group_state(group)
            decision = policy.evaluate(candidate, state, settings)
            store.record_audit(
                group,
                "decision",
                decision.reason,
                {"kind": candidate.kind.value, "allowed": decision.allowed},
            )
            if event_bus:
                await event_bus.emit_decision(
                    group, "allow" if decision.allowed else "skip", decision.reason
                )
            if not decision.allowed:
                continue

            if settings.ai_enabled and ai_sender is not None:
                try:
                    if await ai_sender.should_skip(group, settings.ai_context_count):
                        store.record_audit(
                            group, "decision", "last_message_still_recent",
                            {"kind": candidate.kind.value, "allowed": False},
                        )
                        if event_bus:
                            await event_bus.emit_decision(
                                group, "skip", "last_message_still_recent"
                            )
                        continue
                except ChannelPrivateError:
                    await pause_inaccessible(group)
                    continue
                except Exception:
                    logger.warning(
                        "Unable to check recent own message [%s]", group,
                        exc_info=True,
                    )

            result = await generator.generate(
                group,
                settings.ai_prompt,
                settings.ai_context_count,
                settings.ai_enabled,
            )
            if result.text is None:
                store.record_audit(group, "generation_skipped", result.reason)
                if event_bus:
                    await event_bus.emit_decision(group, "skip", result.reason)
                continue

            delay = random.randint(settings.reply_delay_min, settings.reply_delay_max)
            pending[group] = PendingCandidate(
                candidate=candidate,
                text=result.text,
                source=result.source or "unknown",
                due_at=monotonic() + delay,
            )
            if event_bus:
                await event_bus.emit_decision(group, "waiting", f"delay:{delay}")

        if stop_event.is_set():
            break
        if pending:
            next_due = min(item.due_at for item in pending.values())
            wait_seconds = min(OBSERVATION_INTERVAL, max(0.0, next_due - monotonic()))
            if event_bus:
                await event_bus.emit_countdown(int(max(0.0, next_due - monotonic())))
        else:
            wait_seconds = OBSERVATION_INTERVAL
        await _interruptible_wait(wait_seconds, stop_event)

    total, _ = manager.runtime_counts_snapshot()
    logger.info("Participation loop exited (session total: %d)", total)
