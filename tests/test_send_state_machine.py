"""SendLoopManager 状态机转移测试。"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.config import Settings
from ui.send_loop import SendRuntime, SendState
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


def _start_arguments(tmp_path: Path) -> tuple[object, Settings, object]:
    """构造不接触网络的最小启动参数。"""
    settings = Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+861****8000",
        target_groups=["https://t.me/example"],
        state_db_path=str(tmp_path / "state" / "be_water.db"),
    )
    return object(), settings, object()


@pytest.mark.parametrize("failure_stage", ["construction", "start"])
def test_thread_launch_failure_rolls_back_all_start_state_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    """线程构造或 start 失败均返回固定结果、清空字段并允许再次启动。"""
    manager = SendLoopManager()
    sender, settings, message_manager = _start_arguments(tmp_path)

    class StartFailureThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("boom")

    def construction_failure(**_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "web_manager.threading.Thread",
        construction_failure if failure_stage == "construction" else StartFailureThread,
    )

    result = manager.start(sender, settings, message_manager)

    assert not result.ok
    assert result.reason == "发送线程启动失败"
    assert manager.state == SendState.STOPPED
    assert manager._thread is None
    assert manager._active_settings is None
    assert manager._state_store is None
    assert manager._runtime == SendRuntime()
    assert not manager.stop_event.is_set()
    assert Path(settings.state_db_path).exists()

    class StartedThread:
        def __init__(self, **_kwargs: object) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr("web_manager.threading.Thread", StartedThread)
    retry = manager.start(sender, settings, message_manager)
    assert retry.ok
    assert manager.state == SendState.STARTING
    assert manager._thread is not None


def test_fast_finishing_thread_cannot_be_restored_after_it_clears_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """线程在 start 返回前结束时，start 不得把已清理线程引用反向写回。"""
    manager = SendLoopManager()
    sender, settings, message_manager = _start_arguments(tmp_path)

    def finish_immediately(*_args: object) -> None:
        with manager._state_lock:
            manager.transition(SendState.STOPPED)
            manager._thread = None
            manager._active_settings = None
            manager._state_store = None

    monkeypatch.setattr(manager, "_run_loop", finish_immediately)

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs: object) -> None:
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr("web_manager.threading.Thread", ImmediateThread)

    result = manager.start(sender, settings, message_manager)

    assert result.ok
    assert manager.state == SendState.STOPPED
    assert manager._thread is None
    assert manager._active_settings is None
    assert manager._state_store is None
