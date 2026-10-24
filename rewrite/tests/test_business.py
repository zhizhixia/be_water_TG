"""共用生产装配验证业务，而不是只测试自定义模拟管线。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from bewater.conversation import Conversation, same_topic
from bewater.engine import Engine
from bewater.lottery import Lottery
from bewater.models import Action, Button, Message
from bewater.policy import lottery_plan
from conftest import FakeTelegram


def engine_for(setup, at, messages=None):
    """使用真实 SQLite、配置、业务组件，仅替换外部 Telegram。"""
    control, identity, values = setup
    values["reply_delay_min"] = values["reply_delay_max"] = 0
    telegram = FakeTelegram(identity, messages)
    engine = Engine(control, telegram, values, clock=lambda: at, randint=lambda low, high: low)
    engine.identities[identity.peer_id] = identity
    engine.groups[identity.peer_id] = values["groups"][0]
    return engine, telegram, identity


def lottery_message(at, **changes):
    """独立预期来自明确公告、按钮与固定上海截止。"""
    value = Message(
        -1000000000123,
        20,
        "抽奖\n奖品：耳机\n点击“参与”按钮\n截止：2026-10-18T11:00:00+08:00",
        at,
        44,
        buttons=((Button("参与", "callback", b"join"),),),
        pinned=True,
        source="pinned_today",
    )
    return replace(value, **changes)


@pytest.mark.asyncio
async def test_conversation_actual_flow_and_one_decision(setup, at):
    """首次只建基线，新增问题产生发送；重复轮询不会对同触发重发。"""
    engine, telegram, identity = engine_for(
        setup, at, [Message(-1000000000123, 1, "历史内容不可立即回复", at, 4)]
    )
    chat = Conversation(engine, identity)
    await chat.step()
    assert telegram.calls == []
    telegram.messages.append(
        Message(identity.peer_id, 2, "请教大家，Python服务怎么部署更稳定？", at, 5)
    )
    await chat.step()
    await chat.step()
    await chat.step()
    assert len(telegram.calls) == 1
    assert engine.store.quota(at.date().isoformat(), identity.peer_id) == 1
    assert len(engine.store.recent_texts(identity.peer_id)) == 1


@pytest.mark.asyncio
async def test_send_time_gate_catches_pause_after_recheck(setup, at):
    """复核期间暂停，零外发且没有留下占用额度。"""
    engine, telegram, identity = engine_for(setup, at)

    async def verify():
        """模拟另一网页请求在复核期间暂停。"""
        engine.control.state = "paused"
        return True

    result = await engine.transmit(
        identity.peer_id, Action("text", 0, "测试"), "chat", "pause-race", verify=verify
    )
    assert result == "WAIT" and telegram.calls == []
    assert engine.store.quota(at.date().isoformat(), identity.peer_id) == 0


@pytest.mark.asyncio
async def test_latest_disabled_group_is_applied_immediately(setup, at):
    """修改当前配置的停用开关，旧启动快照不能继续发送。"""
    engine, telegram, identity = engine_for(setup, at)
    values, rev = engine.control.config.load()
    values["groups"][0]["chat"] = False
    engine.control.config.update({"groups": values["groups"]}, rev)
    assert (
        await engine.transmit(identity.peer_id, Action("text", 0, "不应发送"), "chat", "disabled")
        == "WAIT"
    )
    assert telegram.calls == []


@pytest.mark.asyncio
async def test_checkin_once_cross_day_and_independent_failure(setup, at):
    """签到失败当日不重试，新日有新任务，普通聊天不被连带门控。"""
    engine, telegram, identity = engine_for(setup, at)
    telegram.fail = TimeoutError()
    await engine.checkins()
    await engine.checkins()
    assert len(telegram.calls) == 1
    assert engine.allowed(identity.peer_id, "chat")
    assert not engine.store.frozen(identity.peer_id)
    engine.clock = lambda: at + timedelta(days=1)
    telegram.fail = None
    engine.next_send = 0
    await engine.checkins()
    await engine.checkins()
    assert len(telegram.calls) == 2


@pytest.mark.asyncio
async def test_window_applies_to_all_external_actions(setup, at):
    """非窗口时，签到、聊天和抽奖均没有外部调用。"""
    engine, telegram, identity = engine_for(setup, at)
    engine.config["schedule_enabled"] = True
    engine.clock = lambda: at.replace(hour=7)
    for kind in ("chat", "checkin", "lottery"):
        assert (
            await engine.transmit(identity.peer_id, Action("text", 0, "不发送"), kind, kind)
            == "WAIT"
        )
    await engine.checkins()
    assert telegram.calls == []


@pytest.mark.asyncio
async def test_lottery_production_discover_execute_and_unknown(setup, at):
    """扫描发现实际进入规划、账本、统一门禁、Telegram fake 与冻结。"""
    message = lottery_message(at)
    engine, telegram, identity = engine_for(setup, at, [message])
    lottery = Lottery(engine, identity)
    await lottery.discover(message)
    assert len(lottery.pending) == 1
    record_id, original = lottery.pending[0]
    telegram.fail = TimeoutError()
    assert await lottery.execute(record_id, original) == "UNKNOWN"
    assert engine.store.frozen(identity.peer_id)
    assert engine.store.get_lottery(record_id)["status"] == "UNKNOWN"
    engine.store.mark_viewed(record_id)
    assert engine.store.frozen(identity.peer_id)
    engine.store.unfreeze(identity.peer_id)
    assert not engine.store.frozen(identity.peer_id)
    assert await lottery.execute(record_id, original) == "BLOCKED"
    assert len(telegram.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "edit", "downgrade", "expired"])
async def test_execution_rechecks_latest_evidence(setup, at, change):
    """源删除、编辑、角色降权和执行时过期都不能触发动作。"""
    message = lottery_message(at)
    engine, telegram, identity = engine_for(setup, at, [message])
    lottery = Lottery(engine, identity)
    await lottery.discover(message)
    record_id, original = lottery.pending[0]
    if change == "delete":
        telegram.messages = []
    if change == "edit":
        telegram.messages = [replace(message, text="抽奖\n还需转发")]
    if change == "downgrade":
        telegram.role = "member"
    if change == "expired":
        engine.clock = lambda: at.replace(hour=12)
    await lottery.execute(record_id, original)
    assert telegram.calls == []
    assert engine.store.quota(at.date().isoformat(), identity.peer_id, "lottery") == 0


@pytest.mark.parametrize("kind", ["发送", "回复"])
def test_standalone_fixed_text_lottery(at, kind):
    """纯文本参与不再错误要求 callback，固定文本来自原消息。"""
    message = lottery_message(at, text=f"抽奖\n奖品：耳机\n{kind}“+1”参与", buttons=())
    result = lottery_plan(message, "admin", at)
    assert result.action == Action("text" if kind == "发送" else "reply", message.id, "+1")


@pytest.mark.parametrize(
    "condition",
    [
        "关注频道后参与",
        "转发后参与",
        "subscribe before joining",
        "额外条件未能识别",
        "https://evil.example",
    ],
)
def test_unknown_or_additional_conditions_always_manual(at, condition):
    """未知附加条件也不能靠关键词黑名单遗漏被放行。"""
    result = lottery_plan(lottery_message(at, text="抽奖\n点击参与按钮\n" + condition), "admin", at)
    assert result.action is None


@pytest.mark.asyncio
async def test_scan_pagination_over_500_does_not_skip_middle(setup, at):
    """601条新增消息分七页处理，游标从最早页逐次推进。"""
    messages = [Message(-1000000000123, i, "普通群消息", at, 55) for i in range(2, 603)]
    engine, telegram, identity = engine_for(setup, at, messages)
    engine.store.checkpoint(identity.peer_id, 1, at)
    lottery = Lottery(engine, identity)
    for _ in range(7):
        await lottery.scan_page()
    assert engine.store.scan(identity.peer_id)["cursor"] == 602
    assert telegram.calls == []


def test_topic_continuation_not_same_as_any_new_message():
    """同话题有可解释共同内容，转到完全不同主题则取消。"""
    assert same_topic("Python服务怎么部署更稳定", "这个Python服务建议增加健康检查")
    assert not same_topic("Python服务怎么部署更稳定", "明天天气会下雨吗")


def test_seven_day_dedup_not_permanent_across_different_messages(setup, at):
    """同消息永久去重，跨群内容指纹到七日后过期。"""
    control, identity, _ = setup
    old = at - timedelta(days=8)
    record = control.store.lottery(identity.peer_id, 1, "same", None, old, "MANUAL", "", "匿名")
    assert control.store.find_lottery(identity.peer_id, 1, "same", None, at) == record
    assert control.store.find_lottery(identity.peer_id + 1, 2, "same", None, at) is None


def test_reservation_recovery_never_refunds_calling_unknown(setup, at):
    """崩溃后未调用预留释放，已调用预留保持占用且不重做。"""
    control, identity, _ = setup
    ready = control.store.reserve("ready", identity.peer_id, "chat", "a", None, at, 30)
    assert ready is not None
    calling = control.store.reserve("calling", identity.peer_id, "chat", "b", None, at, 30)
    control.store.action_status(calling, "CALLING")
    control.store.recover()
    assert control.store.quota(at.date().isoformat(), identity.peer_id) == 1
    assert control.store.reserve("calling", identity.peer_id, "chat", "b", None, at, 30) is None
    assert control.store.reserve("ready", identity.peer_id, "chat", "a", None, at, 30) is not None
