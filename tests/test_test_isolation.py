from __future__ import annotations

from datetime import datetime

import pytest


def test_fake_clock_advances_across_shanghai_midnight(fake_clock) -> None:
    fake_clock.advance(seconds=120)
    assert fake_clock.now() == datetime(2026, 8, 30, 0, 1, tzinfo=fake_clock.tz)


@pytest.mark.asyncio
async def test_fake_sender_records_controlled_actions_without_real_client(fake_sender) -> None:
    await fake_sender.send_group_text(1, "签到")
    await fake_sender.click_callback(1, 2, 0, 0)
    assert fake_sender.calls == [("send_group_text", 1, "签到"), ("click_callback", 1, 2, 0, 0)]


def test_network_isolation_blocks_socket() -> None:
    import socket

    try:
        socket.create_connection(("example.com", 443))
    except RuntimeError as exc:
        assert "network" in str(exc).lower()
    else:
        raise AssertionError("test network must be blocked")
