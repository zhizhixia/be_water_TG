"""SendLoopManager 状态机转移测试。"""
from __future__ import annotations

import threading

import pytest

from ui.send_loop import SendState
from web_manager import SendLoopManager


def test_initial_state_idle() -> None:
    """新构造的 manager 状态为 IDLE。"""
    m = SendLoopManager()
    assert m.state == SendState.IDLE


def test_legal_transition_idle_to_starting() -> None:
    """IDLE → STARTING 合法。"""
    m = SendLoopManager()
    assert m.transition(SendState.STARTING).ok
    assert m.state == SendState.STARTING


def test_illegal_transition_idle_to_paused() -> None:
    """IDLE → PAUSED 非法，返回失败且状态不变。"""
    m = SendLoopManager()
    result = m.transition(SendState.PAUSED)
    assert not result.ok
    assert m.state == SendState.IDLE
    assert "illegal" in result.reason.lower()


def test_full_lifecycle() -> None:
    """完整生命周期：IDLE→STARTING→RUNNING→PAUSING→PAUSED→RUNNING→STOPPING→STOPPED→IDLE。"""
    m = SendLoopManager()
    assert m.transition(SendState.STARTING).ok
    assert m.transition(SendState.RUNNING).ok
    assert m.transition(SendState.PAUSING).ok
    assert m.transition(SendState.PAUSED).ok
    assert m.transition(SendState.RUNNING).ok
    assert m.transition(SendState.STOPPING).ok
    assert m.transition(SendState.STOPPED).ok
    assert m.transition(SendState.IDLE).ok


def test_starting_to_waiting_code() -> None:
    """STARTING → WAITING_CODE 合法。"""
    m = SendLoopManager()
    m.transition(SendState.STARTING)
    assert m.transition(SendState.WAITING_CODE).ok
    assert m.state == SendState.WAITING_CODE


def test_waiting_code_to_running() -> None:
    """WAITING_CODE → RUNNING 合法。"""
    m = SendLoopManager()
    m.transition(SendState.STARTING)
    m.transition(SendState.WAITING_CODE)
    assert m.transition(SendState.RUNNING).ok


def test_stop_from_any_state() -> None:
    """任意状态都能转 STOPPING。"""
    m = SendLoopManager()
    m.transition(SendState.STARTING)
    m.transition(SendState.RUNNING)
    assert m.transition(SendState.STOPPING).ok


def test_concurrent_start_no_two_loops() -> None:
    """并发调用 transition(STARTING) 只能成功一次。"""
    m = SendLoopManager()
    results = []
    barrier = threading.Barrier(10)

    def attempt():
        barrier.wait()
        results.append(m.transition(SendState.STARTING).ok)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 1