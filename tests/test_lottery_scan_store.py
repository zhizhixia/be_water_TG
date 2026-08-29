from __future__ import annotations

import sqlite3

import pytest

from src.lottery_store import LotteryStore


def test_scan_state_returns_safe_empty_cursor_for_unseen_peer(tmp_path) -> None:
    """首次扫描未知群时必须返回不推进游标的安全状态。"""
    state = LotteryStore(tmp_path / "state.db").scan_state(123)

    assert state == {
        "peer_id": 123,
        "cursor_message_id": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
    }


def test_commit_scan_batch_persists_cursor_across_reopen(tmp_path) -> None:
    """批次提交必须原子写入游标，重开仓储后仍可读取。"""
    path = tmp_path / "state.db"
    LotteryStore(path).commit_scan_batch(123, 456, [])

    assert LotteryStore(path).scan_state(123)["cursor_message_id"] == 456


def test_commit_scan_batch_never_moves_cursor_backward(tmp_path) -> None:
    """迟到的扫描批次不得把已提交游标回退。"""
    store = LotteryStore(tmp_path / "state.db")
    store.commit_scan_batch(123, 456)
    store.commit_scan_batch(123, 400)

    assert store.scan_state(123)["cursor_message_id"] == 456


def test_commit_scan_batch_rolls_back_when_database_rejects_update(tmp_path) -> None:
    """批次提交异常时必须保留已成功提交的旧游标。"""
    path = tmp_path / "state.db"
    store = LotteryStore(path)
    store.commit_scan_batch(123, 456)
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TRIGGER reject_scan_cursor_update
            BEFORE UPDATE ON lottery_scan_state
            BEGIN SELECT RAISE(ABORT, 'cursor rejected'); END;
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="cursor rejected"):
        store.commit_scan_batch(123, 789)

    assert LotteryStore(path).scan_state(123)["cursor_message_id"] == 456


def test_commit_scan_batch_ignores_media_and_callback_messages(tmp_path) -> None:
    """扫描提交只能保存游标，消息中的媒体和回调不得进入数据库。"""
    path = tmp_path / "state.db"
    messages = [{"media": object(), "callback": lambda: None}]
    LotteryStore(path).commit_scan_batch(123, 456, messages)

    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert db.execute("SELECT peer_id, cursor_message_id FROM lottery_scan_state").fetchall() == [(123, 456)]
    assert "lottery_scan_message" not in tables


def test_commit_scan_batch_uses_immediate_write_transaction(monkeypatch, tmp_path) -> None:
    """扫描游标提交必须通过仓储的 BEGIN IMMEDIATE 写事务。"""
    store = LotteryStore(tmp_path / "state.db")
    statements: list[str] = []
    original_connect = store._connect

    def traced_connect() -> sqlite3.Connection:
        db = original_connect()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_connect", traced_connect)
    store.commit_scan_batch(123, 456)

    assert any(statement.strip().upper() == "BEGIN IMMEDIATE" for statement in statements)
