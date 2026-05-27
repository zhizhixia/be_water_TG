from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from src.config import Settings

logger = logging.getLogger(__name__)


class TelegramSender:
    """Telegram 消息发送器，管理客户端连接和消息发送。"""

    def __init__(self, settings: Settings) -> None:
        """初始化发送器。

        Args:
            settings: 项目配置。
        """
        self._settings = settings
        self._client: TelegramClient | None = None

    async def start(
        self,
        code_callback: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        """启动 Telegram 客户端并完成登录。

        首次运行时通过手机号和验证码登录，后续运行自动复用 session 文件。

        Args:
            code_callback: 可选异步回调，用于获取验证码。
                          如果为 None，则使用 CLI 的 input() 交互方式。
        """
        # 配置代理（如果设置）
        proxy = None
        if self._settings.proxy_host and self._settings.proxy_port:
            proxy_type = self._settings.proxy_type or "http"
            proxy = (proxy_type, self._settings.proxy_host, self._settings.proxy_port)
            logger.info(
                "使用 %s 代理: %s:%d",
                proxy_type,
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
            if code_callback:
                code = await code_callback()
            else:
                code = input("请输入收到的验证码: ")
            await self._client.sign_in(self._settings.phone, code)
            logger.info("登录成功！")
        else:
            logger.info("已通过 session 文件自动登录")

    async def is_authorized(self) -> bool:
        """检查用户是否已授权登录。

        Returns:
            True 如果客户端已连接且用户已授权，否则 False。
        """
        if self._client is None:
            return False
        if not self._client.is_connected():
            return False
        return await self._client.is_user_authorized()

    async def send_message(
        self, text: str, target_group: str | None = None
    ) -> None:
        """发送消息到目标群组。

        Args:
            text: 要发送的消息文本。
            target_group: 目标群组 username（可选，默认使用配置中的群组）。

        Raises:
            FloodWaitError: 触发 Telegram 限流时抛出（已自动等待）。
            ConnectionError: 网络连接异常时抛出。
            Exception: 其他异常时抛出。
        """
        target = target_group if target_group else self._settings.target_groups[0]
        try:
            await self._client.send_message(target, text)
            logger.info("✅ 已发送: %r → %s", text[:30], target)
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

    async def get_recent_messages(self, entity: str, limit: int = 5) -> list:
        """获取指定实体的最近消息。

        Args:
            entity: 目标实体（群组 username 或 ID）。
            limit: 获取的消息数量。

        Returns:
            消息列表，客户端未连接时返回空列表。
        """
        if self._client is None or not self._client.is_connected():
            logger.warning("客户端未连接，无法获取消息")
            return []
        return await self._client.get_messages(entity, limit=limit)

    async def disconnect(self) -> None:
        """断开 Telegram 客户端连接。"""
        if self._client:
            await self._client.disconnect()
            logger.info("已断开 Telegram 连接")
