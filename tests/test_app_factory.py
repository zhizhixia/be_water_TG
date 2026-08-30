"""Flask 工厂的依赖注入与实例隔离契约测试。"""
from __future__ import annotations

import logging

from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState
from web_app import create_app


def _ready_state() -> ApplicationRuntimeState:
    """构造已完成迁移和配置检查的非敏感运行态。"""
    state = ApplicationRuntimeState()
    state.update(
        configured=True,
        migration_status="ready",
        setup_status="configured",
    )
    return state


def test_create_app_injects_paths_state_and_isolates_manager_and_event_bus(tmp_path) -> None:
    """每个工厂实例必须拥有自己的路径、管理器和事件总线。"""
    first_paths = RuntimePaths(tmp_path / "resources", tmp_path / "first")
    second_paths = RuntimePaths(tmp_path / "resources", tmp_path / "second")
    first_state = _ready_state()
    second_state = _ready_state()

    first = create_app(runtime_paths=first_paths, runtime_state=first_state)
    second = create_app(runtime_paths=second_paths, runtime_state=second_state)

    assert first is not second
    assert first.extensions["runtime_paths"] is first_paths
    assert first.extensions["runtime_state"] is first_state
    assert second.extensions["runtime_paths"] is second_paths
    assert second.extensions["runtime_state"] is second_state
    assert first.extensions["send_loop_manager"] is not second.extensions["send_loop_manager"]
    assert (
        first.extensions["send_loop_manager"].event_bus
        is not second.extensions["send_loop_manager"].event_bus
    )


def test_web_app_module_has_no_global_flask_app_or_manager() -> None:
    """导入模块不得创建依赖真实环境的 Flask 或发送管理器全局对象。"""
    import web_app

    assert not hasattr(web_app, "app")
    assert not hasattr(web_app, "manager")


def test_create_app_does_not_register_root_log_handler_or_publish_logs(tmp_path) -> None:
    """仅创建 Flask 实例不得改变根日志器，也不得向任一实例广播日志。"""
    root_logger = logging.getLogger()
    baseline_handlers = list(root_logger.handlers)
    apps = []
    subscriptions = []
    try:
        for name in ("first", "second"):
            app = create_app(
                runtime_paths=RuntimePaths(tmp_path / "resources", tmp_path / name),
                runtime_state=_ready_state(),
            )
            apps.append(app)
            event_bus = app.extensions["send_loop_manager"].event_bus
            subscriptions.append((event_bus, event_bus.subscribe()))

        logging.getLogger("tests.app_factory.lifecycle").warning("factory-only warning")

        assert root_logger.handlers == baseline_handlers
        assert all(event_queue.empty() for _, event_queue in subscriptions)
    finally:
        for event_bus, event_queue in subscriptions:
            event_bus.unsubscribe(event_queue)
        for app in apps:
            handler = app.extensions.get("log_queue_handler")
            if handler is not None:
                root_logger.removeHandler(handler)
