from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from telethon.errors import ChannelPrivateError, FloodWaitError

from src.config import Settings
from src.group_identity import GroupIdentity
from src.state_store import PauseKind, StateStore
from src.activity_observer import Observation
from ui.send_loop import SendState, _candidate_from_observation, send_loop


TZ = ZoneInfo("Asia/Shanghai")


def _settings(tmp_path) -> Settings:
    return Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/allowed"],
        ai_enabled=False,
        question_reply_pct=100,
        discussion_reply_pct=100,
        reply_delay_min=0,
        reply_delay_max=0,
        state_db_path=str(tmp_path / "state.db"),
    )


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.stop_event = threading.Event()
    manager.state = SendState.RUNNING
    manager.set_persisted_count = MagicMock()
    manager.increment_count = MagicMock(return_value=(1, {"https://t.me/allowed": 1}))
    manager.runtime_counts_snapshot = MagicMock(return_value=(1, {"https://t.me/allowed": 1}))
    return manager


def test_question_candidate_is_generated_sent_and_persisted(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    fake_sender.get_recent_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=1,
                text="这个功能怎么配置？",
                date=datetime.now(TZ),
                out=False,
                sender_id=2,
                sender=None,
            )
        ]
    )

    async def send_once(*args, **kwargs):
        manager.stop_event.set()

    fake_sender.send_message = AsyncMock(side_effect=send_once)
    messages = MagicMock()
    messages.get_message.return_value = "可以先打开参与策略页面"
    store = StateStore(settings.state_db_path)
    store.touch_activity(settings.target_groups[0], datetime.now(TZ), message_id=0)

    asyncio.run(
        send_loop(
            fake_sender,
            settings,
            manager,
            messages,
            state_store=store,
        )
    )

    fake_sender.send_message.assert_awaited_once()
    assert store.get_group_state(settings.target_groups[0]).sent_count == 1


def test_resolved_identity_keeps_stable_state_key_and_configured_message_alias(tmp_path) -> None:
    """普通观察/发送使用稳定状态键，消息配置仍按原始群引用查找。"""
    settings = _settings(tmp_path)
    settings.lottery_enabled = False
    settings.daily_checkin_enabled = False
    manager = _manager()
    sender = MagicMock()
    sender.resolve_group_identity = AsyncMock(
        return_value=GroupIdentity(-1000000000101, settings.target_groups[0], "允许群")
    )
    sender.get_recent_messages = AsyncMock(return_value=[
        SimpleNamespace(
            id=1, text="这个功能怎么配置？", date=datetime.now(TZ),
            out=False, sender_id=2, sender=None,
        )
    ])
    sender.is_group_admin = AsyncMock(return_value=False)

    async def send_once(*_args, **kwargs):
        assert kwargs["target_group"] == "-1000000000101"
        manager.stop_event.set()

    sender.send_message = AsyncMock(side_effect=send_once)
    messages = MagicMock()
    messages.get_message.return_value = "按配置说明操作"
    store = StateStore(settings.state_db_path)
    store.touch_activity("-1000000000101", datetime.now(TZ), message_id=0)

    asyncio.run(send_loop(sender, settings, manager, messages, state_store=store))

    messages.get_message.assert_called_with(settings.target_groups[0])
    assert store.get_group_state("-1000000000101").sent_count == 1
    assert store.get_group_state(settings.target_groups[0]).sent_count == 0


def test_explicit_complaint_pauses_without_sending(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    manager.stop_event.set()
    fake_sender.get_recent_messages = AsyncMock(return_value=[])
    messages = MagicMock()
    store = StateStore(settings.state_db_path)
    store.touch_activity(settings.target_groups[0], datetime.now(TZ), message_id=0)

    # Directly exercise a short run by clearing stop and stopping after observation.
    manager.stop_event.clear()
    fake_sender.get_recent_messages.return_value = [
        SimpleNamespace(
            id=1,
            text="不要再发了，太刷屏",
            date=datetime.now(TZ),
            out=False,
            sender_id=2,
            sender=None,
        )
    ]

    async def run_briefly():
        task = asyncio.create_task(
            send_loop(fake_sender, settings, manager, messages, state_store=store)
        )
        await asyncio.sleep(0.05)
        manager.stop_event.set()
        await task

    asyncio.run(run_briefly())
    assert store.get_group_state(settings.target_groups[0]).pause_kind is PauseKind.SAFETY
    fake_sender.send_message.assert_not_awaited()


def test_private_group_is_paused_and_not_polled_repeatedly(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    messages = MagicMock()
    store = StateStore(settings.state_db_path)
    fake_sender.get_recent_messages = AsyncMock(
        side_effect=ChannelPrivateError(request=MagicMock())
    )

    async def run_briefly():
        task = asyncio.create_task(
            send_loop(fake_sender, settings, manager, messages, state_store=store)
        )
        await asyncio.sleep(0.05)
        manager.stop_event.set()
        await task

    asyncio.run(run_briefly())
    state = store.get_group_state(settings.target_groups[0])
    assert state.pause_kind is PauseKind.MANUAL
    assert "无法读取" in state.pause_reason
    assert fake_sender.get_recent_messages.await_count == 1


def test_recent_own_ai_message_suppresses_second_reply(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    settings.ai_enabled = True
    manager = _manager()
    store = StateStore(settings.state_db_path)
    store.touch_activity(settings.target_groups[0], datetime.now(TZ), message_id=0)
    fake_sender.get_recent_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=1,
                text="还有人遇到这个问题吗？",
                date=datetime.now(TZ),
                out=False,
                sender_id=2,
                sender=None,
            )
        ]
    )
    ai_sender = AsyncMock()
    ai_sender.should_skip.return_value = True
    messages = MagicMock()

    async def run_briefly():
        task = asyncio.create_task(
            send_loop(
                fake_sender,
                settings,
                manager,
                messages,
                ai_sender=ai_sender,
                state_store=store,
            )
        )
        await asyncio.sleep(0.05)
        manager.stop_event.set()
        await task

    asyncio.run(run_briefly())
    ai_sender.should_skip.assert_awaited_once()
    ai_sender.generate_message.assert_not_awaited()
    fake_sender.send_message.assert_not_awaited()


def test_low_signal_discussion_is_not_a_relevant_candidate() -> None:
    observation = Observation(
        "group",
        (SimpleNamespace(message_id=1, text="哈哈"),),
        False,
        False,
    )
    candidate = _candidate_from_observation(observation)
    assert candidate is not None
    assert not candidate.relevant


def test_deterministic_question_fallback_handles_help_request_without_false_me_marker() -> None:
    help_request = Observation(
        "group",
        (SimpleNamespace(message_id=1, text="这个报错求解决"),),
        False,
        False,
    )
    statement = Observation(
        "group",
        (SimpleNamespace(message_id=2, text="这么处理效果很好"),),
        False,
        False,
    )
    assert _candidate_from_observation(help_request).kind.value == "question"
    assert _candidate_from_observation(statement).kind.value == "discussion"


def test_activity_during_delay_cancels_candidate_before_send(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    store = StateStore(settings.state_db_path)
    store.touch_activity(settings.target_groups[0], datetime.now(TZ), message_id=0)
    now = datetime.now(TZ)
    fake_sender.get_recent_messages = AsyncMock(side_effect=[
        [SimpleNamespace(id=1, text="这个功能怎么配置？", date=now, out=False, sender_id=2, sender=None)],
        [SimpleNamespace(id=2, text="不要再发了", date=now, out=False, sender_id=3, sender=None)],
    ])
    messages = MagicMock()
    messages.get_message.return_value = "TXT 回复"

    async def run_briefly():
        task = asyncio.create_task(send_loop(fake_sender, settings, manager, messages, state_store=store))
        await asyncio.sleep(0.05)
        manager.stop_event.set()
        await task

    asyncio.run(run_briefly())
    assert store.get_group_state(settings.target_groups[0]).pause_kind is PauseKind.SAFETY
    fake_sender.send_message.assert_not_awaited()


def test_waiting_group_does_not_block_observation_of_other_groups(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    settings.target_groups = ["https://t.me/a", "https://t.me/b"]
    settings.reply_delay_min = 30
    settings.reply_delay_max = 30
    manager = _manager()
    store = StateStore(settings.state_db_path)
    now = datetime.now(TZ)
    for group in settings.target_groups:
        store.touch_activity(group, now, message_id=0)

    async def messages_for(group, limit=20):
        return [SimpleNamespace(id=1, text="这个功能怎么配置？", date=now, out=False, sender_id=2, sender=None)]

    fake_sender.get_recent_messages = AsyncMock(side_effect=messages_for)
    messages = MagicMock()
    messages.get_message.return_value = "TXT 回复"

    async def run_briefly():
        task = asyncio.create_task(send_loop(fake_sender, settings, manager, messages, state_store=store))
        await asyncio.sleep(0.05)
        manager.stop_event.set()
        await task

    asyncio.run(run_briefly())
    observed_groups = [call.args[0] for call in fake_sender.get_recent_messages.await_args_list]
    assert observed_groups[:2] == settings.target_groups


def test_send_loop_reaches_unanswered_pause_after_two_quiet_windows(
    monkeypatch, tmp_path, fake_sender
) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    start = datetime(2026, 7, 28, 12, 0, tzinfo=TZ)
    current = [start]
    store = StateStore(settings.state_db_path, now=lambda: current[0])
    group = settings.target_groups[0]
    store.touch_activity(group, start - timedelta(minutes=1), message_id=0)
    store.increment_sent(group, settings.daily_limit)
    current[0] += timedelta(minutes=settings.idle_threshold_minutes)
    fake_sender.get_recent_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=1,
                text="已发送内容",
                date=start,
                out=True,
                sender_id=1,
                sender=SimpleNamespace(is_self=True),
            )
        ]
    )
    waits = 0

    async def advance_clock(_seconds, stop_event, event_bus=None):
        nonlocal waits
        waits += 1
        current[0] += timedelta(minutes=settings.idle_threshold_minutes)
        if waits >= 2:
            stop_event.set()
        return not stop_event.is_set()

    monkeypatch.setattr("ui.send_loop._interruptible_wait", advance_clock)
    asyncio.run(send_loop(fake_sender, settings, manager, MagicMock(), state_store=store))

    state = store.get_group_state(group)
    assert state.consecutive_unanswered == 2
    assert state.pause_kind is PauseKind.UNANSWERED


def test_ai_semantic_classifier_controls_candidate_kind(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    settings.ai_enabled = True
    settings.question_reply_pct = 100
    settings.discussion_reply_pct = 0
    manager = _manager()
    store = StateStore(settings.state_db_path)
    group = settings.target_groups[0]
    store.touch_activity(group, datetime.now(TZ), message_id=0)
    fake_sender.get_recent_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=1,
                text="这个现象属于配置差异",
                date=datetime.now(TZ),
                out=False,
                sender_id=2,
                sender=None,
            )
        ]
    )
    ai_sender = MagicMock()
    ai_sender.classify_message = AsyncMock(return_value="question")
    ai_sender.should_skip = AsyncMock(return_value=False)
    ai_sender.generate_message = AsyncMock(return_value="可以检查配置来源")
    ai_sender.commit_sent = MagicMock()

    async def send_once(*args, **kwargs):
        manager.stop_event.set()

    fake_sender.send_message = AsyncMock(side_effect=send_once)
    asyncio.run(
        send_loop(
            fake_sender,
            settings,
            manager,
            MagicMock(),
            ai_sender=ai_sender,
            state_store=store,
        )
    )

    ai_sender.classify_message.assert_awaited_once()
    fake_sender.send_message.assert_awaited_once()
    ai_sender.commit_sent.assert_called_once_with(group, "可以检查配置来源")


def test_send_loop_recovers_crash_reservation_and_continues(tmp_path, fake_sender) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    store = StateStore(settings.state_db_path)
    group = settings.target_groups[0]
    store.touch_activity(group, datetime.now(TZ), message_id=0)
    store.reserve_send(group, settings.daily_limit)
    fake_sender.get_recent_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=1,
                text="这个功能怎么配置？",
                date=datetime.now(TZ),
                out=False,
                sender_id=2,
                sender=None,
            )
        ]
    )
    messages = MagicMock()
    messages.get_message.return_value = "可以检查配置页"

    async def send_once(*args, **kwargs):
        manager.stop_event.set()

    fake_sender.send_message = AsyncMock(side_effect=send_once)
    asyncio.run(send_loop(fake_sender, settings, manager, messages, state_store=store))

    state = store.get_group_state(group)
    assert state.sent_count == 2
    assert state.pending_reservation_at is None
    assert any(event.event_type == "reservation_recovered" for event in store.list_audit())


def test_permission_flood_wait_enters_global_cooldown(
    monkeypatch, tmp_path, fake_sender
) -> None:
    settings = _settings(tmp_path)
    manager = _manager()
    store = StateStore(settings.state_db_path)
    group = settings.target_groups[0]
    store.touch_activity(group, datetime.now(TZ), message_id=0)
    fake_sender.get_recent_messages = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=1,
                text="管理员提醒",
                date=datetime.now(TZ),
                out=False,
                sender_id=2,
                sender=None,
            )
        ]
    )
    fake_sender.is_group_admin = AsyncMock(
        side_effect=FloodWaitError(request=MagicMock(), capture=30)
    )
    cooldowns = []

    async def capture_wait(seconds, stop_event, event_bus=None):
        cooldowns.append(seconds)
        stop_event.set()
        return False

    monkeypatch.setattr("ui.send_loop._interruptible_wait", capture_wait)
    asyncio.run(send_loop(fake_sender, settings, manager, MagicMock(), state_store=store))

    assert cooldowns == [30]
    fake_sender.send_message.assert_not_awaited()
