"""
Telegram 灌水工具 — Flask Web GUI 入口

用法:
    python main.py           # 启动 Web 界面 (http://127.0.0.1:5000)
"""

import logging
import sys

logger = logging.getLogger("main")


def setup_root_logger():
    """配置根日志：终端输出 + 文件持久化（自动轮转）。"""
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(
            log_dir / "be_water.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


if __name__ == "__main__":
    setup_root_logger()
    logger.info("=" * 50)
    logger.info("Telegram 灌水工具 — Flask Web UI 启动")
    logger.info("=" * 50)

    from web_app import create_app

    app = create_app()
    logger.info("访问地址: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
