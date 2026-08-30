from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_paths import RuntimePaths


def test_runtime_paths_use_injected_localappdata_and_keep_resources_separate(
    tmp_path: Path,
) -> None:
    """运行数据固定在用户目录，资源目录不依赖当前工作目录。"""
    paths = RuntimePaths.from_environment(
        local_appdata=tmp_path / "local-app-data",
        resource_dir=tmp_path / "package-resources",
    )

    assert paths.data_dir == tmp_path / "local-app-data" / "BeWaterTG"
    assert paths.env_path == paths.data_dir / "config" / ".env"
    assert paths.state_db_path == paths.data_dir / "state" / "be_water.db"
    assert paths.sender_session_path == paths.data_dir / "session" / "sender_session.session"
    assert paths.logs_dir == paths.data_dir / "logs"
    assert paths.backups_dir == paths.data_dir / "backups"
    assert paths.messages_dir == paths.data_dir / "config" / "messages"
    assert paths.resource_dir == tmp_path / "package-resources"
    assert paths.env_path.parent.is_dir()
    assert paths.state_db_path.parent.is_dir()
    assert paths.sender_session_path.parent.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.backups_dir.is_dir()
    assert paths.messages_dir.is_dir()


def test_runtime_paths_fail_closed_without_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 LOCALAPPDATA 时不回退到当前目录。"""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        RuntimePaths.from_environment()
