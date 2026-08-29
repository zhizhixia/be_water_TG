from __future__ import annotations

from flask import Flask
import pytest

import main
from src.app_server import APP_NAME, APP_VERSION, AppServer, AppServerStartupError


class FakeWaitressServer:
    effective_port = 43123

    def __init__(self) -> None:
        self.closed = False

    def run(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_health_is_only_available_at_api_health_with_fixed_safe_status() -> None:
    """健康检查只返回固定标识、版本和配置布尔状态。"""
    app = Flask(__name__)
    server = AppServer(
        app,
        port=0,
        configuration_status=lambda: {"configured": True},
        server_factory=lambda _app, **_: FakeWaitressServer(),
    )

    server.start()

    client = app.test_client()
    assert client.get("/health").status_code == 404

    response = client.get("/api/health")
    assert response.get_json() == {
        "app": APP_NAME,
        "version": APP_VERSION,
        "configured": True,
    }
    server.stop()


def test_health_request_does_not_load_settings_dotenv_or_env(monkeypatch) -> None:
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        calls.append("configuration")
        raise AssertionError("health must not read configuration")

    monkeypatch.setattr("src.config.load_settings", forbidden)
    monkeypatch.setattr("src.config.load_dotenv", forbidden)
    monkeypatch.setattr("src.config.os.getenv", forbidden)

    app = Flask(__name__)
    server = AppServer(
        app,
        configuration_status=main._configuration_status,
        server_factory=lambda _app, **_: FakeWaitressServer(),
    )
    server.start()

    assert app.test_client().get("/api/health").get_json() == {
        "app": APP_NAME,
        "version": APP_VERSION,
        "configured": False,
    }
    assert calls == []
    server.stop()


def test_server_uses_loopback_and_releases_waitress_server() -> None:
    """宿主仅绑定回环地址，并在停止时关闭 Waitress 服务。"""
    app = Flask(__name__)
    created: list[tuple[dict[str, object], FakeWaitressServer]] = []

    def factory(_app: Flask, **kwargs: object) -> FakeWaitressServer:
        waitress_server = FakeWaitressServer()
        created.append((kwargs, waitress_server))
        return waitress_server

    server = AppServer(app, port=0, server_factory=factory)
    server.start()
    assert server.port == 43123
    server.stop()

    assert created[0][0]["host"] == "127.0.0.1"
    assert created[0][1].closed


def test_main_exits_when_existing_local_service_owns_instance(monkeypatch) -> None:
    """入口识别到既有 BeWaterTG 后不创建第二个服务。"""
    class Instance:
        def acquire(self) -> bool:
            return False

        def existing_service_is_ours(self) -> bool:
            return True

        def release(self) -> None:
            raise AssertionError("未持有锁不应释放")

    monkeypatch.setattr(main, "AppInstance", lambda port: Instance())
    monkeypatch.setattr(main, "AppServer", lambda *args, **kwargs: AssertionError())

    assert main.run_application() == 0


def test_port_conflict_returns_safe_startup_error() -> None:
    """端口被占用时不暴露配置，也不尝试处理占用进程。"""
    def occupied(*_args: object, **_kwargs: object) -> FakeWaitressServer:
        raise OSError("address already in use")

    with pytest.raises(AppServerStartupError, match="端口"):
        AppServer(Flask(__name__), server_factory=occupied).start()
