"""Waitress 本地服务宿主。"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify

from src.runtime_state import ApplicationRuntimeState

APP_NAME = "BeWaterTG"
APP_VERSION = "1.0.1"


class AppServerStartupError(RuntimeError):
    """本地服务无法监听时抛出。"""


class AppServer:
    """仅将 Flask 应用托管到本机回环地址的 Waitress 服务。"""

    def __init__(
        self,
        app: Flask,
        *,
        port: int = 5000,
        runtime_state: ApplicationRuntimeState | None = None,
        server_factory: Callable[..., Any] | None = None,
    ) -> None:
        """使用可注入端口、状态提供器和服务工厂创建本地宿主。"""
        self._app = app
        self._requested_port = port
        self._runtime_state = runtime_state or ApplicationRuntimeState()
        self._server_factory = server_factory
        self._server: Any | None = None
        self._thread: threading.Thread | None = None
        self._run_error: BaseException | None = None
        self._install_health_route()

    @property
    def port(self) -> int:
        """返回实际监听端口；启动前返回请求端口。"""
        if self._server is None:
            return self._requested_port
        return int(getattr(self._server, "effective_port", self._requested_port))

    def start(self) -> None:
        """创建 Waitress 监听器并在后台运行；失败时不留下服务资源。"""
        if self._server is not None:
            return
        try:
            factory = self._server_factory
            if factory is None:
                from waitress import create_server

                factory = create_server
            self._server = factory(self._app, host="127.0.0.1", port=self._requested_port)
        except OSError as exc:
            self.stop()
            raise AppServerStartupError("本地服务端口被占用或不可用") from exc
        except Exception:
            self.stop()
            raise
        self._thread = threading.Thread(target=self._run, name="bewatertg-waitress", daemon=True)
        self._thread.start()

    def wait(self) -> None:
        """等待 Waitress 服务线程结束，并传播服务异常。"""
        if self._thread is not None:
            self._thread.join()
        if self._run_error is not None:
            raise self._run_error

    def stop(self) -> None:
        """关闭服务并回收后台线程。"""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _install_health_route(self) -> None:
        if "be_water_health" in self._app.view_functions:
            return

        @self._app.get("/api/health", endpoint="be_water_health")
        def health() -> Any:
            status = self._runtime_state.snapshot()
            return jsonify({
                "app": APP_NAME,
                "version": APP_VERSION,
                "configured": bool(status.get("configured", False)),
            })

    def _run(self) -> None:
        try:
            if self._server is not None:
                self._server.run()
        except BaseException as exc:
            self._run_error = exc
