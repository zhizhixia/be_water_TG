from __future__ import annotations

import asyncio
import sys

from telethon.errors import FloodWaitError

from src.config import load_settings
from src.interval import format_duration, get_random_interval
from src.logger import setup_logger
from src.message_loader import load_messages, validate_messages
from src.selector import MessageSelector
from src.sender import TelegramSender

logger = setup_logger("main")

RETRY_DELAYS = [30, 60, 120]
MAX_RETRIES = 3
RETRY_FAIL_WAIT = 300


async def main() -> None:
    """主循环：加载配置和消息，然后持续随机发送。"""
    # 加载配置
    try:
        settings = load_settings()
    except ValueError as e:
        logger.error("配置错误: %s", e)
        sys.exit(1)

    # 加载消息
    try:
        messages = load_messages("messages.txt")
        validate_messages(messages)
    except (FileNotFoundError, ValueError) as e:
        logger.error("消息文件错误: %s", e)
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("Telegram Auto-Sender 启动")
    logger.info("目标群组: %s", settings.target_group)
    logger.info("发送间隔: %d ~ %d 秒", settings.min_interval, settings.max_interval)
    logger.info("消息数量: %d 条", len(messages))
    logger.info("=" * 50)

    # 初始化选择器和发送器
    selector = MessageSelector(messages)
    sender = TelegramSender(settings)

    try:
        await sender.start()
    except Exception as e:
        logger.error("Telegram 登录失败: %s", e)
        sys.exit(1)

    # 主循环
    try:
        while True:
            message = selector.select()

            # 带重试的发送
            sent = False
            for attempt in range(MAX_RETRIES):
                try:
                    await sender.send_message(message)
                    sent = True
                    break
                except FloodWaitError:
                    # FloodWait 已在 sender 内部等待，直接重试发送
                    logger.info("FloodWait 等待结束，重新发送...")
                    continue
                except (ConnectionError, OSError) as e:
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        logger.warning(
                            "网络异常 (第 %d/%d 次): %s，%d 秒后重试...",
                            attempt + 1,
                            MAX_RETRIES,
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "网络异常重试 %d 次均失败: %s", MAX_RETRIES, e
                        )
                except Exception as e:
                    logger.error("发送失败: %s", e)
                    break

            if not sent:
                logger.warning("本次发送失败，等待 %d 秒后继续下一轮", RETRY_FAIL_WAIT)
                await asyncio.sleep(RETRY_FAIL_WAIT)
                continue

            # 生成随机间隔
            interval = get_random_interval(settings.min_interval, settings.max_interval)
            logger.info("⏳ 下次发送等待: %s", format_duration(interval))
            await asyncio.sleep(interval)

    except (KeyboardInterrupt, asyncio.CancelledError) as e:
        logger.info("正在优雅退出... (触发原因: %s)", type(e).__name__)
    finally:
        await sender.disconnect()
        logger.info("程序已退出")


if __name__ == "__main__":
    asyncio.run(main())
