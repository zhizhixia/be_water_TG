"""应用运行态的线程安全与非敏感快照契约测试。"""
from __future__ import annotations

import threading

import pytest

from src.runtime_state import ApplicationRuntimeState


def test_runtime_state_starts_with_fixed_safe_codes() -> None:
    """初始快照只包含配置布尔值和固定状态码。"""
    state = ApplicationRuntimeState()

    assert state.snapshot() == {
        "configured": False,
        "migration_status": "not_started",
        "setup_status": "not_checked",
    }


def test_runtime_state_updates_all_fields_atomically() -> None:
    """单次更新应原子替换完整快照，读者看不到混合状态。"""
    state = ApplicationRuntimeState()
    expected = {
        "configured": True,
        "migration_status": "ready",
        "setup_status": "configured",
    }
    stop = threading.Event()
    started = threading.Event()
    observed_once = threading.Event()
    observed: list[dict[str, object]] = []

    def reader() -> None:
        started.set()
        while not stop.is_set():
            observed.append(state.snapshot())
            observed_once.set()

    worker = threading.Thread(target=reader)
    worker.start()
    assert started.wait(1)
    assert observed_once.wait(1)
    state.update(**expected)
    stop.set()
    worker.join(timeout=1)

    allowed = [
        {
            "configured": False,
            "migration_status": "not_started",
            "setup_status": "not_checked",
        },
        expected,
    ]
    assert observed
    assert all(snapshot in allowed for snapshot in observed)
    assert state.snapshot() == expected


def test_runtime_state_rejects_non_fixed_status_codes() -> None:
    """拒绝将路径、异常或其他任意文本写入公开运行态。"""
    state = ApplicationRuntimeState()

    with pytest.raises(ValueError, match="migration_status"):
        state.update(migration_status="C:/private/state.db")
    with pytest.raises(ValueError, match="setup_status"):
        state.update(setup_status="token=secret")

    assert state.snapshot() == {
        "configured": False,
        "migration_status": "not_started",
        "setup_status": "not_checked",
    }
