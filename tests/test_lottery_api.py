"""抽奖 Web API 与 SSE 安全契约测试。"""
from __future__ import annotations

import asyncio
import json

from src.lottery_store import LotteryStore
from web_app import app, manager


def _set_env(monkeypatch, db_path) -> None:
    """设置独立的抽奖状态库配置。"""
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x" * 32)
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.setenv("TARGET_GROUPS", "https://t.me/allowed")
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")
    monkeypatch.setenv("STATE_DB_PATH", str(db_path))


def test_lottery_list_is_paginated_json_and_read_only(monkeypatch, tmp_path) -> None:
    """GET 列表始终返回稳定 JSON，且不会关闭提醒或解冻群组。"""
    path = tmp_path / "state.db"
    _set_env(monkeypatch, path)
    store = LotteryStore(path)
    first = store.record_entry(1, 10, "one", None, "RESULT_UNKNOWN", "第一条")
    second = store.record_entry(2, 20, "two", None, "DETECTED", "第二条")
    store.freeze(1, "RESULT_UNKNOWN", first)

    response = app.test_client().get("/api/lotteries?offset=0&limit=1")

    assert response.status_code == 200
    assert response.content_type.startswith("application/json")
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["total"] == 0
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert payload["alerts"] == payload["items"] == []
    assert "callback" not in json.dumps(payload).lower()
    assert store.is_frozen(1) is True
    assert store.open_alert(first).alert_status.value == "OPENED"


def test_open_lottery_closes_alert_unfreezes_unknown_and_returns_safe_link(monkeypatch, tmp_path) -> None:
    """POST open 原子关闭提醒、解冻未知结果，并返回消息 ID 的降级链接。"""
    path = tmp_path / "state.db"
    _set_env(monkeypatch, path)
    store = LotteryStore(path)
    lottery_id = store.record_entry(12345, 678, "one", None, "RESULT_UNKNOWN", "需要人工查看")
    store.freeze(12345, "RESULT_UNKNOWN", lottery_id)

    response = app.test_client().post(f"/api/lotteries/{lottery_id}/open")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message_id"] == 678
    assert payload["fallback"] is True
    assert payload["url"].startswith("https://t.me/c/12345")
    assert store.is_frozen(12345) is False
    assert store.list_alerts()[1] == 0


def test_cleanup_requires_stopped_state_and_explicit_chinese_confirmation(monkeypatch, tmp_path) -> None:
    """清理只允许 IDLE/STOPPED，且缺少中文二次确认绝不改变持久化。"""
    path = tmp_path / "state.db"
    _set_env(monkeypatch, path)
    store = LotteryStore(path)
    lottery_id = store.record_entry(1, 2, "old", "2026-08-01", "DETECTED", "旧记录")
    manager._state = manager.state.RUNNING
    client = app.test_client()

    blocked = client.post("/api/lotteries/cleanup", json={"before": "2026-08-02", "confirm": "确认清理"})
    assert blocked.status_code == 409
    assert "停止" in blocked.get_json()["error"]
    manager._state = manager.state.IDLE
    unconfirmed = client.post("/api/lotteries/cleanup", json={"before": "2026-08-02", "confirm": ""})
    assert unconfirmed.status_code == 400
    assert "确认" in unconfirmed.get_json()["error"]
    assert store.open_alert(lottery_id) is not None


def test_lottery_sse_events_redact_sensitive_fields() -> None:
    """抽奖 SSE 只发布允许类型及摘要，递归剔除 callback 与凭据字段。"""
    manager.event_bus._history.clear()
    asyncio.run(manager.emit_lottery_event("lottery_alert", {
        "id": 1, "summary": "待处理", "callback_data": "secret", "token": "secret",
        "nested": {"api_key": "secret", "message_id": 2},
    }))
    event = list(manager.event_bus._history)[-1][1]
    assert event["type"] == "lottery_alert"
    assert event["data"] == {"id": 1, "summary": "待处理", "nested": {"message_id": 2}}
