"""/api/events 端点 last_event_id 回放与标准 SSE 格式测试。"""
from __future__ import annotations

import json

import pytest

from web_app import app, manager


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _reset_event_bus():
    """清理 EventBus 状态，保证每个测试从干净的 seq/history/subscribers 开始。"""
    manager.event_bus._history.clear()
    manager.event_bus._seq = 0
    manager.event_bus._subscribers.clear()


def _read_sse_chunks(resp, max_chunks: int = 5) -> list[bytes]:
    """从 SSE streaming response 读取最多 max_chunks 个 chunk，然后断开连接。"""
    gen = resp.response  # generate() 生成器，yield 返回 str
    chunks: list[bytes] = []
    for _ in range(max_chunks):
        try:
            val = next(gen)
        except StopIteration:
            break
        if isinstance(val, str):
            val = val.encode("utf-8")
        chunks.append(val)
    gen.close()  # 触发 GeneratorExit → generate() 执行 finally: unsubscribe(q)
    return chunks


def test_events_no_last_id_returns_current_status_and_new(client) -> None:
    """无 last_event_id：返回当前 status 首条 + 后续新事件。"""
    _reset_event_bus()
    import asyncio
    asyncio.run(manager.event_bus.emit_status("running"))

    resp = client.get("/api/events")
    chunks = _read_sse_chunks(resp, max_chunks=2)
    assert any(b"running" in c for c in chunks)
    # 应包含标准 SSE id 行（首条 status 用 id: 0 占位也可接受）
    assert any(b"id:" in c for c in chunks)


def test_events_with_last_event_id_replays(client) -> None:
    """?last_event_id=N 仅回放 seq>N 的历史事件。"""
    _reset_event_bus()
    import asyncio
    asyncio.run(manager.event_bus.emit_log("info", "old"))
    asyncio.run(manager.event_bus.emit_log("info", "new"))
    # 取第一个事件 seq（应为 1，因为 status 未发过——emit_log 走 _publish seq 自增）
    seq_old = list(manager.event_bus._history)[0][0]

    resp = client.get(f"/api/events?last_event_id={seq_old}")
    chunks = _read_sse_chunks(resp, max_chunks=2)
    # 应回放 seq>seq_old 的事件
    body = b"".join(chunks)
    assert b"new" in body
    # 关键断言：new 事件出现在某 chunk 中
    assert any(b"new" in c for c in chunks)


def test_events_invalid_last_event_id_falls_back_to_zero(client) -> None:
    """?last_event_id=非法值 解析失败时按 0 处理，回放全部历史。"""
    _reset_event_bus()
    import asyncio
    asyncio.run(manager.event_bus.emit_log("info", "x"))

    resp = client.get("/api/events?last_event_id=abc")
    assert resp.status_code == 200
    chunks = _read_sse_chunks(resp, max_chunks=2)
    assert any(b"x" in c for c in chunks)
