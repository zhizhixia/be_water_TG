from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings, load_settings, save_settings
from src.runtime_migration import migrate_legacy_runtime_data
from src.runtime_paths import RuntimePaths


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setattr("src.config.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x" * 32)
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.setenv("TARGET_GROUP", "https://t.me/group")


def test_load_settings_uses_runtime_default_state_path(monkeypatch, tmp_path: Path) -> None:
    """未显式配置数据库时，从注入的运行路径获取默认位置。"""
    _set_required_env(monkeypatch)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    settings = load_settings(runtime_paths=paths)

    assert settings.state_db_path == str(paths.state_db_path)


@pytest.mark.parametrize("configured_path", ["state/outside.db", "C:/outside/custom.db"])
def test_load_settings_ignores_configured_state_database_path(
    monkeypatch,
    tmp_path: Path,
    configured_path: str,
) -> None:
    """相对或绝对数据库配置都不能改变注入的固定运行路径。"""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("STATE_DB_PATH", configured_path)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    settings = load_settings(runtime_paths=paths)

    assert settings.state_db_path == str(paths.state_db_path)


def test_load_settings_uses_migrated_absolute_state_path(monkeypatch, tmp_path: Path) -> None:
    """迁移旧 .env 后，配置加载使用新运行目录中的绝对状态库路径。"""
    for key in (
        "API_ID",
        "API_HASH",
        "PHONE",
        "TARGET_GROUP",
        "TARGET_GROUPS",
        "STATE_DB_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".env").write_text(
        "\n".join(
            [
                "API_ID=1",
                "API_HASH=" + "x" * 32,
                "PHONE=+1",
                "TARGET_GROUP=https://t.me/group",
                "STATE_DB_PATH=state/be_water.db",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    migrate_legacy_runtime_data(legacy, paths)
    settings = load_settings(runtime_paths=paths)

    assert settings.state_db_path == str(paths.state_db_path)
    assert Path(settings.state_db_path).is_absolute()


def test_save_settings_uses_runtime_env_path_but_keeps_explicit_path(tmp_path: Path) -> None:
    """默认保存到用户数据目录，显式路径仍保持兼容。"""
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    settings = Settings(api_id=1, api_hash="x" * 32, phone="+1", target_groups=["https://t.me/group"])
    explicit = tmp_path / "legacy.env"

    save_settings(settings, runtime_paths=paths)
    save_settings(settings, str(explicit), runtime_paths=paths)

    assert paths.env_path.exists()
    assert explicit.exists()


def test_save_settings_normalizes_state_database_to_runtime_path(tmp_path: Path) -> None:
    """保存配置时忽略 Settings 中携带的数据库路径并写入固定目标。"""
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    settings = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/group"],
        state_db_path="C:/outside/custom.db",
    )

    save_settings(settings, runtime_paths=paths)

    saved = paths.env_path.read_text(encoding="utf-8")
    assert f"STATE_DB_PATH={paths.state_db_path}\n" in saved
    assert "C:/outside/custom.db" not in saved
    assert settings.state_db_path == "C:/outside/custom.db"
