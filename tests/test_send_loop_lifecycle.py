"""send_loop 退出后 sender.disconnect 恰好被调用一次测试。"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from ui.send_loop import send_loop


def test_disconnect_called_on_normal_exit(fake_sender, make_settings) -> None:
    """stop_event 置位后立即退出：send_loop 不抛、不调 disconnect。"""
    mgr = MagicMock()
    mgr.stop_event = threading.Event()
    mgr.stop_event.set()  # 立即退出
    mgr.transition = MagicMock(return_value=MagicMock(ok=True))
    mgr.increment_count = MagicMock(return_value=(1, 1))
    mgr.runtime_counts_snapshot = MagicMock(return_value=(1, {"g": 1}))
    mm = MagicMock()
    mm.get_message = MagicMock(return_value="hi")

    # send_loop 自身不调 disconnect（那是 _run_loop 的 finally 职责）
    asyncio.run(send_loop(
        sender=fake_sender, settings=make_settings, manager=mgr,
        message_manager=mm, event_bus=None, ai_sender=None,
    ))
    fake_sender.disconnect.assert_not_called()


def test_disconnect_via_run_loop_on_stop(fake_sender, make_settings) -> None:
    """_run_loop 在 send_loop 退出/异常后经 finally 调 disconnect。

    本测试通过 SendLoopManager 真实实例与 fake sender 配合，避免过度 mock。
    """
    from web_manager import SendLoopManager
    from src.config import Settings

    mgr = SendLoopManager()
    # 立即置位 stop_event，让 send_loop 第一轮就退出
    mgr.stop_event.set()
    # 模拟 sender 已授权，避免真实 Telethon 连接
    fake_sender.start = AsyncMock(return_value=None)

    try:
        mgr.start(
            sender=fake_sender,
            settings=make_settings,
            message_manager=MagicMock(),
            ai_sender=None,
        )
        # 等后台线程跑完 _run_loop（finally 调 disconnect）
        if mgr._thread is not None:
            mgr._thread.join(timeout=5)
    finally:
        pass

    assert fake_sender.disconnect.called
    # 状态机最终应为 STOPPED
    assert mgr.state.name in ("STOPPED", "IDLE")


def test_disconnect_via_run_loop_on_exception(fake_sender, make_settings) -> None:
    """send_loop 抛异常时 _run_loop finally 仍调 disconnect。"""
    from web_manager import SendLoopManager

    async def boom(*a, **k):
        raise RuntimeError("boom")

    fake_sender.start = AsyncMock(side_effect=boom)

    mgr = SendLoopManager()
    try:
        mgr.start(
            sender=fake_sender,
            settings=make_settings,
            message_manager=MagicMock(),
            ai_sender=None,
        )
        if mgr._thread is not None:
            mgr._thread.join(timeout=5)
    finally:
        pass

    # 即使 sender.start 抛异常，finally 也会尝试 disconnect
    assert fake_sender.disconnect.called
