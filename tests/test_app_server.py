from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask
import pytest

import main
from src.app_server import APP_NAME, APP_VERSION, AppServer, AppServerStartupError
from src.runtime_state import ApplicationRuntimeState


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
    runtime_state = ApplicationRuntimeState()
    runtime_state.update(configured=True)
    server = AppServer(
        app,
        port=0,
        runtime_state=runtime_state,
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
        runtime_state=ApplicationRuntimeState(),
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
    """入口识别到既有 BeWaterTG 后不创建第二个服务或运行数据。"""
    class Instance:
        def acquire(self) -> bool:
            return False

        def existing_service_is_ours(self) -> bool:
            return True

        def release(self) -> None:
            raise AssertionError("未持有锁不应释放")

    monkeypatch.setattr(main, "AppInstance", lambda port: Instance())
    monkeypatch.setattr(main, "RuntimePaths", AssertionError)
    monkeypatch.setattr(main, "ApplicationRuntimeState", AssertionError)
    monkeypatch.setattr(main, "migrate_legacy_runtime_data", AssertionError)
    monkeypatch.setattr(main, "AppServer", lambda *args, **kwargs: AssertionError())

    assert main.run_application() == 0


def test_port_conflict_returns_safe_startup_error() -> None:
    """端口被占用时不暴露配置，也不尝试处理占用进程。"""
    def occupied(*_args: object, **_kwargs: object) -> FakeWaitressServer:
        raise OSError("address already in use")

    with pytest.raises(AppServerStartupError, match="端口"):
        AppServer(Flask(__name__), server_factory=occupied).start()


def test_run_application_initializes_runtime_migrates_and_loads_same_paths_before_app(monkeypatch, tmp_path) -> None:
    """持锁后在集中运行目录迁移并加载同一配置快照，再创建本机服务。"""
    events: list[object] = []

    class Paths:
        resource_dir = tmp_path / "program-resource"
        logs_dir = tmp_path / "runtime" / "logs"

        def ensure_directories(self) -> None:
            events.append("ensure")
            self.logs_dir.mkdir(parents=True, exist_ok=True)

    class Instance:
        def acquire(self) -> bool:
            events.append("acquire")
            return True

        def release(self) -> None:
            events.append("release")

    class Server:
        port = 5000

        def __init__(self, app: object, **kwargs: object) -> None:
            events.append(("server", app, kwargs["runtime_state"].snapshot()))

        def start(self) -> None:
            events.append("start")

        def wait(self) -> None:
            events.append("wait")

        def stop(self) -> None:
            events.append("stop")

    paths = Paths()
    monkeypatch.setattr(main, "AppInstance", lambda port: Instance())
    monkeypatch.setattr(main.RuntimePaths, "from_environment", lambda: paths)
    migration_result = type("MigrationResult", (), {"status": "ready", "migrated": (), "skipped": ()})()
    monkeypatch.setattr(main, "migrate_legacy_runtime_data", lambda legacy, actual: events.append(("migrate", legacy, actual)) or migration_result)
    monkeypatch.setattr(main, "_load_configuration_status", lambda actual: events.append(("load", actual)) or True)
    monkeypatch.setattr(main, "AppServer", Server)
    monkeypatch.setattr(
        "web_app.create_app",
        lambda **kwargs: events.append(("create_app", kwargs)) or "fake-app",
    )

    assert main.run_application() == 0
    assert events == [
        "acquire",
        "ensure",
        "ensure",
        ("migrate", paths.resource_dir, paths),
        ("load", paths),
        (
            "create_app",
            {
                "runtime_paths": paths,
                "runtime_state": events[5][1]["runtime_state"],
            },
        ),
        (
            "server",
            "fake-app",
            {
                "configured": True,
                "migration_status": "ready",
                "setup_status": "configured",
            },
        ),
        "start",
        "wait",
        "stop",
        "release",
    ]


def test_setup_root_logger_writes_only_to_supplied_runtime_log_directory(monkeypatch, tmp_path) -> None:
    """根日志文件处理器使用集中运行日志目录，不使用源码目录。"""
    runtime_log_dir = tmp_path / "runtime" / "logs"
    captured: dict[str, object] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))

    paths = type(
        "Paths",
        (),
        {"logs_dir": runtime_log_dir, "ensure_directories": lambda self: runtime_log_dir.mkdir(parents=True, exist_ok=True)},
    )()
    main.setup_root_logger(paths)

    handlers = captured["handlers"]
    file_handler = next(handler for handler in handlers if isinstance(handler, logging.FileHandler))
    assert Path(file_handler.baseFilename) == runtime_log_dir / "be_water.log"
    assert "main.py" not in file_handler.baseFilename
    file_handler.close()


@pytest.mark.parametrize("failure", ["paths", "migration"])
def test_runtime_startup_failure_releases_lock_without_creating_server(monkeypatch, failure) -> None:
    """运行目录或迁移失败时安全退出并释放已持有实例锁。"""
    events: list[str] = []

    class Instance:
        def acquire(self) -> bool:
            events.append("acquire")
            return True

        def release(self) -> None:
            events.append("release")

    class Paths:
        resource_dir = Path("C:/program-resource")
        logs_dir = Path("C:/runtime/logs")

        def ensure_directories(self) -> None:
            return None

    monkeypatch.setattr(main, "AppInstance", lambda port: Instance())
    if failure == "paths":
        monkeypatch.setattr(main.RuntimePaths, "from_environment", lambda: (_ for _ in ()).throw(RuntimeError("no path")))
    else:
        monkeypatch.setattr(main.RuntimePaths, "from_environment", lambda: Paths())
        monkeypatch.setattr(main, "setup_root_logger", lambda paths: None)
        monkeypatch.setattr(main, "migrate_legacy_runtime_data", lambda legacy, paths: (_ for _ in ()).throw(RuntimeError("no migration")))
    monkeypatch.setattr(main, "AppServer", lambda *args, **kwargs: events.append("server"))

    assert main.run_application() == 1
    assert events == ["acquire", "release"]


@pytest.mark.parametrize(
    "migration_status",
    ["custom_database_requires_manual_copy", "migration_failed"],
)
def test_non_ready_migration_starts_local_web_without_loading_configuration(
    monkeypatch, tmp_path, migration_status
) -> None:
    """迁移未就绪时保持未配置状态，但仍启动本地 Web 供后续向导使用。"""
    events: list[object] = []

    class Instance:
        def acquire(self) -> bool:
            return True

        def release(self) -> None:
            events.append("release")

    class Server:
        port = 5000

        def __init__(self, app: object, **kwargs: object) -> None:
            events.append(("server", app, kwargs["runtime_state"].snapshot()))

        def start(self) -> None:
            events.append("start")

        def wait(self) -> None:
            events.append("wait")

        def stop(self) -> None:
            events.append("stop")

    paths = type(
        "Paths",
        (),
        {
            "resource_dir": tmp_path / "resources",
            "logs_dir": tmp_path / "logs",
            "ensure_directories": lambda self: self.logs_dir.mkdir(parents=True, exist_ok=True),
        },
    )()
    result = type(
        "MigrationResult",
        (),
        {"status": migration_status, "migrated": (), "skipped": ()},
    )()
    monkeypatch.setattr(main, "AppInstance", lambda port: Instance())
    monkeypatch.setattr(main.RuntimePaths, "from_environment", lambda: paths)
    monkeypatch.setattr(main, "setup_root_logger", lambda actual: None)
    monkeypatch.setattr(main, "migrate_legacy_runtime_data", lambda legacy, actual: result)
    monkeypatch.setattr(
        main,
        "_load_configuration_status",
        lambda actual: (_ for _ in ()).throw(AssertionError("迁移未就绪不得加载配置")),
    )
    monkeypatch.setattr(main, "AppServer", Server)
    monkeypatch.setattr(
        "web_app.create_app",
        lambda **kwargs: events.append(("create_app", kwargs)) or "fake-app",
    )

    assert main.run_application() == 0
    server_event = next(event for event in events if isinstance(event, tuple) and event[0] == "server")
    assert server_event[2] == {
        "configured": False,
        "migration_status": migration_status,
        "setup_status": "configuration_required",
    }
    assert events[-4:] == ["start", "wait", "stop", "release"]
