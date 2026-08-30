from __future__ import annotations

import asyncio
import gc
import logging
import time
import weakref
from pathlib import Path

from flask import Flask
import pytest

import main
from src.app_server import APP_NAME, APP_VERSION, AppServer, AppServerStartupError
from src.config import Settings
from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState
from web_app import create_app
from web_manager import (
    EventBus,
    LogQueueHandler,
    bind_log_context,
    current_log_context,
    reset_log_context,
)


class FakeWaitressServer:
    effective_port = 43123

    def __init__(self) -> None:
        self.closed = False

    def run(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _app_with_log_handler(name: str) -> tuple[Flask, EventBus]:
    """构造带实例日志处理器扩展的最小 Flask 应用。"""
    app = Flask(name)
    event_bus = EventBus()
    app.extensions["log_queue_handler"] = LogQueueHandler(event_bus)
    return app, event_bus


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


def test_server_start_installs_current_app_log_handler_until_stop() -> None:
    """服务运行期间只广播到当前应用，停止后移除根日志器引用。"""
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    current_app, current_bus = _app_with_log_handler("current")
    _other_app, other_bus = _app_with_log_handler("other")
    current_queue = current_bus.subscribe()
    other_queue = other_bus.subscribe()
    server = AppServer(
        current_app,
        server_factory=lambda _app, **_: FakeWaitressServer(),
    )
    try:
        server.start()
        assert root_logger.handlers == [
            *baseline_handlers,
            current_app.extensions["log_queue_handler"],
        ]

        logging.getLogger("tests.app_server.lifecycle").warning("while running")
        assert current_queue.get_nowait()[1]["data"]["message"].endswith("while running")
        assert other_queue.empty()

        server.stop()
        assert root_logger.handlers == baseline_handlers

        logging.getLogger("tests.app_server.lifecycle").warning("after stop")
        assert current_queue.empty()
        assert other_queue.empty()
    finally:
        server.stop()
        root_logger.removeHandler(current_app.extensions["log_queue_handler"])
        current_bus.unsubscribe(current_queue)
        other_bus.unsubscribe(other_queue)


def test_server_repeated_start_and_stop_keeps_one_log_handler() -> None:
    """重复启动不得重复注册，重复停止后仍可再次干净启动。"""
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    app, _event_bus = _app_with_log_handler("idempotent")
    handler = app.extensions["log_queue_handler"]
    server = AppServer(app, server_factory=lambda _app, **_: FakeWaitressServer())
    try:
        server.start()
        server.start()
        assert root_logger.handlers.count(handler) == 1
        assert len(root_logger.handlers) == len(baseline_handlers) + 1

        server.stop()
        server.stop()
        assert root_logger.handlers == baseline_handlers

        server.start()
        assert root_logger.handlers.count(handler) == 1
        assert len(root_logger.handlers) == len(baseline_handlers) + 1
    finally:
        server.stop()
        root_logger.removeHandler(handler)


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        (OSError("address already in use"), AppServerStartupError),
        (RuntimeError("factory failed"), RuntimeError),
    ],
)
def test_server_factory_failure_removes_log_handler(
    failure: Exception,
    expected_exception: type[Exception],
) -> None:
    """服务工厂创建监听器失败时不得在根日志器遗留应用 handler。"""
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    app, _event_bus = _app_with_log_handler("factory-failure")
    handler = app.extensions["log_queue_handler"]

    def failing_factory(_app: Flask, **_kwargs: object) -> FakeWaitressServer:
        raise failure

    server = AppServer(app, server_factory=failing_factory)
    try:
        with pytest.raises(expected_exception):
            server.start()
        assert root_logger.handlers == baseline_handlers
    finally:
        server.stop()
        root_logger.removeHandler(handler)


def test_thread_construction_failure_cleans_server_and_handler(monkeypatch) -> None:
    """线程构造失败时关闭监听器、移除 handler，且后续停止保持幂等。"""
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    app, _event_bus = _app_with_log_handler("thread-construction-failure")
    handler = app.extensions["log_queue_handler"]
    waitress_server = FakeWaitressServer()

    def failing_thread(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("thread construction failed")

    monkeypatch.setattr("src.app_server.threading.Thread", failing_thread)
    server = AppServer(app, server_factory=lambda _app, **_: waitress_server)
    try:
        with pytest.raises(RuntimeError, match="thread construction failed"):
            server.start()

        assert waitress_server.closed
        assert root_logger.handlers == baseline_handlers
        assert server._server is None
        assert server._thread is None
        server.stop()
        assert root_logger.handlers == baseline_handlers
    finally:
        root_logger.removeHandler(handler)


def test_thread_start_failure_cleans_without_joining_unstarted_thread(monkeypatch) -> None:
    """线程启动失败时清理服务和 handler，但不得 join 尚未启动的线程。"""
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    app, _event_bus = _app_with_log_handler("thread-start-failure")
    handler = app.extensions["log_queue_handler"]
    waitress_server = FakeWaitressServer()
    join_calls: list[str] = []

    class FailingStartThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("thread start failed")

        def join(self, **_kwargs: object) -> None:
            join_calls.append("join")
            raise AssertionError("未启动线程不得 join")

    monkeypatch.setattr("src.app_server.threading.Thread", FailingStartThread)
    server = AppServer(app, server_factory=lambda _app, **_: waitress_server)
    try:
        with pytest.raises(RuntimeError, match="thread start failed"):
            server.start()

        assert waitress_server.closed
        assert root_logger.handlers == baseline_handlers
        assert server._server is None
        assert server._thread is None
        assert join_calls == []
        server.stop()
        assert join_calls == []
    finally:
        root_logger.removeHandler(handler)


@pytest.mark.parametrize(
    ("thread_failure", "expected_exception"),
    [
        (OSError("thread resource unavailable"), AppServerStartupError),
        (RuntimeError("thread construction failed"), RuntimeError),
    ],
)
def test_start_cleanup_failure_preserves_original_start_exception(
    monkeypatch,
    thread_failure,
    expected_exception,
) -> None:
    """启动失败后的 close 异常不得覆盖原启动异常或其 OSError 包装。"""
    class FailingCloseServer(FakeWaitressServer):
        def close(self) -> None:
            self.closed = True
            raise RuntimeError("close failed")

    waitress_server = FailingCloseServer()

    def failing_thread(*_args: object, **_kwargs: object) -> None:
        raise thread_failure

    monkeypatch.setattr("src.app_server.threading.Thread", failing_thread)
    server = AppServer(
        Flask(__name__),
        server_factory=lambda _app, **_: waitress_server,
    )

    with pytest.raises(expected_exception) as captured:
        server.start()

    if expected_exception is RuntimeError:
        assert str(captured.value) == "thread construction failed"
    assert waitress_server.closed
    assert server._server is None
    assert server._thread is None


def test_restart_clears_previous_run_error() -> None:
    """首轮运行异常不得在停止并正常重启后再次由 wait 传播。"""
    class FailingRunServer(FakeWaitressServer):
        def run(self) -> None:
            raise RuntimeError("first run failed")

    waitress_servers = [FailingRunServer(), FakeWaitressServer()]
    server = AppServer(
        Flask(__name__),
        server_factory=lambda _app, **_: waitress_servers.pop(0),
    )

    server.start()
    with pytest.raises(RuntimeError, match="first run failed"):
        server.wait()
    server.stop()

    server.start()
    server.wait()
    server.stop()


def test_stop_close_failure_still_clears_thread_and_handler() -> None:
    """监听器关闭失败时仍尽最大努力清理线程状态和日志 handler。"""
    class FailingCloseServer(FakeWaitressServer):
        def close(self) -> None:
            self.closed = True
            raise RuntimeError("close failed")

    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    app, _event_bus = _app_with_log_handler("stop-close-failure")
    handler = app.extensions["log_queue_handler"]
    waitress_server = FailingCloseServer()
    server = AppServer(app, server_factory=lambda _app, **_: waitress_server)
    try:
        server.start()
        with pytest.raises(RuntimeError, match="close failed"):
            server.stop()

        assert waitress_server.closed
        assert root_logger.handlers == baseline_handlers
        assert server._server is None
        assert server._thread is None
    finally:
        root_logger.removeHandler(handler)


def test_server_stop_failure_does_not_block_instance_release(monkeypatch, tmp_path) -> None:
    """服务清理异常不得覆盖固定退出码或阻止释放实例锁。"""
    events: list[str] = []

    class Paths:
        resource_dir = tmp_path / "resources"
        logs_dir = tmp_path / "logs"

        def ensure_directories(self) -> None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)

    class Instance:
        def acquire(self) -> bool:
            events.append("acquire")
            return True

        def release(self) -> None:
            events.append("release")

    class Server:
        port = 5000

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("start failed")

        def stop(self) -> None:
            events.append("stop")
            raise RuntimeError("close failed")

    migration_result = type(
        "MigrationResult",
        (),
        {"status": "migration_failed", "migrated": (), "skipped": ()},
    )()
    monkeypatch.setattr(main, "setup_root_logger", lambda _paths: None)

    result = main.run_application(
        paths_factory=Paths,
        migration_function=lambda _legacy, _paths: migration_result,
        instance_factory=lambda **_kwargs: Instance(),
        app_factory=lambda **_kwargs: object(),
        server_factory=Server,
    )

    assert result == 1
    assert events == ["acquire", "stop", "release"]


@pytest.mark.parametrize("failure_stage", ["construction", "start"])
def test_run_application_thread_failure_releases_lock_and_restores_handler(
    monkeypatch,
    tmp_path,
    failure_stage,
) -> None:
    """线程构造或启动失败均应返回 1、释放实例锁并恢复根日志 handler。"""
    events: list[str] = []
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    app, _event_bus = _app_with_log_handler(f"main-{failure_stage}-failure")
    handler = app.extensions["log_queue_handler"]
    waitress_server = FakeWaitressServer()

    class Paths:
        resource_dir = tmp_path / "resources"
        logs_dir = tmp_path / "logs"

        def ensure_directories(self) -> None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)

    class Instance:
        def acquire(self) -> bool:
            events.append("acquire")
            return True

        def release(self) -> None:
            events.append("release")

    class FailingStartThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    def failing_thread_construction(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("thread construction failed")

    thread_factory = (
        failing_thread_construction
        if failure_stage == "construction"
        else FailingStartThread
    )
    monkeypatch.setattr("src.app_server.threading.Thread", thread_factory)
    monkeypatch.setattr(main, "setup_root_logger", lambda _paths: None)
    migration_result = type(
        "MigrationResult",
        (),
        {"status": "migration_failed", "migrated": (), "skipped": ()},
    )()

    def create_server(flask_app: Flask, **kwargs: object) -> AppServer:
        return AppServer(
            flask_app,
            port=int(kwargs["port"]),
            runtime_state=kwargs["runtime_state"],
            server_factory=lambda _app, **_: waitress_server,
        )

    try:
        result = main.run_application(
            paths_factory=Paths,
            migration_function=lambda _legacy, _paths: migration_result,
            instance_factory=lambda **_kwargs: Instance(),
            app_factory=lambda **_kwargs: app,
            server_factory=create_server,
        )

        assert result == 1
        assert events == ["acquire", "release"]
        assert waitress_server.closed
        assert root_logger.handlers == baseline_handlers
    finally:
        root_logger.removeHandler(handler)


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


def _ready_runtime_state() -> ApplicationRuntimeState:
    """构造日志隔离测试使用的就绪状态。"""
    state = ApplicationRuntimeState()
    state.update(configured=True, migration_status="ready", setup_status="configured")
    return state


def _isolated_app(tmp_path: Path, name: str):
    """构造拥有独立 token、manager 与事件总线的应用。"""
    return create_app(
        runtime_paths=RuntimePaths(tmp_path / "resources", tmp_path / name),
        runtime_state=_ready_runtime_state(),
    )


def test_two_running_app_servers_isolate_request_and_server_thread_root_logs(tmp_path: Path) -> None:
    """双服务同时注册 root handler 时，请求与宿主线程日志只进入所属总线。"""
    first = _isolated_app(tmp_path, "first")
    second = _isolated_app(tmp_path, "second")
    first.add_url_rule(
        "/probe",
        "first_probe",
        lambda: (logging.getLogger("tests.request").warning("first-request") or "ok"),
    )
    second.add_url_rule(
        "/probe",
        "second_probe",
        lambda: (logging.getLogger("tests.request").warning("second-request") or "ok"),
    )

    class LoggingServer(FakeWaitressServer):
        def __init__(self, marker: str) -> None:
            super().__init__()
            self.marker = marker

        def run(self) -> None:
            logging.getLogger("tests.waitress").warning(self.marker)

    first_server = AppServer(first, server_factory=lambda _app, **_: LoggingServer("first-server"))
    second_server = AppServer(second, server_factory=lambda _app, **_: LoggingServer("second-server"))
    first_bus = first.extensions["send_loop_manager"].event_bus
    second_bus = second.extensions["send_loop_manager"].event_bus
    first_queue = first_bus.subscribe()
    second_queue = second_bus.subscribe()
    outer = object()
    outer_handle = bind_log_context(outer)
    try:
        first_server.start()
        second_server.start()
        first_server.wait()
        second_server.wait()
        assert first.test_client().get("/probe").status_code == 200
        assert current_log_context() is outer
        assert second.test_client().get("/probe").status_code == 200
        assert current_log_context() is outer

        first_messages = [item[1]["data"]["message"] for item in list(first_queue.queue)]
        second_messages = [item[1]["data"]["message"] for item in list(second_queue.queue)]
        assert any(message.endswith("first-server") for message in first_messages)
        assert any(message.endswith("first-request") for message in first_messages)
        assert not any("second-" in message for message in first_messages)
        assert any(message.endswith("second-server") for message in second_messages)
        assert any(message.endswith("second-request") for message in second_messages)
        assert not any("first-" in message for message in second_messages)

        first_server.stop()
        handle = bind_log_context(second.extensions["log_context_token"])
        try:
            logging.getLogger("tests.after-stop").warning("second-still-running")
        finally:
            reset_log_context(handle)
        assert any(
            item[1]["data"]["message"].endswith("second-still-running")
            for item in list(second_queue.queue)
        )
    finally:
        reset_log_context(outer_handle)
        first_server.stop()
        second_server.stop()
        first_bus.unsubscribe(first_queue)
        second_bus.unsubscribe(second_queue)


def test_unbound_root_log_is_not_broadcast_to_multiple_app_servers(tmp_path: Path) -> None:
    """无实例上下文的 root 日志不得广播到任一 create_app 实例。"""
    first = _isolated_app(tmp_path, "unbound-first")
    second = _isolated_app(tmp_path, "unbound-second")
    servers = [
        AppServer(first, server_factory=lambda _app, **_: FakeWaitressServer()),
        AppServer(second, server_factory=lambda _app, **_: FakeWaitressServer()),
    ]
    queues = [
        app.extensions["send_loop_manager"].event_bus.subscribe()
        for app in (first, second)
    ]
    try:
        for server in servers:
            server.start()
            server.wait()
        logging.getLogger("tests.unbound").warning("unbound-message")
        assert all(event_queue.empty() for event_queue in queues)
    finally:
        for server in servers:
            server.stop()
        for app, event_queue in zip((first, second), queues):
            app.extensions["send_loop_manager"].event_bus.unsubscribe(event_queue)


def test_two_manager_threads_publish_logs_only_to_their_own_app(tmp_path: Path) -> None:
    """两个 SendLoopManager 后台线程的完整生命周期均绑定各自实例 token。"""
    apps = [_isolated_app(tmp_path, name) for name in ("manager-first", "manager-second")]
    servers = [
        AppServer(app, server_factory=lambda _app, **_: FakeWaitressServer())
        for app in apps
    ]
    queues = [app.extensions["send_loop_manager"].event_bus.subscribe() for app in apps]

    class TimeoutSender:
        def __init__(self, marker: str) -> None:
            self.marker = marker

        async def start(self, **_kwargs: object) -> None:
            logging.getLogger("tests.manager-thread").warning(self.marker)
            raise asyncio.TimeoutError

        async def disconnect(self) -> None:
            return None

    try:
        for server in servers:
            server.start()
            server.wait()
        for index, app in enumerate(apps):
            settings = Settings(
                api_id=12345,
                api_hash="a" * 32,
                phone="+861****8000",
                target_groups=["https://t.me/example"],
                state_db_path=str(tmp_path / f"manager-{index}.db"),
            )
            result = app.extensions["send_loop_manager"].start(
                TimeoutSender(f"manager-{index}"), settings, object()
            )
            assert result.ok
        deadline = time.monotonic() + 2
        while any(app.extensions["send_loop_manager"]._thread is not None for app in apps):
            assert time.monotonic() < deadline
            time.sleep(0.01)

        messages = [
            [
                item[1]["data"]["message"]
                for item in list(event_queue.queue)
                if item[1].get("type") == "log"
            ]
            for event_queue in queues
        ]
        assert any(message.endswith("manager-0") for message in messages[0])
        assert not any(message.endswith("manager-1") for message in messages[0])
        assert any(message.endswith("manager-1") for message in messages[1])
        assert not any(message.endswith("manager-0") for message in messages[1])
        assert current_log_context() is None
    finally:
        for server in servers:
            server.stop()
        for app, event_queue in zip(apps, queues):
            app.extensions["send_loop_manager"].event_bus.unsubscribe(event_queue)


def test_stopped_app_server_releases_event_bus_for_gc(tmp_path: Path) -> None:
    """停止服务并释放应用引用后，root handler 不得继续持有旧事件总线。"""
    app = _isolated_app(tmp_path, "collectible")
    event_bus = app.extensions["send_loop_manager"].event_bus
    reference = weakref.ref(event_bus)
    server = AppServer(app, server_factory=lambda _app, **_: FakeWaitressServer())
    server.start()
    server.wait()
    server.stop()

    del event_bus
    del server
    del app
    gc.collect()

    assert reference() is None
