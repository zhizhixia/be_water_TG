"""新版 Be Water TG 本机启动入口。"""

from __future__ import annotations

import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from waitress import create_server

from bewater.app import create_app
from bewater.models import Paths
from bewater.runtime import FileLock
from bewater.upgrade import prepare_upgrade


class ApplicationLock:
    """与旧版共享 Windows 应用互斥体，防止两个正式入口并行。"""

    def __init__(self, paths: Paths, isolated: bool) -> None:
        """测试目录使用独立文件锁，正式运行与旧版共用命名互斥。"""
        self.file = FileLock(paths.data / "state/application.lock")
        self.handle = None
        self.kernel = None
        self.isolated = isolated

    def acquire(self) -> bool:
        """在初始化运行数据或连接账号前取得单实例锁。"""
        if os.name != "nt" or self.isolated:
            return self.file.acquire()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = ctypes.c_bool
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, "Local\\BeWaterTG-v1")
        if not handle:
            raise OSError("无法取得应用互斥锁")
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        """正常或异常退出都释放持有者句柄。"""
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        self.file.release()


def run(port: int = 5000, isolated_data: str | None = None) -> int:
    """启动网页不启动账号；隔离参数仅用于本机验证和迁移演练。"""
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    base = os.environ.get("LOCALAPPDATA")
    if not base and not isolated_data:
        raise RuntimeError("无法确定本机应用数据目录")
    paths = Paths(resources, Path(isolated_data) if isolated_data else Path(base) / "BeWaterTG")
    lock = ApplicationLock(paths, isolated_data is not None)
    if not lock.acquire():
        print("本机应用已在运行，请打开原控制台。")
        return 0
    server = None
    controller = None
    try:
        paths.prepare()
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                RotatingFileHandler(
                    paths.logs_dir / "bewater-v2.log",
                    maxBytes=2 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
            ],
        )
        legacy = resources if getattr(sys, "frozen", False) else resources.parent
        migration = prepare_upgrade(legacy, paths, isolated_data is not None)
        app = create_app(paths, migration_status=migration.status)
        controller = app.extensions["controller"]
        server = create_server(app, host="127.0.0.1", port=port, threads=12)
        print(f"本机控制台：http://127.0.0.1:{server.effective_port}", flush=True)
        server.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except OSError:
        print("端口不可用，请先关闭占用程序；本应用不会自动结束其他进程。", flush=True)
        return 1
    finally:
        if controller and controller.thread:
            controller.control("stop")
            thread = controller.thread
            if thread:
                thread.join(timeout=35)
        if server:
            server.close()
        lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Be Water TG 本机控制台")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--isolated-data-dir", help="验证专用隔离数据目录，不用于切换真实账号")
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.port, arguments.isolated_data_dir))
