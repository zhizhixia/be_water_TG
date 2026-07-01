"""EventBus 多订阅者隔离与历史重放测试。"""
from __future__ import annotations

import asyncio
import queue

import pytest

from web_manager import EventBus


def test_multi_subscribers_all_receive() -> None:
    """多订阅者各自独立收到同一事件，不再互相偷走。"""
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    asyncio.run(bus.emit_log("info", "hello"))
    e1 = q1.get(timeout=1)
    e2 = q2.get(timeout=1)
    assert e1[1]["data"]["message"] == "hello"
    assert e2[1]["data"]["message"] == "hello"


def test_subscribe_replays_history() -> None:
    """先发事件再订阅，历史事件按 seq 回放给新订阅者。"""
    bus = EventBus()
    asyncio.run(bus.emit_log("info", "first"))
    asyncio.run(bus.emit_log("info", "second"))
    q = bus.subscribe()
    seq1, ev1 = q.get(timeout=1)
    seq2, ev2 = q.get(timeout=1)
    assert seq1 < seq2
    assert ev1["data"]["message"] == "first"
    assert ev2["data"]["message"] == "second"


def test_subscribe_with_last_seq_skips_old() -> None:
    """传 last_seq=N 只回放 seq>N 的事件。"""
    bus = EventBus()
    asyncio.run(bus.emit_log("info", "a"))
    asyncio.run(bus.emit_log("info", "b"))
    # 取第一个事件的 seq
    tmp = bus.subscribe()
    first_seq, _ = tmp.get(timeout=1)
    q = bus.subscribe(last_seq=first_seq)
    seq, ev = q.get(timeout=1)
    assert seq == first_seq + 1
    assert ev["data"]["message"] == "b"


def test_ring_buffer_evicts_oldest() -> None:
    """环形缓冲满后丢弃最旧，seq 单调递增不回退。"""
    bus = EventBus()
    # EventBus 默认 history 容量 500，这里塞 505 条
    for i in range(505):
        asyncio.run(bus.emit_log("info", f"msg{i}"))
    q = bus.subscribe()
    seq, ev = q.get(timeout=1)
    # 最旧的 5 条被丢，回放从第 6 条开始
    assert ev["data"]["message"] == "msg5"
    assert seq == 6


def test_seq_monotonic() -> None:
    """seq 严格单调递增。"""
    bus = EventBus()
    q = bus.subscribe()
    for i in range(5):
        asyncio.run(bus.emit_log("info", f"m{i}"))
    seqs = [q.get(timeout=1)[0] for _ in range(5)]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5


def test_unsubscribe_on_close() -> None:
    """subscribe 返回的句柄可 unregister，避免泄漏。"""
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    asyncio.run(bus.emit_log("info", "after"))
    # 取消订阅后不应再收到事件
    assert q.empty()