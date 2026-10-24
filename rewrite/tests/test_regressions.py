"""跨日、排队授权与扫描失败的生产路径回归。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bewater.content import AI
from bewater.conversation import Conversation
from bewater.engine import Engine
from bewater.lottery import Lottery
from bewater.models import Action, Button, DomainError, Message
from conftest import FakeTelegram


@pytest.mark.asyncio
async def test_next_day_does_not_repeat_yesterday_unanswered_pause(setup, at):
    """跨日恢复后，昨天的旧发言不能再次把整个新日封住。"""
    control, identity, values = setup
    yesterday = at - timedelta(days=1)
    control.store.set_state(
        identity.peer_id,
        pause="unanswered",
        pause_day=yesterday.date().isoformat(),
        last_outbound=yesterday.isoformat(),
        last_activity=(yesterday - timedelta(minutes=5)).isoformat(),
    )
    engine = Engine(control, FakeTelegram(identity), values, clock=lambda: at)
    await Conversation(engine, identity).observe([], [])
    assert not control.store.state(identity.peer_id).get("pause")


@pytest.mark.asyncio
async def test_queued_ai_request_checks_consent_after_semaphore():
    """排队期间撤回同意后，取得并发槽也不能发出新请求。"""
    ai = AI.__new__(AI)
    ai.slots = asyncio.Semaphore(0)
    consent = {"value": True}
    ai.allowed = lambda: consent["value"]
    create = AsyncMock()
    ai.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    task = asyncio.create_task(ai.request("匿名测试", "非真实群消息"))
    await asyncio.sleep(0)
    consent["value"] = False
    ai.slots.release()
    with pytest.raises(DomainError):
        await task
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_failure_preserves_cursor_and_waits_full_interval(setup, at):
    """失败不越过未处理消息，且不会十秒后就重扫。"""
    control, identity, values = setup
    gateway = FakeTelegram(identity)
    gateway.pinned = AsyncMock(side_effect=OSError("匿名失败"))
    engine = Engine(control, gateway, values, clock=lambda: at)
    await engine.resolve_groups()
    control.store.checkpoint(identity.peer_id, 123, at)
    job = Lottery(engine, identity)
    with pytest.raises(OSError):
        await job.step()
    scan = control.store.scan(identity.peer_id)
    assert scan["cursor"] == 123 and scan["error"]
    assert job.next_scan - monotonic() > 14 * 60
    await job.step()
    gateway.pinned.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        None,
        "处理中",
        "验证后可参与",
        "参与成功",
        "已参与",
        "参与成功，等待开奖",
        "参与成功，但需转发",
    ],
)
async def test_callback_transport_ack_is_not_automatically_participation(reply, at):
    """真实 SDK 返回类型；只有明确确认才记执行，含糊响应不能冒充成功。"""
    from telethon import types
    from bewater.telegram import Telegram

    gateway = Telegram.__new__(Telegram)
    peer = -1000000000123
    gateway.peers = {peer: types.InputPeerChannel(123, 456)}
    gateway.client = AsyncMock(
        return_value=types.messages.BotCallbackAnswer(cache_time=0, message=reply)
    )
    original = Message(
        peer, 20, "抽奖", at, 9, buttons=((Button("参与", "callback", b"public-test"),),)
    )
    action = Action("callback", 20, row=0, column=0)
    if reply in {"参与成功", "已参与", "参与成功，等待开奖"}:
        assert await gateway.perform(peer, action, original) is None
    else:
        with pytest.raises(DomainError):
            await gateway.perform(peer, action, original)
    gateway.client.assert_awaited_once()
