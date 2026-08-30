from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import sqlite3
import stat
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from src.runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    """记录迁移的类别与固定状态码，不包含运行数据内容或凭据。"""

    migrated: tuple[str, ...]
    skipped: tuple[str, ...]
    status: str = "ready"


_MIGRATION_MARKER_NAME = "migration-state.json"
_PENDING_STATUS = "custom_database_requires_manual_copy"
_CONFLICT_STATUS = "database_destination_conflict"
_FAILED_STATUS = "migration_failed"
_DEFAULT_LEGACY_STATE_PATH = "state/be_water.db"
_MAX_ENV_BYTES = 1024 * 1024


def _create_private_temporary(directory: Path, name: str) -> tuple[Path, int]:
    """在目标目录创建唯一私有临时文件，并持续持有其文件描述符。"""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        invalid_handle = ctypes.c_void_p(-1).value
        for _ in range(100):
            temporary = directory / f".{name}.migration-{next(tempfile._get_candidate_names())}.tmp"
            handle = create_file(
                str(temporary),
                0xC0010000,  # GENERIC_READ | GENERIC_WRITE | DELETE
                0x00000007,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
                None,
                1,  # CREATE_NEW
                0x00000080,  # FILE_ATTRIBUTE_NORMAL
                None,
            )
            if handle != invalid_handle:
                return temporary, msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
            if ctypes.get_last_error() != 80:  # ERROR_FILE_EXISTS
                raise ctypes.WinError(ctypes.get_last_error())
        raise FileExistsError("无法创建唯一运行迁移临时文件")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{name}.migration-", suffix=".tmp", dir=directory
    )
    return Path(temporary_name), descriptor


def _delete_temporary_by_handle(descriptor: int) -> bool:
    """通过已持有的 Windows 文件句柄标记临时文件链接删除。"""
    if os.name != "nt":
        return False
    try:
        import msvcrt
        from ctypes import wintypes

        class FileDispositionInfo(ctypes.Structure):
            _fields_ = [("delete_file", wintypes.BOOL)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_file_information = kernel32.SetFileInformationByHandle
        set_file_information.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        set_file_information.restype = wintypes.BOOL
        disposition = FileDispositionInfo(True)
        if not set_file_information(
            wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)),
            4,  # FileDispositionInfo
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            error = ctypes.get_last_error()
            logger.warning("运行迁移临时文件句柄删除失败，错误码：%d", error)
            return False
        return True
    except (OSError, ValueError) as error:
        logger.warning("运行迁移临时文件句柄删除不可用：%s", type(error).__name__)
        return False


def _windows_file_identity(descriptor: int) -> tuple[int, int]:
    """返回 Windows 已打开文件的稳定卷序列号与文件索引。"""
    if os.name != "nt":
        raise OSError("Windows 文件身份仅适用于 Windows")
    import msvcrt
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time_low", wintypes.DWORD),
            ("creation_time_high", wintypes.DWORD),
            ("last_access_time_low", wintypes.DWORD),
            ("last_access_time_high", wintypes.DWORD),
            ("last_write_time_low", wintypes.DWORD),
            ("last_write_time_high", wintypes.DWORD),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    get_file_information.restype = wintypes.BOOL
    information = ByHandleFileInformation()
    if not get_file_information(
        wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)), ctypes.byref(information)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return (
        information.volume_serial_number,
        (information.file_index_high << 32) | information.file_index_low,
    )


def _reopen_temporary_for_cleanup(temporary: Path) -> int:
    """重开临时文件并禁止删除共享，以绑定身份复核和提交期间的路径。"""
    if os.name != "nt":
        return os.open(temporary, os.O_RDWR | getattr(os, "O_BINARY", 0))
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(temporary),
        0xC0010000,  # GENERIC_READ | GENERIC_WRITE | DELETE
        0x00000003,  # FILE_SHARE_READ | FILE_SHARE_WRITE：禁止删除、改名或替换路径。
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))


def _remove_owned_temporary(temporary: Path, descriptor: int) -> bool:
    """仅通过原始句柄清理临时文件，绝不按可替换路径删除。"""
    try:
        deleted = _delete_temporary_by_handle(descriptor)
        if not deleted:
            logger.warning("运行迁移保留未清理的唯一临时文件：%s", temporary.name)
        return deleted
    finally:
        os.close(descriptor)


def _commit_without_overwrite(temporary: Path, destination: Path) -> bool:
    """将同目录临时文件原子提交到尚不存在的目标，绝不替换已有目标。"""
    try:
        os.link(temporary, destination)
    except FileExistsError:
        return False
    return True


def _open_regular_source(source: Path) -> int | None:
    """以不可跟随重解析点的方式打开普通源文件，并在句柄上完成校验。"""
    if os.name == "nt":
        try:
            import msvcrt
            from ctypes import wintypes

            class ByHandleFileInformation(ctypes.Structure):
                _fields_ = [
                    ("file_attributes", wintypes.DWORD),
                    ("creation_time_low", wintypes.DWORD),
                    ("creation_time_high", wintypes.DWORD),
                    ("last_access_time_low", wintypes.DWORD),
                    ("last_access_time_high", wintypes.DWORD),
                    ("last_write_time_low", wintypes.DWORD),
                    ("last_write_time_high", wintypes.DWORD),
                    ("volume_serial_number", wintypes.DWORD),
                    ("file_size_high", wintypes.DWORD),
                    ("file_size_low", wintypes.DWORD),
                    ("number_of_links", wintypes.DWORD),
                    ("file_index_high", wintypes.DWORD),
                    ("file_index_low", wintypes.DWORD),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = (
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            )
            create_file.restype = wintypes.HANDLE
            get_file_information = kernel32.GetFileInformationByHandle
            get_file_information.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
            get_file_information.restype = wintypes.BOOL
            get_file_type = kernel32.GetFileType
            get_file_type.argtypes = (wintypes.HANDLE,)
            get_file_type.restype = wintypes.DWORD
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            invalid_handle = ctypes.c_void_p(-1).value
            handle = create_file(
                str(source),
                0x80000000,  # GENERIC_READ
                0x00000003,  # FILE_SHARE_READ | FILE_SHARE_WRITE：禁止替换或删除已打开的源文件。
                None,
                3,  # OPEN_EXISTING
                0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
                None,
            )
            if handle == invalid_handle:
                return None
            information = ByHandleFileInformation()
            if not get_file_information(handle, ctypes.byref(information)):
                close_handle(handle)
                return None
            if get_file_type(handle) != 1 or information.file_attributes & 0x00000400:
                close_handle(handle)
                return None
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            except OSError:
                close_handle(handle)
                return None
        except (OSError, ValueError):
            return None

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def _copy_open_file(input_file, output_file) -> bool:
    """复制已打开的源文件并在复制过程中校验内容摘要。"""
    source_digest = hashlib.sha256()
    temporary_digest = hashlib.sha256()
    while chunk := input_file.read(1024 * 1024):
        source_digest.update(chunk)
        output_file.write(chunk)
        temporary_digest.update(chunk)
    return source_digest.digest() == temporary_digest.digest()


def _copy_atomically(source: Path, destination: Path) -> bool:
    """复制已安全打开的普通运行文件，并以唯一临时文件实现无覆盖提交。"""
    source_descriptor = _open_regular_source(source)
    if source_descriptor is None:
        return False
    try:
        temporary, descriptor = _create_private_temporary(destination.parent, destination.name)
    except OSError:
        os.close(source_descriptor)
        return False
    committed = False
    try:
        with os.fdopen(source_descriptor, "rb") as input_file:
            with os.fdopen(descriptor, "wb", closefd=False) as output_file:
                if not _copy_open_file(input_file, output_file):
                    return False
                output_file.flush()
                os.fsync(output_file.fileno())
        committed = _commit_without_overwrite(temporary, destination)
    except OSError:
        return False
    finally:
        cleanup_succeeded = _remove_owned_temporary(temporary, descriptor)
    return committed and cleanup_succeeded


def _normalized_env_text(
    source: Path,
    paths: RuntimePaths,
    rewrite_custom_path: bool,
    expected_source_digest: str | None = None,
) -> str | None:
    """使用一次安全读取的数据校验来源并生成数据库路径规范化文本。"""
    source_snapshot = _read_legacy_env(source)
    if source_snapshot is None:
        return None
    source_text, source_digest = source_snapshot
    if expected_source_digest is not None and source_digest != expected_source_digest:
        return None

    output_lines: list[str] = []
    replaced = False
    for line in source_text.splitlines(keepends=True):
        line_ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if line_ending else line
        stripped = content.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            normalized_value = value.strip().strip("\"'")
            if key.strip() == "STATE_DB_PATH" and (
                rewrite_custom_path or normalized_value == _DEFAULT_LEGACY_STATE_PATH
            ):
                output_lines.append(f"STATE_DB_PATH={paths.state_db_path}{line_ending}")
                replaced = True
                continue
        output_lines.append(line)
    if replaced and (not output_lines or not output_lines[-1].endswith("\n")):
        output_lines.append("\n")
    return "".join(output_lines)


def _rewrite_legacy_env_state_path(
    source: Path,
    destination: Path,
    paths: RuntimePaths,
    rewrite_custom_path: bool = False,
    expected_source_digest: str | None = None,
) -> bool:
    """迁移 .env，并将旧数据库路径规范化为固定绝对路径。"""
    output_text = _normalized_env_text(
        source,
        paths,
        rewrite_custom_path,
        expected_source_digest,
    )
    if output_text is None:
        return False
    try:
        temporary, descriptor = _create_private_temporary(destination.parent, destination.name)
    except OSError:
        return False

    committed = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="", closefd=False) as output_file:
            output_file.write(output_text)
            output_file.flush()
            os.fsync(output_file.fileno())
        committed = _commit_without_overwrite(temporary, destination)
    except OSError:
        return False
    finally:
        cleanup_succeeded = _remove_owned_temporary(temporary, descriptor)
    return committed and cleanup_succeeded


def _env_matches_normalized_source(
    source: Path,
    destination: Path,
    paths: RuntimePaths,
    expected_source_digest: str | None = None,
) -> bool:
    """确认已有目标配置精确等于当前源配置的固定路径规范化结果。"""
    expected = _normalized_env_text(
        source,
        paths,
        rewrite_custom_path=True,
        expected_source_digest=expected_source_digest,
    )
    if expected is None:
        return False
    destination_descriptor = _open_regular_source(destination)
    if destination_descriptor is None:
        return False
    try:
        with os.fdopen(destination_descriptor, "r", encoding="utf-8", newline="") as input_file:
            actual = input_file.read(_MAX_ENV_BYTES + 1)
        return len(actual.encode("utf-8")) <= _MAX_ENV_BYTES and actual == expected
    except (OSError, UnicodeError):
        return False


def _backup_sqlite_atomically(source: Path, destination: Path) -> bool:
    """在 Windows 以身份绑定临时库完成 SQLite 备份，拒绝可替换路径提交。"""
    if os.name != "nt":
        logger.warning("非 Windows 平台跳过 SQLite 运行库迁移：缺少文件身份绑定保证")
        return False
    source_descriptor = _open_regular_source(source)
    if source_descriptor is None or destination.exists():
        if source_descriptor is not None:
            os.close(source_descriptor)
        return False
    try:
        if os.read(source_descriptor, 16) != b"SQLite format 3\x00":
            os.close(source_descriptor)
            return False
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        temporary, descriptor = _create_private_temporary(destination.parent, destination.name)
        try:
            initial_identity = _windows_file_identity(descriptor)
        except OSError:
            os.close(descriptor)
            raise
    except OSError:
        os.close(source_descriptor)
        return False

    descriptor_for_cleanup: int | None = None
    identity_verified = False
    committed = False
    cleanup_succeeded = False
    try:
        # SQLite 只能按路径打开目标临时库，因此先释放创建句柄，再在备份后重新绑定身份。
        os.close(descriptor)
        source_uri = f"{source.absolute().as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            with closing(sqlite3.connect(temporary)) as temporary_connection:
                source_connection.backup(temporary_connection)
                temporary_connection.commit()
        descriptor_for_cleanup = _reopen_temporary_for_cleanup(temporary)
        if _windows_file_identity(descriptor_for_cleanup) != initial_identity:
            logger.warning("SQLite 迁移临时文件身份不匹配，拒绝提交")
            return False
        identity_verified = True
        with os.fdopen(descriptor_for_cleanup, "r+b", closefd=False) as temporary_file:
            os.fsync(temporary_file.fileno())
        committed = _commit_without_overwrite(temporary, destination)
    except (OSError, sqlite3.Error):
        return False
    finally:
        os.close(source_descriptor)
        if descriptor_for_cleanup is None:
            logger.warning("SQLite 迁移未重新取得受保护临时文件句柄")
        elif identity_verified:
            cleanup_succeeded = _remove_owned_temporary(temporary, descriptor_for_cleanup)
        else:
            os.close(descriptor_for_cleanup)
    return committed and cleanup_succeeded


def _read_legacy_env(source: Path) -> tuple[str, str] | None:
    """一次性安全读取受限配置，并返回文本与绑定身份和内容的摘要。"""
    descriptor = _open_regular_source(source)
    if descriptor is None:
        return None
    try:
        source_stat = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb") as input_file:
            content = input_file.read(_MAX_ENV_BYTES + 1)
        if len(content) > _MAX_ENV_BYTES:
            return None
        text = content.decode("utf-8")
    except (OSError, UnicodeError):
        return None

    identity = f"{source_stat.st_dev}:{source_stat.st_ino}".encode("ascii")
    digest = hashlib.sha256(b"runtime-env-v1\0" + identity + b"\0" + content).hexdigest()
    return text, digest


def _inspect_legacy_env(source: Path) -> tuple[bool, str] | None:
    """安全读取旧配置并仅返回自定义路径标志与内容绑定摘要。"""
    source_snapshot = _read_legacy_env(source)
    if source_snapshot is None:
        return None
    text, source_digest = source_snapshot

    state_values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "STATE_DB_PATH":
            state_values.append(value.strip().strip("\"'"))
    is_custom = any(value != _DEFAULT_LEGACY_STATE_PATH for value in state_values)
    return is_custom, source_digest


def _write_initial_marker(paths: RuntimePaths, status: str, source_digest: str) -> bool:
    """以无覆盖方式持久化不含路径或配置内容的首次迁移状态。"""
    marker_path = paths.backups_dir / _MIGRATION_MARKER_NAME
    payload = {
        "schema": 1,
        "status": status,
        "source_config_digest": source_digest,
    }
    try:
        temporary, descriptor = _create_private_temporary(marker_path.parent, marker_path.name)
    except OSError:
        return False
    committed = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n", closefd=False) as output_file:
            json.dump(payload, output_file, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        committed = _commit_without_overwrite(temporary, marker_path)
    except (OSError, TypeError, ValueError):
        return False
    finally:
        cleanup_succeeded = _remove_owned_temporary(temporary, descriptor)
    return committed and cleanup_succeeded


def _read_marker(paths: RuntimePaths, source_digest: str) -> str | None:
    """校验迁移标记的完整固定结构和源配置身份摘要。"""
    marker_path = paths.backups_dir / _MIGRATION_MARKER_NAME
    descriptor = _open_regular_source(marker_path)
    if descriptor is None:
        return None
    try:
        with os.fdopen(descriptor, "rb") as input_file:
            content = input_file.read(4097)
        if len(content) > 4096:
            return None
        payload = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "status",
        "source_config_digest",
    }:
        return None
    status = payload.get("status")
    digest = payload.get("source_config_digest")
    if payload.get("schema") != 1 or status not in {_PENDING_STATUS, _CONFLICT_STATUS}:
        return None
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    try:
        int(digest, 16)
    except ValueError:
        return None
    if digest != source_digest:
        return None
    return status


def _validate_fixed_sqlite(database: Path) -> bool:
    """只读验证固定目标是普通非重解析 SQLite 主库且完整性正常。"""
    descriptor = _open_regular_source(database)
    if descriptor is None:
        return False
    try:
        initial_stat = os.fstat(descriptor)
        if os.read(descriptor, 16) != b"SQLite format 3\x00":
            return False
        database_uri = f"{database.absolute().as_uri()}?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            integrity = list(connection.execute("PRAGMA quick_check"))
        final_stat = database.stat(follow_symlinks=False)
        return (
            stat.S_ISREG(final_stat.st_mode)
            and (initial_stat.st_dev, initial_stat.st_ino) == (final_stat.st_dev, final_stat.st_ino)
            and integrity == [("ok",)]
        )
    except (OSError, sqlite3.Error):
        return False
    finally:
        os.close(descriptor)


def _block_custom_database(
    paths: RuntimePaths,
    source: Path,
    source_digest: str,
) -> MigrationResult:
    """首次发现自定义旧库时记录阻断，并仅从固定目标完成离线恢复。"""
    marker_path = paths.backups_dir / _MIGRATION_MARKER_NAME
    if marker_path.exists():
        status = _read_marker(paths, source_digest)
        if status is None:
            return MigrationResult((), (), _FAILED_STATUS)
        if status == _CONFLICT_STATUS:
            return MigrationResult((), (), status)
        if not paths.state_db_path.exists():
            return MigrationResult((), (), status)
        if not _validate_fixed_sqlite(paths.state_db_path):
            return MigrationResult((), (), "manual_database_invalid")
        if paths.env_path.exists():
            if _env_matches_normalized_source(
                source,
                paths.env_path,
                paths,
                expected_source_digest=source_digest,
            ):
                return MigrationResult((), (), "ready")
            return MigrationResult((), (), _FAILED_STATUS)
        migrated = _rewrite_legacy_env_state_path(
            source,
            paths.env_path,
            paths,
            rewrite_custom_path=True,
            expected_source_digest=source_digest,
        )
        if not migrated:
            return MigrationResult((), (), _FAILED_STATUS)
        return MigrationResult(("env",), (), "ready")
    status = _CONFLICT_STATUS if paths.state_db_path.exists() else _PENDING_STATUS
    if not _write_initial_marker(paths, status, source_digest):
        return MigrationResult((), (), _FAILED_STATUS)
    logger.info("旧运行数据迁移阻断：%s", status)
    return MigrationResult((), (), status)


def migrate_legacy_runtime_data(legacy_dir: Path | str, paths: RuntimePaths) -> MigrationResult:
    """保守且幂等地迁移已识别的旧运行数据，绝不删除或覆盖源数据。"""
    root = Path(legacy_dir)
    if ".." in root.parts:
        raise ValueError("legacy runtime directory traversal is not allowed")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("legacy runtime directory must be a real directory")

    candidates: list[tuple[str, Path, Path]] = []
    env_file = root / ".env"
    if env_file.exists():
        env_inspection = _inspect_legacy_env(env_file)
        if env_inspection is None:
            return MigrationResult((), (), _FAILED_STATUS)
        is_custom_database, source_digest = env_inspection
        if is_custom_database:
            return _block_custom_database(paths, env_file, source_digest)
        candidates.append(("env", env_file, paths.env_path))
    state_dir = root / "state"
    database = state_dir / "be_water.db"
    if not state_dir.is_symlink() and database.exists():
        candidates.append(("state_db", database, paths.state_db_path))
    session = root / "sender_session.session"
    if session.exists():
        candidates.append(("sender_session", session, paths.sender_session_path))

    migrated: list[str] = []
    skipped: list[str] = []
    for name, source, destination in candidates:
        if destination.exists():
            skipped.append(name)
            continue
        if name == "env":
            committed = _rewrite_legacy_env_state_path(
                source,
                destination,
                paths,
                expected_source_digest=source_digest,
            )
        elif name == "state_db":
            committed = _backup_sqlite_atomically(source, destination)
        else:
            committed = _copy_atomically(source, destination)
        if committed:
            migrated.append(name)
        else:
            skipped.append(name)
    logger.info("旧运行数据迁移完成：%d 项已迁移，%d 项跳过", len(migrated), len(skipped))
    return MigrationResult(tuple(migrated), tuple(skipped))
