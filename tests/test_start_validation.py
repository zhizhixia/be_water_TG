"""/api/start 前置群组链接校验测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from web_app import _build_message_manager
from web_manager import TransitionResult


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


def test_web_default_messages_ignore_cwd_and_prefer_user_root(
    runtime_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Web 默认消息仅来自可信根，用户文件覆盖内置文件而非 CWD 诱饵。"""
    runtime_paths.resource_dir.mkdir()
    (runtime_paths.resource_dir / "messages.txt").write_text("内置", encoding="utf-8")
    (runtime_paths.messages_dir / "messages.txt").write_text("用户", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "messages.txt").write_text("诱饵", encoding="utf-8")
    monkeypatch.chdir(cwd)
    settings = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
    )

    manager = _build_message_manager(settings, runtime_paths)

    assert manager.get_message("https://t.me/example") == "用户"


def test_web_explicit_builtin_and_group_fallback_are_controlled(
    runtime_paths,
) -> None:
    """显式 builtin 读取资源；共享默认缺失时才回退安全群组文件名。"""
    runtime_paths.resource_dir.mkdir()
    (runtime_paths.resource_dir / "explicit.txt").write_text("明确内置", encoding="utf-8")
    (runtime_paths.messages_dir / "messages_example.txt").write_text(
        "群组回退", encoding="utf-8"
    )
    explicit = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
        message_files={"https://t.me/example": "builtin:explicit.txt"},
    )
    fallback = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
    )

    assert _build_message_manager(explicit, runtime_paths).get_message(
        "https://t.me/example"
    ) == "明确内置"
    assert _build_message_manager(fallback, runtime_paths).get_message(
        "https://t.me/example"
    ) == "群组回退"


def test_start_api_uses_controlled_manager_without_connecting_external_services(
    app, manager, runtime_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实启动路由只装配受控消息，并以 fake 阻止 Telegram 与后台线程。"""
    runtime_paths.resource_dir.mkdir()
    (runtime_paths.resource_dir / "messages.txt").write_text("可信内置", encoding="utf-8")
    cwd = tmp_path / "api-cwd"
    cwd.mkdir()
    (cwd / "messages.txt").write_text("CWD 诱饵", encoding="utf-8")
    monkeypatch.chdir(cwd)
    settings = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr("web_app.load_settings", lambda **kwargs: settings)
    monkeypatch.setattr("web_app.TelegramSender", lambda *args, **kwargs: object())

    def fake_start(sender, actual_settings, message_manager, ai_sender=None):
        captured["message"] = message_manager.get_message("https://t.me/example")
        captured["settings"] = actual_settings
        return TransitionResult(True)

    monkeypatch.setattr(manager, "start", fake_start)

    response = app.test_client().post("/api/start")

    assert response.status_code == 200
    assert captured == {"message": "可信内置", "settings": settings}


def test_start_api_thread_failure_returns_structured_409_and_keeps_database(
    app, manager, runtime_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """线程启动失败不得冒泡 500，且保留已合法创建的固定状态库。"""
    runtime_paths.resource_dir.mkdir()
    (runtime_paths.resource_dir / "messages.txt").write_text("可信内置", encoding="utf-8")
    settings = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
        state_db_path=str(runtime_paths.state_db_path),
    )

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    monkeypatch.setattr("web_app.load_settings", lambda **_kwargs: settings)
    monkeypatch.setattr("web_app.TelegramSender", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("web_manager.threading.Thread", FailingThread)

    response = app.test_client().post("/api/start")

    assert response.status_code == 409
    assert response.get_json() == {"success": False, "error": "发送线程启动失败"}
    assert manager.state.value == "stopped"
    assert runtime_paths.state_db_path.exists()
