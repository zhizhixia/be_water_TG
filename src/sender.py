from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from src.config import Settings
from src.logger import setup_logger

logger = setup_logger("sender")


class TelegramSender:
    """Telegram 消息发送器，管理客户端连接和消息发送。"""

    def __init__(self, settings: Settings) -> None:
        """初始化发送器。

        Args:
            settings: 项目配置。
        """
        self._settings = settings
        self._client: TelegramClient | None = None

    async def start(self) -> None:
        """启动 Telegram 客户端并完成登录。

        首次运行时通过手机号和验证码登录，后续运行自动复用 session 文件。
        """
        # 配置代理（如果设置）
        proxy = None
        if self._settings.proxy_host and self._settings.proxy_port:
            proxy = ("http", self._settings.proxy_host, self._settings.proxy_port)
            logger.info(
                "使用 HTTP 代理: %s:%d",
                self._settings.proxy_host,
                self._settings.proxy_port,
            )

        self._client = TelegramClient(
            "sender_session",
            self._settings.api_id,
            self._settings.api_hash,
            proxy=proxy,
        )
        await self._client.connect()

        if not await self._client.is_user_authorized():
            logger.info("首次登录，正在发送验证码...")
            await self._client.send_code_request(self._settings.phone)
            code = input("请输入收到的验证码: ")
            await self._client.sign_in(self._settings.phone, code)
            logger.info("登录成功！")
        else:
            logger.info("已通过 session 文件自动登录")

    async def send_message(self, text: str) -> None:
        """发送消息到目标群组。

        Args:
            text: 要发送的消息文本。

        Raises:
            FloodWaitError: 触发 Telegram 限流时抛出（已自动等待）。
            ConnectionError: 网络连接异常时抛出。
            Exception: 其他异常时抛出。
        """
        try:
            await self._client.send_message(self._settings.target_group, text)
            logger.info("✅ 已发送: \"%s\"", text)
        except FloodWaitError as e:
            logger.warning(
                "⚠️ 触发 FloodWait，需要等待 %d 秒", e.seconds
            )
            await asyncio.sleep(e.seconds)
            raise
        except ConnectionError as e:
            logger.error("❌ 网络连接异常: %s", e)
            raise
        except Exception as e:
            logger.exception("❌ 发送消息时发生未知异常")
            raise

    async def disconnect(self) -> None:
        """断开 Telegram 客户端连接。"""
        if self._client:
            await self._client.disconnect()
            logger.info("已断开 Telegram 连接")
