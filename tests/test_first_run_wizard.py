"""首次启动向导与配置保存后进程内即时状态更新回归测试。

覆盖契约：api_config_save 在保存成功后原子更新 runtime_state，
使 /api/health 与业务门禁在同一进程内立即反映已配置，无需重启。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.app_server import AppServer
from src.config import Settings
from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState
from web_app import create_app


def _ready_unconfigured_state() -> ApplicationRuntimeState:
    """构造已完成迁移但尚未配置的非敏感运行态。"""
    state = ApplicationRuntimeState()
    state.update(
        configured=False,
        migration_status="ready",
        setup_status="configuration_required",
    )
    return state


def _valid_payload() -> dict:
    """构造可一次通过校验与保存的完整配置载荷。"""
    return {
        "api_id": 12345,
        "api_hash": "b" * 32,
        "phone": "+861****8000",
        "target_groups": ["https://t.me/example"],
    }


def _current_settings() -> Settings:
    """构造 api_config_save 在保存前读取到的既有配置。"""
    return Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+861****8000",
        target_groups=["https://t.me/example"],
    )


def _unconfigured_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ApplicationRuntimeState, list[Settings], object]:
    """创建 ready 但未配置的隔离应用；stub 配置读写，返回状态、保存收集器与 app。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    saved: list[Settings] = []
    current = _current_settings()
    monkeypatch.setattr("web_app.load_settings", lambda **_kwargs: current)
    monkeypatch.setattr(
        "web_app.save_settings",
        lambda settings, **_kwargs: saved.append(settings),
    )
    state = _ready_unconfigured_state()
    app = create_app(runtime_paths=paths, runtime_state=state)
    return state, saved, app


def test_save_config_marks_runtime_state_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存成功后 runtime_state.configured 立即为 True，setup_status 为 configured。"""
    state, saved, app = _unconfigured_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post("/api/config", json=_valid_payload())

    assert response.status_code == 200
    assert len(saved) == 1
    snapshot = state.snapshot()
    assert snapshot["configured"] is True
    assert snapshot["setup_status"] == "configured"


def test_health_reflects_configured_after_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存前 /api/health 返回 configured:false，保存后立即返回 true。"""
    state, _saved, app = _unconfigured_app(tmp_path, monkeypatch)
    # 与 main.py 一致：同一 runtime_state 同时注入 Flask 与健康检查宿主
    AppServer(app, runtime_state=state)
    client = app.test_client()

    assert client.get("/api/health").get_json()["configured"] is False
    assert client.post("/api/config", json=_valid_payload()).status_code == 200
    assert client.get("/api/health").get_json()["configured"] is True


def test_start_route_opens_after_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存后业务门禁立即放行 /api/start，不再返回 409。"""
    state, _saved, app = _unconfigured_app(tmp_path, monkeypatch)
    client = app.test_client()

    # 未配置时门禁拦截业务路由
    blocked = client.post("/api/start", json={})
    assert blocked.status_code == 409

    assert client.post("/api/config", json=_valid_payload()).status_code == 200

    # 保存后门禁放行；load_settings 抛 ValueError 以区分门禁 409 与业务 422
    def raising_load_settings(**_kwargs: object) -> Settings:
        raise ValueError("empty target groups")

    monkeypatch.setattr("web_app.load_settings", raising_load_settings)
    opened = client.post("/api/start", json={})
    assert opened.status_code == 422
    assert opened.status_code != 409


def test_save_validation_failure_does_not_update_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """校验失败时 runtime_state 保持未配置，绝不提前放行。"""
    state, saved, app = _unconfigured_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post(
        "/api/config",
        json={"api_id": 12345, "target_groups": []},
    )

    assert response.status_code == 422
    assert saved == []
    snapshot = state.snapshot()
    assert snapshot["configured"] is False
    assert snapshot["setup_status"] == "configuration_required"
