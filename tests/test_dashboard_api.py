from __future__ import annotations

import asyncio
from unittest.mock import Mock

from src.state_store import PauseKind, StateStore


def _set_env(monkeypatch) -> None:
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x" * 32)
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.setenv("TARGET_GROUPS", "https://t.me/allowed")
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")


def test_dashboard_reads_durable_group_state(monkeypatch, app, runtime_paths) -> None:
    _set_env(monkeypatch)
    store = StateStore(runtime_paths.state_db_path)
    store.increment_sent("https://t.me/allowed", 30)
    client = app.test_client()

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    dashboard = response.get_json()["dashboard"]
    assert dashboard["groups"][0]["sent_count"] == 1


def test_audit_and_manual_resume_work_before_loop_start(monkeypatch, app, runtime_paths) -> None:
    _set_env(monkeypatch)
    store = StateStore(runtime_paths.state_db_path)
    store.pause_group("https://t.me/allowed", PauseKind.SAFETY, "complaint")
    client = app.test_client()

    audit = client.get("/api/audit").get_json()["events"]
    assert audit[0]["event_type"] == "group_paused"
    response = client.post(
        "/api/groups/resume", json={"group": "https://t.me/allowed"}
    )
    assert response.status_code == 200
    assert response.get_json()["group"]["pause_kind"] == "none"


def test_event_bus_structured_decision_event(manager) -> None:
    bus = manager.event_bus
    bus._history.clear()
    asyncio.run(bus.emit_decision("group", "skip", "probability"))
    event = list(bus._history)[-1][1]
    assert event == {
        "type": "decision",
        "data": {"group": "group", "action": "skip", "reason": "probability"},
    }


def test_config_api_rejects_invalid_groups_before_persisting(monkeypatch, app) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr("web_app.save_settings", lambda settings, **kwargs: None)
    client = app.test_client()
    response = client.post(
        "/api/config",
        json={
            "api_id": 1,
            "api_hash": "x" * 32,
            "phone": "+1",
            "target_groups": ["not-a-group"],
            "min_interval": 10,
            "max_interval": 20,
        },
    )
    assert response.status_code == 422
    assert response.get_json()["field"] == "target_groups"


def test_config_api_normalizes_message_file_group_keys(monkeypatch, app, runtime_paths) -> None:
    _set_env(monkeypatch)
    saved = Mock()
    monkeypatch.setattr("web_app.save_settings", saved)
    client = app.test_client()

    response = client.post(
        "/api/config",
        json={
            "api_id": 1,
            "api_hash": "x" * 32,
            "phone": "+1",
            "target_groups": ["@allowed"],
            "message_files": {"@allowed": "messages.txt"},
            "min_interval": 10,
            "max_interval": 20,
        },
    )

    assert response.status_code == 200
    settings = saved.call_args.args[0]
    assert saved.call_args.kwargs == {"runtime_paths": runtime_paths}
    assert settings.target_groups == ["https://t.me/allowed"]
    assert settings.message_files == {"https://t.me/allowed": "messages.txt"}
