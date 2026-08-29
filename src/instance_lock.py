from __future__ import annotations

import ctypes
import hashlib
import json
import os
import socket
import time
from pathlib import Path


def _process_creation_time(pid: int) -> float | None:
    """读取 Windows 进程创建时间；不存在或无法确认时返回 None。"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return None
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created = ctypes.c_ulonglong()
        exited = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return created.value / 10_000_000
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class InstanceLock:
    """Windows 单实例文件锁，拒绝活锁且只回收可证实陈旧的锁。"""

    def __init__(self, account: str, database_path: str) -> None:
        """用账号摘要和规范化数据库绝对路径生成稳定锁路径。"""
        self.account_digest = hashlib.sha256(account.encode("utf-8")).hexdigest()
        database = Path(database_path).resolve()
        digest = hashlib.sha256(f"{self.account_digest}:{database}".encode("utf-8")).hexdigest()
        self.path = database.parent / f".be_water_{digest}.lock"
        self.held = False

    def _is_stale(self) -> bool:
        """仅当锁元数据无效或 PID 创建时间不匹配时确认锁陈旧。"""
        try:
            metadata = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(metadata["pid"])
            recorded_created = float(metadata["created_at"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return True
        actual_created = _process_creation_time(pid)
        return actual_created is None or actual_created != recorded_created

    def acquire(self) -> bool:
        """获取锁；已有可确认活锁时 fail closed。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if not self._is_stale():
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            creation_time = _process_creation_time(os.getpid())
            metadata = {"pid": os.getpid(), "created_at": creation_time if creation_time is not None else time.time(), "account_digest": self.account_digest, "holder": socket.gethostname()}
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(metadata, file, sort_keys=True)
            self.held = True
            return True
        return False

    def release(self) -> None:
        """仅由当前持有者移除本实例锁。"""
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False
