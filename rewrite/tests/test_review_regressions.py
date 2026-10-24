"""最终只读审查发现的生产路径反例。"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock
import pytest

from bewater.conversation import Conversation
from bewater.engine import Engine
from bewater.models import Message
from bewater.store import Store
from conftest import FakeTelegram


@pytest.mark.asyncio
async def test_manual_checkin_recovery_queries_and_audits_once_per_day(setup, at):
    """发现手动签到后持久跳过重复拉取；次日仍能重新签到。"""
    control, identity, values = setup
    message = Message(identity.peer_id, 10, "签到", at, 7, own=True)
    gateway = FakeTelegram(identity, [message])
    gateway.recent = AsyncMock(wraps=gateway.recent)
    engine = Engine(control, gateway, values, clock=lambda: at)
    await engine.resolve_groups()
    for _ in range(4):
        await engine.checkins()
    gateway.recent.assert_awaited_once()
    with control.store.connect() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM bw_audit WHERE event='checkin_recovered'").fetchone()[
                0
            ]
            == 1
        )
    assert not gateway.calls
    engine.clock = lambda: at + timedelta(days=1)
    await engine.checkins()
    assert len(gateway.calls) == 1


def test_explicit_lottery_resume_survives_restart_and_keeps_unknown_ledger(setup, paths, at):
    """人工恢复不抹掉未知事实，但不能在重启后再冻结/再次提醒。"""
    control, identity, _ = setup
    record = control.store.lottery(identity.peer_id, 20, "resume", None, at, "PLANNED", "", "测试")
    action = control.store.reserve(
        "resume-unknown", identity.peer_id, "lottery", "", 20, at, 10, 30
    )
    control.store.action_status(action, "UNKNOWN")
    control.store.lottery_result(record, "UNKNOWN", "匿名未知结果")
    control.store.mark_viewed(record)
    control.store.unfreeze(identity.peer_id)
    restarted = Store(paths.database)
    restarted.recover()
    assert not restarted.frozen(identity.peer_id)
    assert restarted.get_lottery(record)["pending"] == 0
    with restarted.connect() as db:
        assert (
            db.execute("SELECT status FROM bw_action WHERE id=?", (action,)).fetchone()[0]
            == "UNKNOWN"
        )
    assert restarted.quota(at.date().isoformat(), identity.peer_id, "lottery") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["管理员提醒：禁止自动参与", "继续自动参与会被移出群"])
@pytest.mark.parametrize("role", ["admin", "member"])
async def test_warning_query_filter_and_policy_share_complete_vocabulary(setup, at, text, role):
    """先查询角色的过滤词表与最终安全判定必须一致。"""
    control, identity, values = setup
    gateway = FakeTelegram(identity)
    gateway.role = role
    engine = Engine(control, gateway, values, clock=lambda: at)
    message = Message(identity.peer_id, 3, text, at, 99)
    await Conversation(engine, identity).observe([message], [message])
    assert (control.store.state(identity.peer_id).get("pause") == "safety") == (role == "admin")


@pytest.mark.asyncio
async def test_regular_observation_reuses_context_instead_of_pulling_history_twice(setup, at):
    """上下文首轮读取一次，后续只取未观察消息。"""
    control, identity, values = setup
    gateway = FakeTelegram(identity)
    gateway.recent = AsyncMock(wraps=gateway.recent)
    engine = Engine(control, gateway, values, clock=lambda: at)
    await engine.resolve_groups()
    conversation = Conversation(engine, identity)
    for _ in range(4):
        assert await conversation.step() >= 20
    gateway.recent.assert_awaited_once()


@pytest.mark.asyncio
async def test_exhausted_checkin_quota_is_terminal_without_repeated_reads(setup, at):
    """日额度已满时签到只核查一次，不在等待循环重复请求历史。"""
    control, identity, values = setup
    gateway = FakeTelegram(identity)
    gateway.recent = AsyncMock(wraps=gateway.recent)
    engine = Engine(control, gateway, values, clock=lambda: at)
    await engine.resolve_groups()
    with control.store.connect(True) as db:
        db.execute(
            "INSERT INTO bw_quota VALUES(?,?,?,?)",
            (at.date().isoformat(), identity.peer_id, "text", values["daily_limit"]),
        )
    await engine.checkins()
    await engine.checkins()
    gateway.recent.assert_awaited_once()
    assert not gateway.calls


@pytest.mark.asyncio
async def test_lottery_gap_does_not_requery_permissions_while_waiting(setup, at):
    """尚未到账号动作时隙不重复查询发布者，扫描仍保留自己的节奏。"""
    from time import monotonic
    from bewater.lottery import Lottery

    control, identity, values = setup
    gateway = FakeTelegram(identity)
    engine = Engine(control, gateway, values, clock=lambda: at)
    await engine.resolve_groups()
    job = Lottery(engine, identity)
    message = Message(
        identity.peer_id,
        20,
        "抽奖\n奖品：耳机\n回复“+1”参与",
        at,
        7,
        pinned=True,
        source="pinned_today",
    )
    await job.discover(message)
    gateway.trust = AsyncMock(wraps=gateway.trust)
    engine.next_lottery = job.next_scan = monotonic() + 60
    await job.step()
    gateway.trust.assert_not_awaited()
    assert not gateway.calls
