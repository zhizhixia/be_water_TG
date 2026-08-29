"""应用级单实例与本地服务识别。"""
from __future__ import annotations

import ctypes
import json
import os
import tempfile
from http.client import HTTPConnection
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from src.app_server import APP_NAME, APP_VERSION


class _Lock(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class _WindowsMutex:
    """以 Windows 命名互斥体保存应用进程生命周期。"""

    def __init__(self, *, kernel32: object | None = None,
                 set_last_error: Callable[[int], None] = ctypes.set_last_error,
                 get_last_error: Callable[[], int] = ctypes.get_last_error) -> None:
        self._handle: int | None = None
        self._kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
        self._set_last_error = set_last_error
        self._get_last_error = get_last_error
        self._kernel32.CreateMutexW.restype = ctypes.c_void_p
        self._kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self._kernel32.CloseHandle.restype = ctypes.c_bool
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    def acquire(self) -> bool:
        """创建本会话互斥体；已有持有者时返回 False。"""
        self._set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, "Local\\BeWaterTG-v1")
        if not handle:
            raise OSError(self._get_last_error(), "无法创建应用互斥体")
        if self._get_last_error() == 183:
            self._kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """关闭本进程持有的互斥体句柄。"""
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class _FileLock:
    """非 Windows 环境的最小互斥回退实现。"""

    def __init__(self) -> None:
        self._path = Path(tempfile.gettempdir()) / "BeWaterTG-v1.lock"
        self._held = False

    def acquire(self) -> bool:
        """原子创建临时锁文件。"""
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.close(descriptor)
        self._held = True
        return True

    def release(self) -> None:
        """移除当前进程创建的临时锁文件。"""
        if self._held:
            self._path.unlink(missing_ok=True)
            self._held = False


def is_local_bewater_service(port: int = 5000) -> bool:
    """仅通过 127.0.0.1 健康路由确认既有服务身份。"""
    connection = HTTPConnection("127.0.0.1", port=port, timeout=0.5)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    finally:
        connection.close()
    return (
        isinstance(payload, dict)
        and set(payload) == {"app", "version", "configured"}
        and payload["app"] == APP_NAME
        and payload["version"] == APP_VERSION
        and isinstance(payload["configured"], bool)
    )


class AppInstance:
    """保护整个 BeWaterTG 应用进程只运行一个实例。"""

    def __init__(self, *, port: int = 5000, lock_factory: type[_Lock] | None = None,
                 health_probe: Callable[[], bool] | None = None) -> None:
        """创建应用锁；测试可注入锁和本地健康探针。"""
        self._lock = (lock_factory or (_WindowsMutex if os.name == "nt" else _FileLock))()
        self._health_probe = health_probe or (lambda: is_local_bewater_service(port))
        self._held = False

    def acquire(self) -> bool:
        """获取应用级单实例锁。"""
        self._held = self._lock.acquire()
        return self._held

    def existing_service_is_ours(self) -> bool:
        """确认锁的既有持有者是否为本机 BeWaterTG 服务。"""
        try:
            return bool(self._health_probe())
        except Exception:
            return False

    def release(self) -> None:
        """释放当前实例持有的应用锁。"""
        if self._held:
            self._lock.release()
            self._held = False
