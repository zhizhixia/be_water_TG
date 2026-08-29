"""BeWaterTG 本地服务入口。"""
from __future__ import annotations

import logging
import sys

from src.app_instance import AppInstance
from src.app_server import AppServer, AppServerStartupError

logger = logging.getLogger("main")
_configured = False


def setup_root_logger() -> None:
    """配置终端和轮转文件日志。"""
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                log_dir / "be_water.log", maxBytes=5 * 1024 * 1024,
                backupCount=3, encoding="utf-8",
            ),
        ],
    )


def _configuration_status() -> dict[str, bool]:
    return {"configured": _configured}


def _load_configuration_status() -> bool:
    try:
        from src.config import load_settings

        load_settings()
        return True
    except Exception:
        return False


def run_application(port: int = 5000) -> int:
    """启动本机 Waitress 服务；已有实例或端口冲突时安全退出。"""
    instance = AppInstance(port=port)
    if not instance.acquire():
        if instance.existing_service_is_ours():
            logger.info("BeWaterTG 本地服务已在运行")
            return 0
        logger.error("应用实例已被占用，无法确认既有本地服务")
        return 1

    global _configured
    _configured = _load_configuration_status()
    server: AppServer | None = None
    try:
        from web_app import create_app

        server = AppServer(create_app(), port=port, configuration_status=_configuration_status)
        server.start()
        logger.info("本地服务已启动: http://127.0.0.1:%s", server.port)
        server.wait()
        return 0
    except AppServerStartupError:
        logger.error("本地服务端口被其他服务占用或不可用")
        return 1
    finally:
        if server is not None:
            server.stop()
        instance.release()


if __name__ == "__main__":
    setup_root_logger()
    sys.exit(run_application())
