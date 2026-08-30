from __future__ import annotations

import ctypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.runtime_paths import RuntimePaths

MAX_MESSAGE_BYTES = 1024 * 1024


class MessagePathError(ValueError):
    """表示受控消息引用或读取失败，错误文本不泄露可信根。"""


@dataclass(frozen=True, repr=False)
class LoadedMessageText:
    """保存消息引用的安全摘要与已在受控句柄中读取的文本。"""

    kind: str
    display_name: str
    text: str

    def __repr__(self) -> str:
        """仅显示安全元数据，避免日志意外记录消息内容。"""
        return f"LoadedMessageText(kind={self.kind!r}, display_name={self.display_name!r})"


def _parse_reference(reference: str) -> tuple[bool, tuple[str, ...], str]:
    """校验引用并返回 builtin 标记、路径段和规范显示名。"""
    if not isinstance(reference, str) or not reference:
        raise MessagePathError("消息文件引用无效")
    if any(ord(character) < 32 or ord(character) == 127 for character in reference):
        raise MessagePathError("消息文件引用无效")

    builtin = reference.startswith("builtin:")
    raw = reference[len("builtin:") :] if builtin else reference
    normalized = raw.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized
        or normalized.endswith("/")
    ):
        raise MessagePathError("消息文件引用无效")

    parts = tuple(normalized.split("/"))
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((" ", "."))
        for part in parts
    ):
        raise MessagePathError("消息文件引用无效")
    if PurePosixPath(normalized).suffix.lower() != ".txt":
        raise MessagePathError("消息文件必须为 txt 文件")
    return builtin, parts, "/".join(parts)


def validate_message_reference(reference: str) -> None:
    """仅校验可持久化消息引用的语法，不访问文件系统。"""
    _parse_reference(reference)


def read_message_text(
    reference: str,
    runtime_paths: RuntimePaths,
    *,
    max_bytes: int = MAX_MESSAGE_BYTES,
) -> LoadedMessageText:
    """从可信根安全打开并在同一文件句柄内完整读取 UTF-8 文本。"""
    builtin, parts, display_name = _parse_reference(reference)
    roots = (("builtin", runtime_paths.resource_dir),) if builtin else (
        ("user", runtime_paths.messages_dir),
        ("builtin", runtime_paths.resource_dir),
    )
    for kind, root in roots:
        try:
            data = _read_beneath(root, parts, max_bytes)
        except FileNotFoundError:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MessagePathError("消息文件不是有效的 UTF-8 文本") from error
        return LoadedMessageText(kind=kind, display_name=display_name, text=text)
    raise MessagePathError("消息文件不存在")


def _read_beneath(root: Path, parts: tuple[str, ...], max_bytes: int) -> bytes:
    """按平台使用不跟随重解析点的句柄链读取受控文件。"""
    if os.name == "nt":
        return _read_beneath_windows(root, parts, max_bytes)
    return _read_beneath_posix(root, parts, max_bytes)


def _read_beneath_posix(root: Path, parts: tuple[str, ...], max_bytes: int) -> bytes:
    """使用 openat 与 O_NOFOLLOW 在 POSIX 上保守读取。"""
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        current_fd = os.open(root, flags)
        directory_fds.append(current_fd)
        for part in parts[:-1]:
            current_fd = os.open(part, flags, dir_fd=current_fd)
            directory_fds.append(current_fd)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_fd,
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MessagePathError("消息文件类型不安全")
        if info.st_size > max_bytes:
            raise MessagePathError("消息文件超过大小限制")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(file_fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise MessagePathError("消息文件超过大小限制")
        return data
    except FileNotFoundError:
        raise
    except MessagePathError:
        raise
    except OSError as error:
        raise MessagePathError("消息文件路径不安全") from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


if os.name == "nt":
    from ctypes import wintypes

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        """对应 Windows BY_HANDLE_FILE_INFORMATION ABI。"""

        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _CreateFileW.restype = wintypes.HANDLE
    _GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    )
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _GetFileType = _kernel32.GetFileType
    _GetFileType.argtypes = (wintypes.HANDLE,)
    _GetFileType.restype = wintypes.DWORD
    _ReadFile = _kernel32.ReadFile
    _ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    _ReadFile.restype = wintypes.BOOL
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = (wintypes.HANDLE,)
    _CloseHandle.restype = wintypes.BOOL

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_SHARE_READ = 0x1
    _FILE_SHARE_WRITE = 0x2
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x10
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_TYPE_DISK = 1
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_PATH_NOT_FOUND = 3


def _win_open(path: Path, *, directory: bool) -> int:
    """打开 Windows 路径且禁止跟随最终重解析点。"""
    access = _FILE_READ_ATTRIBUTES if directory else _GENERIC_READ
    share = _FILE_SHARE_READ | _FILE_SHARE_WRITE if directory else _FILE_SHARE_READ
    flags = _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    handle = _CreateFileW(str(path), access, share, None, _OPEN_EXISTING, flags, None)
    handle_value = ctypes.cast(handle, ctypes.c_void_p).value
    if handle_value == _INVALID_HANDLE_VALUE:
        code = ctypes.get_last_error()
        if code in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            raise FileNotFoundError
        raise MessagePathError("消息文件路径不安全")
    return handle_value


def _win_info(handle: int) -> "_BY_HANDLE_FILE_INFORMATION":
    """读取句柄元数据，失败时以固定安全类别结束。"""
    info = _BY_HANDLE_FILE_INFORMATION()
    if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise MessagePathError("消息文件路径不安全")
    return info


def _read_beneath_windows(root: Path, parts: tuple[str, ...], max_bytes: int) -> bytes:
    """持有从可信根到文件的全部 Windows 句柄并从最终句柄读取。"""
    handles: list[int] = []
    try:
        current = root
        root_handle = _win_open(current, directory=True)
        handles.append(root_handle)
        root_info = _win_info(root_handle)
        if root_info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise MessagePathError("消息文件路径不安全")
        if not root_info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise MessagePathError("消息文件路径不安全")

        for part in parts[:-1]:
            current = current / part
            directory_handle = _win_open(current, directory=True)
            handles.append(directory_handle)
            info = _win_info(directory_handle)
            if (
                info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
                or not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
            ):
                raise MessagePathError("消息文件路径不安全")

        file_handle = _win_open(current / parts[-1], directory=False)
        handles.append(file_handle)
        info = _win_info(file_handle)
        if (
            info.dwFileAttributes & (_FILE_ATTRIBUTE_REPARSE_POINT | _FILE_ATTRIBUTE_DIRECTORY)
            or info.nNumberOfLinks != 1
            or _GetFileType(file_handle) != _FILE_TYPE_DISK
        ):
            raise MessagePathError("消息文件类型不安全")
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        if size > max_bytes:
            raise MessagePathError("消息文件超过大小限制")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            requested = min(65536, remaining)
            buffer = ctypes.create_string_buffer(requested)
            read = wintypes.DWORD()
            if not _ReadFile(file_handle, buffer, requested, ctypes.byref(read), None):
                raise MessagePathError("消息文件读取失败")
            if read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
            remaining -= read.value
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise MessagePathError("消息文件超过大小限制")
        return data
    finally:
        for handle in reversed(handles):
            _CloseHandle(handle)
