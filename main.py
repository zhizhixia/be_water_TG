"""BeWaterTG 本地服务入口。"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from src.app_instance import AppInstance
from src.app_server import AppServer, AppServerStartupError
from src.runtime_migration import migrate_legacy_runtime_data
from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState

logger = logging.getLogger("main")


def setup_root_logger(paths: RuntimePaths) -> None:
    """将终端和轮转文件日志配置到集中运行目录。"""
    from logging.handlers import RotatingFileHandler

    paths.ensure_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                paths.logs_dir / "be_water.log", maxBytes=5 * 1024 * 1024,
                backupCount=3, encoding="utf-8",
            ),
        ],
    )


def _load_configuration_status(paths: RuntimePaths) -> bool:
    """使用集中运行目录加载一次配置，并返回不含敏感信息的结果。"""
    try:
        from src.config import load_settings

        load_settings(runtime_paths=paths)
        return True
    except Exception:
        return False


def run_application(
    port: int = 5000,
    *,
    paths_factory: Callable[[], RuntimePaths] | None = None,
    migration_function: Callable[[object, RuntimePaths], object] | None = None,
    instance_factory: Callable[..., AppInstance] | None = None,
    app_factory: Callable[..., object] | None = None,
    server_factory: Callable[..., AppServer] | None = None,
) -> int:
    """启动本机 Waitress 服务；运行数据失败或端口冲突时安全退出。"""
    instance = (instance_factory or AppInstance)(port=port)
    if not instance.acquire():
        if instance.existing_service_is_ours():
            logger.info("BeWaterTG 本地服务已在运行")
            return 0
        logger.error("应用实例已被占用，无法确认既有本地服务")
        return 1

    server: AppServer | None = None
    try:
        paths = (paths_factory or RuntimePaths.from_environment)()
        runtime_state = ApplicationRuntimeState()
        paths.ensure_directories()
        setup_root_logger(paths)
        migration_result = (migration_function or migrate_legacy_runtime_data)(paths.resource_dir, paths)
        migration_status = str(getattr(migration_result, "status", "migration_failed"))
        logger.info(
            "旧运行数据迁移结果：%d 项已迁移，%d 项跳过",
            len(getattr(migration_result, "migrated", ())),
            len(getattr(migration_result, "skipped", ())),
        )

        configured = False
        if migration_status == "ready":
            configured = _load_configuration_status(paths)
        runtime_state.update(
            configured=configured,
            migration_status=migration_status,
            setup_status="configured" if configured else "configuration_required",
        )
        if app_factory is None:
            from web_app import create_app

            app_factory = create_app
        app = app_factory(runtime_paths=paths, runtime_state=runtime_state)
        server = (server_factory or AppServer)(app, port=port, runtime_state=runtime_state)
        server.start()
        logger.info("本地服务已启动: http://127.0.0.1:%s", server.port)
        server.wait()
        return 0
    except AppServerStartupError:
        logger.error("本地服务端口被其他服务占用或不可用")
        return 1
    except Exception:
        logger.exception("运行目录初始化或旧数据迁移失败，应用安全退出")
        return 1
    finally:
        try:
            if server is not None:
                try:
                    server.stop()
                except Exception:
                    logger.exception("本地服务清理失败")
        finally:
            instance.release()


if __name__ == "__main__":
    sys.exit(run_application())
