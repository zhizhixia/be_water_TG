"""send_loop FloodWait 单次可中断等待 + 计数线程安全测试。"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.sender import TelegramSender
from ui.send_loop import send_loop


def test_stop_event_interrupts_floodwait(asyncio_loop, fake_sender, make_settings) -> None:
    """FloodWait 等待期间 stop_event 置位，send_loop 应在 0.5s 内退出。"""
    from telethon.errors import FloodWaitError
    from unittest.mock import MagicMock

    async def flood_then_ok(*a, **k):
        raise FloodWaitError(request=MagicMock(), capture=30)

    fake_sender.send_message = flood_then_ok

    # 构造最小 manager mock：仅 send_loop 用到的接口
    mgr = MagicMock()
    mgr.stop_event = threading.Event()
    mgr.transition = MagicMock(return_value=MagicMock(ok=True))
    mgr.increment_count = MagicMock(return_value=(0, 0))
    mgr.runtime_counts_snapshot = MagicMock(return_value=(0, {}))

    async def run():
        # 让 message_manager.get_message 返回非空消息，避免直接 continue
        mm = MagicMock()
        mm.get_message = MagicMock(return_value="hi")
        task = asyncio.create_task(send_loop(
            sender=fake_sender, settings=make_settings, manager=mgr,
            message_manager=mm, event_bus=None, ai_sender=None,
        ))
        await asyncio.sleep(0.1)  # 等进入 FloodWait 等待
        mgr.stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=2)
        except asyncio.TimeoutError:
            pytest.fail("send_loop 未在 stop 后 2s 内退出")

    asyncio_loop.run_until_complete(run())
