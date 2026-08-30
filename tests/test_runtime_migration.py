from __future__ import annotations

import inspect
import os
import sqlite3
from pathlib import Path

import pytest

from src.runtime_migration import migrate_legacy_runtime_data
from src.runtime_paths import RuntimePaths


def _create_wal_database(database: Path) -> sqlite3.Connection:
    """创建包含已提交 WAL 数据的源数据库，并保持连接以保留 WAL 文件。"""
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    connection.execute("INSERT INTO records VALUES ('committed-in-wal')")
    connection.commit()
    assert database.with_name(f"{database.name}-wal").exists()
    return connection


@pytest.mark.skipif(os.name != "nt", reason="SQLite 迁移仅在 Windows 提供文件身份绑定保证")
def test_migration_backs_up_sqlite_wal_to_standalone_database(tmp_path: Path) -> None:
    """持有禁止删除共享句柄时，SQLite WAL 备份仍可无覆盖提交并清理临时库。"""
    legacy = tmp_path / "legacy"
    database = legacy / "state" / "be_water.db"
    database.parent.mkdir(parents=True)
    source_connection = _create_wal_database(database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    try:
        result = migrate_legacy_runtime_data(legacy, paths)

        assert result.migrated == ("state_db",)
        assert result.skipped == ()
        assert paths.state_db_path.exists()
        assert not paths.state_db_path.with_name("be_water.db-wal").exists()
        assert not paths.state_db_path.with_name("be_water.db-shm").exists()
        assert not list(paths.state_db_path.parent.glob(".be_water.db.migration-*.tmp"))
        with sqlite3.connect(paths.state_db_path) as migrated:
            assert migrated.execute("SELECT value FROM records").fetchall() == [("committed-in-wal",)]
        assert database.exists()
        assert database.with_name("be_water.db-wal").exists()
    finally:
        source_connection.close()


def test_migration_rewrites_legacy_relative_state_path_without_logging_secrets(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """旧 .env 中的相对状态库路径迁移后指向新运行目录的绝对路径。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source = legacy / ".env"
    source.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=secret-api-hash",
                "PHONE=+155****1111",
                "TARGET_GROUP=https://t.me/group",
                "STATE_DB_PATH=state/be_water.db",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    result = migrate_legacy_runtime_data(legacy, paths)

    migrated_env = paths.env_path.read_text(encoding="utf-8")
    assert result.migrated == ("env",)
    assert f"STATE_DB_PATH={paths.state_db_path}\n" in migrated_env
    assert "STATE_DB_PATH=state/be_water.db" not in migrated_env
    assert source.read_text(encoding="utf-8").splitlines()[-1] == "STATE_DB_PATH=state/be_water.db"
    assert "secret-api-hash" not in caplog.text
    assert "+155****1111" not in caplog.text


def test_migration_skips_conflicts_and_invalid_database(tmp_path: Path) -> None:
    """迁移不覆盖已有目标，也不接收非 SQLite 文件。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".env").write_text("safe", encoding="utf-8")
    state = legacy / "state"
    state.mkdir()
    (state / "be_water.db").write_bytes(b"not-a-database")
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    paths.env_path.parent.mkdir(parents=True, exist_ok=True)
    paths.env_path.write_text("existing", encoding="utf-8")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.migrated == ()
    assert result.skipped == ("env", "state_db")
    assert paths.env_path.read_text(encoding="utf-8") == "existing"
    assert not paths.sender_session_path.exists()


def test_migration_skips_sqlite_when_target_appears_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """提交前并发出现的数据库目标绝不被覆盖。"""
    legacy = tmp_path / "legacy"
    database = legacy / "state" / "be_water.db"
    database.parent.mkdir(parents=True)
    _create_wal_database(database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    from src import runtime_migration

    original_commit = runtime_migration._commit_without_overwrite

    def create_concurrent_destination(temporary: Path, destination: Path) -> bool:
        destination.write_bytes(b"concurrent-database")
        return original_commit(temporary, destination)

    monkeypatch.setattr(runtime_migration, "_commit_without_overwrite", create_concurrent_destination)

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.migrated == ()
    assert result.skipped == ("state_db",)
    assert paths.state_db_path.read_bytes() == b"concurrent-database"
    assert database.exists()


def test_cleanup_of_replaced_unique_temporary_keeps_concurrent_file(tmp_path: Path) -> None:
    """临时路径被同名替换后，清理不会删除并发创建的文件。"""
    from src import runtime_migration

    temporary, identity = runtime_migration._create_private_temporary(tmp_path, "runtime")
    temporary.unlink()
    temporary.write_bytes(b"concurrent-file")

    runtime_migration._remove_owned_temporary(temporary, identity)

    assert temporary.read_bytes() == b"concurrent-file"


def test_cleanup_keeps_replacement_created_before_handle_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """句柄删除前路径被替换时，清理绝不删除并发方的新文件。"""
    from src import runtime_migration

    temporary, identity = runtime_migration._create_private_temporary(tmp_path, "runtime")
    original_delete = runtime_migration._delete_temporary_by_handle

    def replace_before_handle_deletion(descriptor: int) -> bool:
        temporary.unlink()
        temporary.write_bytes(b"concurrent-after-check")
        return original_delete(descriptor)

    monkeypatch.setattr(
        runtime_migration,
        "_delete_temporary_by_handle",
        replace_before_handle_deletion,
    )

    runtime_migration._remove_owned_temporary(temporary, identity)

    assert temporary.read_bytes() == b"concurrent-after-check"


def test_sqlite_migration_has_no_multi_file_rollback_deletion() -> None:
    """SQLite 迁移不再实现 sidecar 多文件提交或回滚删除路径。"""
    from src import runtime_migration

    source = inspect.getsource(runtime_migration)
    assert "_migrate_sqlite_group" not in source
    assert "samefile(destination)" not in source
    assert "state_wal" not in source
    assert "state_shm" not in source


def test_migration_rejects_database_when_safe_handle_open_fails(monkeypatch, tmp_path: Path) -> None:
    """数据库无法以安全句柄打开时，不读取或迁移该候选。"""
    legacy = tmp_path / "legacy"
    database = legacy / "state" / "be_water.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"SQLite format 3\x00" + b"x" * 32)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    monkeypatch.setattr(
        "src.runtime_migration._open_regular_source",
        lambda _path: None,
    )

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result == type(result)((), ("state_db",))


def test_migration_rejects_legacy_path_traversal(tmp_path: Path) -> None:
    """迁移入口不接受含路径穿越段的旧目录。"""
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    with pytest.raises(ValueError, match="traversal"):
        migrate_legacy_runtime_data(tmp_path / "legacy" / "..", paths)


def test_migration_uses_unique_temporary_and_keeps_existing_fixed_name(tmp_path: Path) -> None:
    """旧固定临时名不会阻塞 .env 迁移，也始终保持原样。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source = legacy / ".env"
    source.write_text("safe", encoding="utf-8")
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    paths.env_path.parent.mkdir(parents=True, exist_ok=True)
    old_temporary = paths.env_path.with_name(f".{paths.env_path.name}.tmp")
    old_temporary.write_text("keep", encoding="utf-8")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.migrated == ("env",)
    assert old_temporary.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 上验证 SQLite 临时文件身份绑定")
def test_windows_sqlite_migration_rejects_temporary_path_replaced_after_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """备份后临时路径被替换时，不提交替换库且绝不删除替换者文件。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    source = legacy / "state" / "be_water.db"
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as source_connection:
        source_connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        source_connection.execute("INSERT INTO records VALUES ('source')")
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(replacement) as replacement_connection:
        replacement_connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        replacement_connection.execute("INSERT INTO records VALUES ('replacement')")
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    original_connect = runtime_migration.sqlite3.connect

    class ReplacingConnection:
        def __init__(self, connection: sqlite3.Connection, temporary_directory: Path) -> None:
            self._connection = connection
            self._temporary_directory = temporary_directory

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def close(self) -> None:
            self._connection.close()
            temporary_files = list(self._temporary_directory.glob(".be_water.db.migration-*.tmp"))
            assert len(temporary_files) == 1
            temporary_files[0].unlink()
            temporary_files[0].write_bytes(replacement.read_bytes())

    def connect_with_replacement(database, *args, **kwargs):
        connection = original_connect(database, *args, **kwargs)
        if kwargs.get("uri"):
            return ReplacingConnection(connection, paths.state_db_path.parent)
        return connection

    monkeypatch.setattr(runtime_migration.sqlite3, "connect", connect_with_replacement)

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.migrated == ()
    assert result.skipped == ("state_db",)
    assert not paths.state_db_path.exists()
    temporary_files = list(paths.state_db_path.parent.glob(".be_water.db.migration-*.tmp"))
    assert len(temporary_files) == 1
    with sqlite3.connect(temporary_files[0]) as temporary_connection:
        assert temporary_connection.execute("SELECT value FROM records").fetchall() == [("replacement",)]


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 上验证句柄删除权限")
def test_windows_temporary_handle_can_delete_its_owned_path(tmp_path: Path) -> None:
    """Windows 临时文件句柄拥有 DELETE 权限，关闭后路径不再存在。"""
    from src import runtime_migration

    temporary, descriptor = runtime_migration._create_private_temporary(tmp_path, ".env")

    assert temporary.exists()
    assert runtime_migration._delete_temporary_by_handle(descriptor)
    os.close(descriptor)
    assert not temporary.exists()


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 上验证句柄删除权限")
def test_windows_env_migration_leaves_no_sensitive_temporary_copy(tmp_path: Path) -> None:
    """Windows 成功迁移 .env 后不遗留任何可能包含配置的临时副本。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".env").write_text("API_HASH=secret-value\n", encoding="utf-8")
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.migrated == ("env",)
    assert paths.env_path.read_text(encoding="utf-8") == "API_HASH=secret-value\n"
    assert not list(paths.env_path.parent.glob("..env.migration-*.tmp"))


def test_migration_reports_env_skipped_when_handle_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """句柄清理失败时，即使目标已提交，迁移结果也不能声称成功。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".env").write_text("API_HASH=secret-value\n", encoding="utf-8")
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    monkeypatch.setattr(runtime_migration, "_delete_temporary_by_handle", lambda _descriptor: False)

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.migrated == ()
    assert result.skipped == ("env",)
    assert paths.env_path.exists()
    assert list(paths.env_path.parent.glob("..env.migration-*.tmp"))
