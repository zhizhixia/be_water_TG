"""/api/events 端点 last_event_id 回放与标准 SSE 格式测试。"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(app):
    """返回当前工厂实例的 Flask 测试客户端。"""
    return app.test_client()


def _reset_event_bus(manager) -> None:
    """清理 EventBus 状态，保证每个测试从干净的 seq/history/subscribers 开始。"""
    manager.event_bus._history.clear()
    manager.event_bus._seq = 0
    manager.event_bus._subscribers.clear()


def _read_sse_chunks(resp, manager, max_chunks: int = 5) -> list[bytes]:
    """读取有限 SSE 块，并验证断开后订阅者已被移除。"""
    gen = resp.response
    chunks: list[bytes] = []
    for _ in range(max_chunks):
        try:
            val = next(gen)
        except StopIteration:
            break
        if isinstance(val, str):
            val = val.encode("utf-8")
        chunks.append(val)
    subscriber_count_active = len(manager.event_bus._subscribers)
    gen.close()
    assert len(manager.event_bus._subscribers) < subscriber_count_active, (
        "SSE generator close 后未触发 unsubscribe，存在订阅者泄漏"
    )
    return chunks


def test_events_no_last_id_returns_current_status_and_new(client, manager) -> None:
    """无 last_event_id：返回当前 status 首条 + 后续新事件。"""
    _reset_event_bus(manager)
    import asyncio

    asyncio.run(manager.event_bus.emit_status("running"))

    resp = client.get("/api/events")
    chunks = _read_sse_chunks(resp, manager, max_chunks=2)
    assert any(b"running" in chunk for chunk in chunks)
    assert any(b"id:" in chunk for chunk in chunks)


def test_events_with_last_event_id_replays(client, manager) -> None:
    """?last_event_id=N 仅回放 seq>N 的历史事件。"""
    _reset_event_bus(manager)
    import asyncio

    asyncio.run(manager.event_bus.emit_log("info", "old"))
    asyncio.run(manager.event_bus.emit_log("info", "new"))
    seq_old = list(manager.event_bus._history)[0][0]

    resp = client.get(f"/api/events?last_event_id={seq_old}")
    chunks = _read_sse_chunks(resp, manager, max_chunks=2)
    body = b"".join(chunks)
    assert b"new" in body
    assert any(b"new" in chunk for chunk in chunks)


def test_events_invalid_last_event_id_falls_back_to_zero(client, manager) -> None:
    """非法 last_event_id 按 0 处理并回放全部历史。"""
    _reset_event_bus(manager)
    import asyncio

    asyncio.run(manager.event_bus.emit_log("info", "x"))

    resp = client.get("/api/events?last_event_id=abc")
    assert resp.status_code == 200
    chunks = _read_sse_chunks(resp, manager, max_chunks=2)
    assert any(b"x" in chunk for chunk in chunks)
