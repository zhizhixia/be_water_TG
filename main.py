"""
Telegram 灌水工具 — Flet GUI 入口

用法:
    python main.py           # 启动 GUI 桌面应用
    python main.py --web     # 在浏览器中打开 (flet web 模式)
"""

import sys
import logging
import flet as ft
from ui.app import main as flet_main

logger = logging.getLogger("main")


def setup_root_logger():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    setup_root_logger()
    logger.info("=" * 50)
    logger.info("Telegram 灌水工具 — Flet GUI 启动")
    logger.info("=" * 50)

    # Check for --web flag
    if "--web" in sys.argv:
        logger.info("启动 Web 模式...")
        ft.app(target=flet_main, view=ft.AppView.WEB_BROWSER)
    else:
        logger.info("启动桌面模式...")
        ft.run(flet_main)
