"""wait_for_code 超时机制测试。"""
from __future__ import annotations

import asyncio

import pytest

from web_manager import EventBus


def test_wait_for_code_times_out(monkeypatch) -> None:
    """无验证码提交时，wait_for_code 在 CODE_TIMEOUT 秒后抛 asyncio.TimeoutError。"""
    bus = EventBus()
    monkeypatch.setattr("web_manager.EventBus.CODE_TIMEOUT", 0.2)  # 测试用短超时
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(bus.wait_for_code())


def test_submit_code_before_timeout_succeeds(monkeypatch) -> None:
    """超时前提交验证码，wait_for_code 正常返回。"""
    bus = EventBus()
    monkeypatch.setattr("web_manager.EventBus.CODE_TIMEOUT", 5)

    async def run2():
        task = asyncio.create_task(bus.wait_for_code())
        await asyncio.sleep(0.05)  # 让 wait_for_code 先发出 code_required 并 set future
        assert bus.submit_code("9999")
        return await asyncio.wait_for(task, timeout=2)

    code = asyncio.run(run2())
    assert code == "9999"


def test_submit_code_returns_false_when_no_future() -> None:
    """无 future 或已超时完成时，submit_code 返回 False。"""
    bus = EventBus()
    # 未经 wait_for_code 调用，_code_future 为 None
    assert bus.submit_code("12345") is False
