from __future__ import annotations

import sqlite3

from src.lottery_models import AlertStatus, ParticipationStatus
from src.lottery_store import LotteryStore


def test_set_pending_alert_persists_safe_summary_without_changing_participation_status(tmp_path) -> None:
    """受控提醒接口只持久化安全摘要，且不改变既有参与状态。"""
    path = tmp_path / "state.db"
    store = LotteryStore(path)
    lottery_id = store.record_entry(
        1,
        2,
        "fingerprint",
        None,
        ParticipationStatus.MANUAL_REQUIRED.value,
        "旧摘要",
    )

    assert store.set_pending_alert(lottery_id, "需要人工确认按钮") is True

    alerts, total = store.list_alerts()
    assert total == 1
    assert len(alerts) == 1
    assert alerts[0].id == lottery_id
    assert alerts[0].participation_status is ParticipationStatus.MANUAL_REQUIRED
    assert alerts[0].alert_status is AlertStatus.PENDING
    assert alerts[0].summary == "需要人工确认按钮"
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT participation_status, alert_status, summary FROM lottery_entry WHERE id=?",
            (lottery_id,),
        ).fetchone()
    assert row == ("MANUAL_REQUIRED", "PENDING", "需要人工确认按钮")
    assert "callback" not in row[2].lower()
    assert "token" not in row[2].lower()


def test_set_pending_alert_uses_immediate_write_transaction(monkeypatch, tmp_path) -> None:
    """提醒状态变更必须通过仓储的 BEGIN IMMEDIATE 写事务执行。"""
    store = LotteryStore(tmp_path / "state.db")
    lottery_id = store.record_entry(1, 2, "fingerprint", None, ParticipationStatus.DETECTED.value)
    write_calls: list[object] = []
    original_write = store._write

    def record_write(operation):
        write_calls.append(operation)
        return original_write(operation)

    monkeypatch.setattr(store, "_write", record_write)

    assert store.set_pending_alert(lottery_id, "人工处理") is True
    assert len(write_calls) == 1


def test_opening_pending_unknown_result_alert_keeps_status_and_unfreezes_group(tmp_path) -> None:
    """打开未知结果提醒仅关闭提醒并解冻对应群，不能改写参与状态。"""
    store = LotteryStore(tmp_path / "state.db")
    lottery_id = store.record_entry(
        10,
        20,
        "fingerprint",
        None,
        ParticipationStatus.RESULT_UNKNOWN.value,
    )
    store.freeze(10, ParticipationStatus.RESULT_UNKNOWN.value, lottery_id)
    assert store.set_pending_alert(lottery_id, "结果待人工确认") is True

    opened = store.open_alert(lottery_id)

    assert opened is not None
    assert opened.participation_status is ParticipationStatus.RESULT_UNKNOWN
    assert opened.alert_status is AlertStatus.OPENED
    assert store.is_frozen(10) is False


def test_set_pending_alert_for_missing_entry_fails_closed(tmp_path) -> None:
    """不存在的记录不得创建提醒或写入任何状态。"""
    store = LotteryStore(tmp_path / "state.db")

    assert store.set_pending_alert(999, "不应写入") is False
    assert store.list_alerts() == ([], 0)
