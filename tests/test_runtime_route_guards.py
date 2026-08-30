"""迁移与配置阻断态的 Web 路由前置门禁回归测试。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config import Settings
from src.runtime_migration import migrate_legacy_runtime_data
from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState
from web_app import create_app


BUSINESS_ROUTES = [
    ("post", "/api/start", {}),
    ("post", "/api/pause", {}),
    ("post", "/api/resume", {}),
    ("post", "/api/stop", {}),
    ("get", "/api/dashboard", None),
    ("get", "/api/lotteries", None),
    ("post", "/api/lotteries/1/open", {}),
    ("post", "/api/lotteries/cleanup", {"confirm": "确认清理", "before": "2026-01-01"}),
    ("get", "/api/audit", None),
    ("post", "/api/groups/resume", {"group": "https://t.me/example"}),
    ("post", "/api/code", {"code": "12345"}),
]


def _state(migration_status: str, configured: bool) -> ApplicationRuntimeState:
    """构造指定迁移/配置状态。"""
    state = ApplicationRuntimeState()
    state.update(
        migration_status=migration_status,
        configured=configured,
        setup_status="configured" if configured else "configuration_required",
    )
    return state


def _forbid_runtime_construction(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """将所有可能读盘或启动运行依赖的构造入口替换为失败探针。"""
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        calls.append("runtime")
        raise AssertionError("门禁前不得构造运行依赖")

    for target in (
        "web_app.load_settings",
        "web_app.StateStore",
        "web_app.LotteryStore",
        "web_app.MessageManager",
        "web_app.TelegramSender",
        "web_app.AIClient",
        "web_app.AISender",
    ):
        monkeypatch.setattr(target, forbidden)
    return calls


@pytest.mark.parametrize("migration_status", ["custom_database_requires_manual_copy", "database_destination_conflict", "migration_failed"])
def test_non_ready_routes_are_blocked_before_any_runtime_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migration_status: str,
) -> None:
    """所有配置和业务 API 在非 ready 时固定 409，且不读写运行文件。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    calls = _forbid_runtime_construction(monkeypatch)
    app = create_app(runtime_paths=paths, runtime_state=_state(migration_status, False))
    client = app.test_client()

    responses = [client.get("/api/config"), client.post("/api/config", json={"api_id": 1})]
    responses.extend(
        getattr(client, method)(url, json=payload) if payload is not None else getattr(client, method)(url)
        for method, url, payload in BUSINESS_ROUTES
    )

    assert {response.status_code for response in responses} == {409}
    assert len({response.get_json()["error"] for response in responses}) == 1
    assert calls == []
    assert not paths.env_path.exists()
    assert not paths.state_db_path.exists()
    assert not list(paths.sender_session_path.parent.iterdir())


def test_ready_but_unconfigured_allows_config_only_and_blocks_business_before_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ready 且未配置时仅配置 API 可继续，业务 API 必须在依赖构造前阻断。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    calls = _forbid_runtime_construction(monkeypatch)
    app = create_app(runtime_paths=paths, runtime_state=_state("ready", False))
    client = app.test_client()

    responses = [
        getattr(client, method)(url, json=payload) if payload is not None else getattr(client, method)(url)
        for method, url, payload in BUSINESS_ROUTES
    ]

    assert {response.status_code for response in responses} == {409}
    assert len({response.get_json()["error"] for response in responses}) == 1
    assert calls == []
    assert not paths.env_path.exists()
    assert not paths.state_db_path.exists()
    assert not list(paths.sender_session_path.parent.iterdir())


def test_ready_but_unconfigured_can_read_and_save_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ready 且未配置时配置 GET/POST 必须穿过门禁进入正常处理。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    current = Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+861****8000",
        target_groups=["https://t.me/example"],
    )
    saved: list[Settings] = []
    monkeypatch.setattr("web_app.load_settings", lambda **_kwargs: current)
    monkeypatch.setattr(
        "web_app.save_settings",
        lambda settings, **_kwargs: saved.append(settings),
    )
    app = create_app(runtime_paths=paths, runtime_state=_state("ready", False))
    client = app.test_client()

    get_response = client.get("/api/config")
    post_response = client.post(
        "/api/config",
        json={
            "api_id": 12345,
            "api_hash": "b" * 32,
            "phone": "+861****8000",
            "target_groups": ["https://t.me/example"],
        },
    )

    assert get_response.status_code == 200
    assert post_response.status_code == 200
    assert len(saved) == 1


def test_index_and_events_remain_public_when_migration_is_blocked(tmp_path: Path) -> None:
    """首页与 SSE 在迁移阻断态仍可无落盘访问。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    app = create_app(
        runtime_paths=paths,
        runtime_state=_state("custom_database_requires_manual_copy", False),
    )

    assert app.test_client().get("/").status_code == 200
    with app.test_request_context("/api/events"):
        response = app.view_functions["web.api_events"]()
        assert response.status_code == 200
        response.response.close()
    assert not paths.env_path.exists()
    assert not paths.state_db_path.exists()


def test_opening_web_during_pending_does_not_make_next_migration_ready(tmp_path: Path) -> None:
    """pending 时访问 Web 业务路由不得制造固定库或 env，后续迁移仍保持 pending。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    with sqlite3.connect(custom_database) as connection:
        connection.execute("CREATE TABLE source_data (value TEXT)")
    (legacy / ".env").write_text(
        f"API_ID=12345\nAPI_HASH={'a' * 32}\nSTATE_DB_PATH={custom_database}\n",
        encoding="utf-8",
    )
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    first = migrate_legacy_runtime_data(legacy, paths)
    assert first.status == "custom_database_requires_manual_copy"

    app = create_app(runtime_paths=paths, runtime_state=_state(first.status, False))
    client = app.test_client()
    assert client.get("/api/lotteries").status_code == 409
    assert client.post("/api/config", json={"api_id": 999}).status_code == 409

    second = migrate_legacy_runtime_data(legacy, paths)
    assert second.status == "custom_database_requires_manual_copy"
    assert not paths.state_db_path.exists()
    assert not paths.env_path.exists()


@pytest.mark.parametrize(
    ("migration_status", "configured"),
    [
        ("custom_database_requires_manual_copy", False),
        ("database_destination_conflict", False),
        ("migration_failed", False),
        ("ready", False),
    ],
)
def test_static_assets_are_public_in_blocked_and_unconfigured_states(
    tmp_path: Path,
    migration_status: str,
    configured: bool,
) -> None:
    """静态资源只读且公开，任何运行状态都不得被门禁拦截返回 409。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    app = create_app(
        runtime_paths=paths,
        runtime_state=_state(migration_status, configured),
    )

    response = app.test_client().get("/static/css/style.css")

    assert response.status_code == 200
    assert response.data
    assert not paths.env_path.exists()
    assert not paths.state_db_path.exists()
