from __future__ import annotations

import json

from src.instance_lock import InstanceLock


def test_live_lock_is_rejected_and_normal_release_allows_next_instance(tmp_path) -> None:
    """活锁必须拒绝第二实例，正常释放后方可重新获取。"""
    first = InstanceLock("account", str(tmp_path / "state.db"))
    assert first.acquire()
    assert not InstanceLock("account", str(tmp_path / "state.db")).acquire()
    first.release()
    assert InstanceLock("account", str(tmp_path / "state.db")).acquire()


def test_stale_lock_is_recovered_only_when_pid_creation_time_does_not_match(monkeypatch, tmp_path) -> None:
    """PID 重用不能视为活实例；仅创建时间不一致的锁可被安全回收。"""
    lock = InstanceLock("account", str(tmp_path / "state.db"))
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({"pid": 42, "created_at": 10.0, "account_digest": "x", "holder": "old"}), encoding="utf-8")
    monkeypatch.setattr("src.instance_lock._process_creation_time", lambda pid: 11.0 if pid == 42 else None)
    assert lock.acquire()
    metadata = json.loads(lock.path.read_text(encoding="utf-8"))
    assert metadata["pid"] > 0
    assert metadata["created_at"] > 0
    assert metadata["account_digest"]
    lock.release()
