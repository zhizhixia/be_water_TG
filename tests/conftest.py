"""公共测试 fixtures，消除各测试文件重复的 setup 代码。"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from src.config import Settings


@pytest.fixture
def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置 load_settings 所需的基础环境变量，并阻止加载真实 .env 文件。"""
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "a" * 32)
    monkeypatch.setenv("PHONE", "+8613800138000")
    monkeypatch.setenv("MIN_INTERVAL", "60")
    monkeypatch.setenv("MAX_INTERVAL", "180")
    monkeypatch.setenv("GROUP_GAP_MIN", "1")
    monkeypatch.setenv("GROUP_GAP_MAX", "1")


@pytest.fixture
def make_settings() -> Settings:
    """构造一个合法的 Settings 实例（最小必填字段齐全）。"""
    return Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+8613800138000",
        target_groups=["https://t.me/test_group"],
    )


@pytest.fixture
def fake_sender() -> AsyncMock:
    """mock TelegramSender：所有方法是 AsyncMock，默认 connect/disconnect/send_message 无副作用。"""
    sender = AsyncMock()
    sender.connect = AsyncMock(return_value=None)
    sender.start = AsyncMock(return_value=None)
    sender.send_message = AsyncMock(return_value=None)
    sender.typing_indicator = AsyncMock(return_value=None)
    sender.disconnect = AsyncMock(return_value=None)
    sender.get_recent_messages = AsyncMock(return_value=[])
    return sender


@pytest.fixture
def stop_event() -> threading.Event:
    """send_loop 停止信号事件，测试可控触发停止。"""
    return threading.Event()


@pytest.fixture
def asyncio_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    """独立 asyncio 事件循环，测试 send_loop 协程用。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)