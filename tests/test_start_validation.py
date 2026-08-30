"""/api/start 前置群组链接校验测试。"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(monkeypatch, app):
    # 阻止加载真实 .env
    monkeypatch.setattr("src.config.load_dotenv", lambda **k: None)
    app.config["TESTING"] = True
    return app.test_client()


def test_start_with_invalid_group_returns_422(client, monkeypatch) -> None:
    """无效群组链接（非 https://t.me/ 格式）→ 422 + detail。"""
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x" * 32)
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.setenv("TARGET_GROUPS", "not_a_valid_link")
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")
    monkeypatch.setenv("GROUP_GAP_MIN", "1")
    monkeypatch.setenv("GROUP_GAP_MAX", "1")
    resp = client.post("/api/start")
    assert resp.status_code == 422
    data = resp.get_json()
    assert "detail" in data
    assert "not_a_valid_link" in data["detail"]


def test_start_with_empty_groups_returns_422(client, monkeypatch) -> None:
    """无任何目标群组 → 422（load_settings 或 validate_group_links 抛 ValueError）。"""
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x" * 32)
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.delenv("TARGET_GROUPS", raising=False)
    monkeypatch.delenv("TARGET_GROUP", raising=False)
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")
    monkeypatch.setenv("GROUP_GAP_MIN", "1")
    monkeypatch.setenv("GROUP_GAP_MAX", "1")
    resp = client.post("/api/start")
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("migration_status", "configured"),
    [
        ("custom_database_requires_manual_copy", False),
        ("migration_failed", False),
        ("ready", False),
    ],
)
def test_start_blocks_before_constructing_runtime_dependencies(
    app, runtime_state, monkeypatch, migration_status, configured
) -> None:
    """迁移未就绪或未配置时不得构造消息、发送、AI 或后台线程依赖。"""
    runtime_state.update(
        configured=configured,
        migration_status=migration_status,
        setup_status="configuration_required",
    )
    calls: list[str] = []

    def forbidden(name: str):
        def construct(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"不得构造{name}")

        return construct

    monkeypatch.setattr("web_app.load_settings", forbidden("settings"))
    monkeypatch.setattr("web_app.MessageManager", forbidden("message_manager"))
    monkeypatch.setattr("web_app.TelegramSender", forbidden("sender"))
    monkeypatch.setattr("web_app.AIClient", forbidden("ai"))
    monkeypatch.setattr("web_app.AISender", forbidden("ai_sender"))

    response = app.test_client().post("/api/start")

    assert response.status_code == 409
    assert response.get_json() == {
        "success": False,
        "error": "应用尚未完成运行数据迁移或配置",
    }
    assert calls == []
