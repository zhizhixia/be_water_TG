"""主循环与真实抽奖组件的安全接线回归。"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.config import Settings
from src.group_identity import GroupIdentity
from src.state_store import StateStore
from ui.send_loop import send_loop


def test_startup_locks_migrates_and_uses_only_peer_ids_for_checkin_and_state(
    monkeypatch, tmp_path
) -> None:
    """主循环必须在锁后迁移，并让签到和状态仓储只收到稳定 peer_id。"""
    events: list[object] = []

    class Lock:
        def __init__(self, *_args: object) -> None:
            events.append("lock_created")

        def acquire(self) -> bool:
            events.append("lock")
            return True

        def release(self) -> None:
            events.append("release")

    class RecordingStore(StateStore):
        def migrate_group_identities(self, identities: list[object]) -> None:
            events.append(("migrate", [identity.peer_id for identity in identities]))
            super().migrate_group_identities(identities)

        def reconcile_pending_reservations(self, groups: list[str], daily_limit: int):
            events.append(("reconcile", groups))
            return super().reconcile_pending_reservations(groups, daily_limit)

    monkeypatch.setattr("ui.send_loop.InstanceLock", Lock)
    settings = Settings(
        api_id=1, api_hash="x" * 32, phone="+1", target_groups=["https://t.me/allowed"],
        state_db_path=str(tmp_path / "state.db"), lottery_enabled=False, daily_checkin_enabled=True,
    )
    manager = MagicMock(stop_event=threading.Event())
    manager.runtime_counts_snapshot.return_value = (0, {})
    sender = MagicMock()
    sender.resolve_group_identity = AsyncMock(return_value=GroupIdentity(101, "https://t.me/allowed", "允许群"))
    async def checkin(peer_id: int, text: str) -> int:
        manager.stop_event.set()
        return 1

    sender.send_group_text = AsyncMock(side_effect=checkin)
    store = RecordingStore(settings.state_db_path)

    asyncio.run(send_loop(sender, settings, manager, MagicMock(), state_store=store))

    assert events[:3] == ["lock_created", "lock", ("migrate", [101])]
    assert sender.send_group_text.await_args.args == (101, "签到")
    assert ("reconcile", ["101"]) in events
    assert store.get_group_state("101").sent_count == 1
    assert store.list_group_states(["https://t.me/allowed"])[0].sent_count == 0
