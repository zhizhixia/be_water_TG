from __future__ import annotations

import inspect
import json
import os
import sqlite3
import subprocess
import sys
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


def _write_custom_legacy_env(legacy: Path, custom_database: Path) -> Path:
    """写入只用于测试的非默认旧数据库配置。"""
    legacy.mkdir()
    source = legacy / ".env"
    source.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=marker-secret",
                f"STATE_DB_PATH={custom_database}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return source


def test_custom_database_path_creates_non_sensitive_pending_without_opening_source(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """首次自定义数据库阻断不读取旧库、不迁移配置，也不创建固定目标。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"must-not-be-opened")
    source = _write_custom_legacy_env(legacy, custom_database)
    original_open = runtime_migration._open_regular_source
    opened: list[Path] = []

    def record_safe_open(candidate: Path) -> int | None:
        opened.append(candidate)
        return original_open(candidate)

    monkeypatch.setattr(runtime_migration, "_open_regular_source", record_safe_open)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "custom_database_requires_manual_copy"
    assert result.migrated == ()
    assert custom_database not in opened
    assert source.exists()
    assert not paths.env_path.exists()
    assert not paths.state_db_path.exists()
    marker_text = (paths.backups_dir / "migration-state.json").read_text(encoding="utf-8")
    marker = json.loads(marker_text)
    assert marker == {
        "schema": 1,
        "status": "custom_database_requires_manual_copy",
        "source_config_digest": marker["source_config_digest"],
    }
    assert len(marker["source_config_digest"]) == 64
    assert str(custom_database) not in marker_text
    assert "marker-secret" not in marker_text
    assert str(custom_database) not in caplog.text
    assert "marker-secret" not in caplog.text


@pytest.mark.skipif(os.name != "nt", reason="独占目标预约仅由 Windows 提供保证")
def test_pending_marker_commit_reserves_target_against_concurrent_sqlite_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """提交 pending marker 前并发 SQLite 创建必须被预约阻断，且返回后目标不存在。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    original_write_marker = runtime_migration._write_initial_marker
    creation_blocked = False

    def create_database_before_marker(*args, **kwargs) -> bool:
        nonlocal creation_blocked
        try:
            with sqlite3.connect(paths.state_db_path) as connection:
                connection.execute("CREATE TABLE raced (value TEXT)")
        except (OSError, sqlite3.Error):
            creation_blocked = True
        return original_write_marker(*args, **kwargs)

    monkeypatch.setattr(runtime_migration, "_write_initial_marker", create_database_before_marker)

    first = migrate_legacy_runtime_data(legacy, paths)
    second = migrate_legacy_runtime_data(legacy, paths)

    assert first.status == "custom_database_requires_manual_copy"
    assert second.status == "custom_database_requires_manual_copy"
    assert creation_blocked
    assert not paths.state_db_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="delete-on-close 清理仅由 Windows 提供保证")
def test_pending_marker_write_failure_closes_and_deletes_target_reservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """pending marker 写入失败时必须关闭预约并自动删除空目标。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    reservation_seen = False

    def fail_marker_write(*_args, **_kwargs) -> bool:
        nonlocal reservation_seen
        reservation_seen = paths.state_db_path.exists()
        return False

    monkeypatch.setattr(runtime_migration, "_write_initial_marker", fail_marker_write)

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "migration_failed"
    assert reservation_seen
    assert not paths.state_db_path.exists()
    assert not (paths.backups_dir / "migration-state.json").exists()


def test_custom_database_path_with_existing_fixed_target_records_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """首次发现时固定目标已存在必须永久进入目标冲突，而非恢复成功。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    source = _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE existing (value TEXT)")

    first = migrate_legacy_runtime_data(legacy, paths)
    marker_path = paths.backups_dir / "migration-state.json"
    marker_before = marker_path.read_bytes()
    original_open = runtime_migration._open_regular_source
    opened: list[Path] = []

    def record_safe_open(candidate: Path) -> int | None:
        opened.append(candidate)
        return original_open(candidate)

    monkeypatch.setattr(runtime_migration, "_open_regular_source", record_safe_open)
    second = migrate_legacy_runtime_data(legacy, paths)

    assert first.status == "database_destination_conflict"
    assert second.status == "database_destination_conflict"
    assert custom_database not in opened
    assert not paths.env_path.exists()
    assert source.exists()
    assert custom_database.read_bytes() == b"source-kept"
    assert marker_path.read_bytes() == marker_before
    marker = json.loads(marker_before.decode("utf-8"))
    assert marker["status"] == "database_destination_conflict"


def test_pending_custom_database_recovers_only_from_valid_fixed_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """pending 后仅验证离线放入的固定 SQLite，并规范化迁移配置。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    with sqlite3.connect(custom_database) as connection:
        connection.execute("CREATE TABLE source_data (value TEXT)")
        connection.execute("INSERT INTO source_data VALUES ('source-kept')")
    source = _write_custom_legacy_env(legacy, custom_database)
    source_before = source.read_bytes()
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE restored (value TEXT NOT NULL)")
        connection.execute("INSERT INTO restored VALUES ('offline-copy')")

    original_open = runtime_migration._open_regular_source
    opened: list[Path] = []

    def record_safe_open(candidate: Path) -> int | None:
        opened.append(candidate)
        return original_open(candidate)

    monkeypatch.setattr(runtime_migration, "_open_regular_source", record_safe_open)
    result = migrate_legacy_runtime_data(legacy, paths)
    marker_path = paths.backups_dir / "migration-state.json"
    marker_after_recovery = marker_path.read_bytes()
    repeated = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "ready"
    assert result.migrated == ("env",)
    assert repeated.status == "ready"
    assert repeated.migrated == ()
    assert marker_path.read_bytes() == marker_after_recovery
    assert custom_database not in opened
    assert source.read_bytes() == source_before
    migrated_env = paths.env_path.read_text(encoding="utf-8")
    assert f"STATE_DB_PATH={paths.state_db_path}\n" in migrated_env
    assert str(custom_database) not in migrated_env
    with sqlite3.connect(paths.state_db_path) as connection:
        assert connection.execute("SELECT value FROM restored").fetchall() == [("offline-copy",)]


def test_pending_custom_database_rejects_in_place_env_content_change(tmp_path: Path) -> None:
    """pending 后源配置即使保持同一文件身份，内容变化也必须安全失败。"""
    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    source = _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    marker_path = paths.backups_dir / "migration-state.json"
    marker_before = marker_path.read_bytes()
    source_identity = source.stat().st_dev, source.stat().st_ino
    changed_content = source.read_text(encoding="utf-8").replace(
        "API_HASH=marker-secret", "API_HASH=changed-secret"
    )
    with source.open("wb") as output_file:
        output_file.write(changed_content.encode("utf-8"))
    assert (source.stat().st_dev, source.stat().st_ino) == source_identity
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE restored (value TEXT)")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "migration_failed"
    assert result.migrated == ()
    assert not paths.env_path.exists()
    assert marker_path.read_bytes() == marker_before
    assert source.read_text(encoding="utf-8") == changed_content


def test_pending_recovery_rejects_env_change_between_inspection_and_normalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """检查后到规范化读取前源内容变化时，不得迁移变化后的配置。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    source = _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    marker_path = paths.backups_dir / "migration-state.json"
    marker_before = marker_path.read_bytes()
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE restored (value TEXT)")
    original_rewrite = runtime_migration._rewrite_legacy_env_state_path

    def change_source_before_normalization(*args, **kwargs):
        with source.open("wb") as output_file:
            output_file.write(b"API_HASH=changed-after-inspection\nSTATE_DB_PATH=other.db\n")
        return original_rewrite(*args, **kwargs)

    monkeypatch.setattr(
        runtime_migration,
        "_rewrite_legacy_env_state_path",
        change_source_before_normalization,
    )

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "migration_failed"
    assert result.migrated == ()
    assert not paths.env_path.exists()
    assert marker_path.read_bytes() == marker_before
    assert source.read_bytes() == b"API_HASH=changed-after-inspection\nSTATE_DB_PATH=other.db\n"


def test_pending_custom_database_rejects_invalid_fixed_database(tmp_path: Path) -> None:
    """pending 后固定目标即使有 SQLite header 但主体损坏也继续阻断。"""
    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    source = _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    paths.state_db_path.write_bytes(b"SQLite format 3\x00" + b"broken-body")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "manual_database_invalid"
    assert result.migrated == ()
    assert not paths.env_path.exists()
    assert source.exists()
    assert custom_database.read_bytes() == b"source-kept"


def test_pending_recovery_requires_exact_single_quick_check_ok(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """只读 quick_check 返回额外行时必须 fail closed，不能迁移配置。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE restored (value TEXT)")

    original_connect = runtime_migration.sqlite3.connect
    executed: list[str] = []

    class ExtraQuickCheckRowConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str):
            executed.append(statement)
            if statement == "PRAGMA quick_check":
                return [("ok",), ("unexpected",)]
            return self._connection.execute(statement)

        def close(self) -> None:
            self._connection.close()

    def connect_with_extra_row(*args, **kwargs):
        return ExtraQuickCheckRowConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr(runtime_migration.sqlite3, "connect", connect_with_extra_row)

    result = migrate_legacy_runtime_data(legacy, paths)

    assert "PRAGMA quick_check" in executed
    assert result.status == "manual_database_invalid"
    assert not paths.env_path.exists()


def test_pending_custom_database_rejects_reparse_fixed_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """安全打开器将固定目标识别为重解析点时继续阻断。"""
    from src import runtime_migration

    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE restored (value TEXT)")
    original_open = runtime_migration._open_regular_source
    attempted: list[Path] = []

    def reject_reparse_target(candidate: Path) -> int | None:
        if candidate == paths.state_db_path:
            attempted.append(candidate)
            return None
        return original_open(candidate)

    monkeypatch.setattr(runtime_migration, "_open_regular_source", reject_reparse_target)

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "manual_database_invalid"
    assert not paths.env_path.exists()
    assert attempted == [paths.state_db_path]


def test_corrupted_pending_marker_fails_closed_without_migrating_env(tmp_path: Path) -> None:
    """pending marker 损坏时，即使固定库有效也按固定失败码安全阻断。"""
    legacy = tmp_path / "legacy"
    custom_database = tmp_path / "private" / "custom.db"
    custom_database.parent.mkdir()
    custom_database.write_bytes(b"source-kept")
    source = _write_custom_legacy_env(legacy, custom_database)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    assert migrate_legacy_runtime_data(legacy, paths).status == "custom_database_requires_manual_copy"
    marker_path = paths.backups_dir / "migration-state.json"
    marker_path.write_text("{broken", encoding="utf-8")
    with sqlite3.connect(paths.state_db_path) as connection:
        connection.execute("CREATE TABLE restored (value TEXT)")

    result = migrate_legacy_runtime_data(legacy, paths)

    assert result.status == "migration_failed"
    assert result.migrated == ()
    assert not paths.env_path.exists()
    assert source.exists()
    assert custom_database.read_bytes() == b"source-kept"


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


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 上验证目标预约独占语义")
def test_windows_pending_target_reservation_blocks_writes_deletes_and_replace(
    tmp_path: Path,
) -> None:
    """预约句柄存续期间真实 SQLite、写入、删除和替换均不得成功。"""
    from src import runtime_migration

    target = tmp_path / "state" / "be_water.db"
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(b"replacement")
    status, handle = runtime_migration._reserve_pending_database_target(target)

    assert status == "acquired"
    assert handle is not None
    assert target.exists()
    try:
        with pytest.raises(sqlite3.Error):
            sqlite3.connect(target, timeout=0)
        with pytest.raises(OSError):
            target.write_bytes(b"concurrent")
        with pytest.raises(OSError):
            target.unlink()
        with pytest.raises(OSError):
            os.replace(replacement, target)
        assert replacement.read_bytes() == b"replacement"
    finally:
        assert runtime_migration._close_pending_database_reservation(target, handle)
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 上验证进程退出自动清理")
def test_windows_pending_target_reservation_is_deleted_after_abnormal_process_exit(
    tmp_path: Path,
) -> None:
    """持有预约的进程异常退出后，Windows 必须自动关闭句柄并删除预约路径。"""
    target = tmp_path / "state" / "be_water.db"
    child_code = "\n".join(
        [
            "import os",
            "import sys",
            "from pathlib import Path",
            "from src.runtime_migration import _reserve_pending_database_target",
            "status, handle = _reserve_pending_database_target(Path(sys.argv[1]))",
            "print(status, flush=True)",
            "sys.stdin.read(1)",
            "os._exit(7)",
        ]
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(target)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "acquired"
        assert target.exists()
        assert child.stdin is not None
        child.stdin.write("x")
        child.stdin.flush()
        assert child.wait(timeout=10) == 7
        assert not target.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


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
