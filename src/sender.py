from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum

from telethon import TelegramClient, types, utils
from telethon.errors import FloodWaitError

from src.config import Settings
from src.group_identity import GroupIdentity
from src.lottery_models import ButtonSnapshot, MessageSnapshot
from src.runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)


class PublisherTrust(StrEnum):
    """由 Telegram 当前群实体校验产生的发布者可信级别。"""

    OWNER = "owner"
    ADMIN = "admin"
    BOT = "bot"
    UNKNOWN = "unknown"


class TelegramSender:
    """Telegram 消息发送器，管理客户端连接和消息发送。"""

    def __init__(
        self,
        settings: Settings,
        runtime_paths: RuntimePaths | None = None,
    ) -> None:
        """初始化发送器。

        Args:
            settings: 项目配置。
        """
        self._settings = settings
        self._runtime_paths = runtime_paths
        self._client: TelegramClient | None = None
        self._input_peers: dict[int, object] = {}

    def _telegram_target(self, peer: object) -> object:
        """把稳定状态 ID 映射为已解析 InputPeer，避免数字字符串越过 Telegram 边界。"""
        peers = getattr(self, "_input_peers", {})
        if isinstance(peer, int) and peer in peers:
            return peers[peer]
        if isinstance(peer, str):
            try:
                marked_id = int(peer)
            except ValueError:
                return peer
            if marked_id in peers:
                return peers[marked_id]
            raise RuntimeError("稳定 peer_id 尚未解析为 Telegram InputPeer")
        return peer

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

        session_path = (self._runtime_paths or RuntimePaths.from_environment()).sender_session_path
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(
            str(session_path),
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
            await self._client.send_message(self._telegram_target(target), text)
            logger.info("✅ 已发送: %r → %s", text[:30], target)
        except FloodWaitError as e:
            # 策略上移至 send_loop：这里仅记录并立即 raise，不再自行 sleep
            logger.warning("⚠️ 触发 FloodWait，需要等待 %d 秒", e.seconds)
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
        return await self._client.get_messages(self._telegram_target(entity), limit=limit)

    async def resolve_group_identity(self, ref: str) -> GroupIdentity:
        """解析授权入口为稳定 peer_id；未连接或解析失败均拒绝启动。"""
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        entity = await self._client.get_entity(ref)
        input_peer = await self._client.get_input_entity(entity)
        peer_id = int(utils.get_peer_id(entity))
        self._input_peers[peer_id] = input_peer
        username = getattr(entity, "username", None)
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or ref
        link = f"https://t.me/{username}" if username else None
        return GroupIdentity(peer_id, ref, str(title), username, link)

    async def get_latest_message_id(self, peer_id: int) -> int | None:
        messages = await self.get_messages_after(peer_id, 0, 1, None)
        return messages[-1].message_id if messages else None

    @staticmethod
    def _snapshot(peer_id: int, message: object, source: str = "incremental") -> MessageSnapshot:
        """将易变 Telethon 消息映射为不可变、无裸实体的快照。"""
        rows = getattr(message, "buttons", None) or getattr(message, "reply_markup", None)
        buttons: list[tuple[ButtonSnapshot, ...]] = []
        row_values = rows if isinstance(rows, (list, tuple)) else (getattr(rows, "rows", []) if rows else [])
        for row in row_values:
            snapshot_row = []
            raw_buttons = row if isinstance(row, (list, tuple)) else getattr(row, "buttons", [])
            for wrapped_button in raw_buttons:
                button = getattr(wrapped_button, "button", wrapped_button)
                if isinstance(button, (types.KeyboardButtonWebView, types.KeyboardButtonSimpleWebView)) or getattr(button, "webview", None) is not None:
                    kind, data = "web_app", None
                elif isinstance(button, types.KeyboardButtonCallback) or hasattr(button, "data") and getattr(button, "data") is not None:
                    kind, data = "callback", bytes(button.data)
                elif isinstance(button, (types.InputKeyboardButtonUrlAuth, types.KeyboardButtonUrlAuth)) or getattr(button, "login_url", None) is not None:
                    kind, data = "login", None
                elif isinstance(button, types.KeyboardButtonBuy) or getattr(button, "invoice", False):
                    kind, data = "payment", None
                elif isinstance(button, types.KeyboardButtonUrl) or hasattr(button, "url"):
                    kind, data = "url", None
                else:
                    kind, data = "unknown", None
                snapshot_row.append(ButtonSnapshot(str(getattr(wrapped_button, "text", getattr(button, "text", ""))), kind, data))
            buttons.append(tuple(snapshot_row))
        date = getattr(message, "date", None)
        return MessageSnapshot(
            peer_id=peer_id,
            message_id=int(message.id),
            text=str(getattr(message, "message", "") or ""),
            buttons=tuple(buttons),
            occurred_at=date.isoformat() if date else None,
            is_reply=bool(getattr(message, "reply_to", None)),
            sender_id=getattr(message, "sender_id", None),
            pinned=bool(getattr(message, "pinned", False)),
            source=source,
        )

    async def get_messages_after(self, peer_id: int, message_id: int, limit: int = 500, min_occurred_at=None) -> list[MessageSnapshot]:
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        messages = await self._client.get_messages(self._telegram_target(peer_id), min_id=message_id, limit=min(limit, 500))
        result = [self._snapshot(peer_id, item) for item in reversed(messages) if item]
        return [item for item in result if min_occurred_at is None or (item.occurred_at and item.occurred_at >= min_occurred_at.isoformat())]

    async def get_message(self, peer_id: int, message_id: int) -> MessageSnapshot | None:
        """读取单条当前消息；已删除消息返回 None。"""
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        message = await self._client.get_messages(self._telegram_target(peer_id), ids=message_id)
        return self._snapshot(peer_id, message) if message else None

    async def get_current_pinned_messages(self, peer_id: int) -> tuple[list[MessageSnapshot], bool]:
        """读取当前置顶；不支持完整枚举时仅返回最新置顶并标记降级。"""
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        iterator = self._client.iter_messages(
            self._telegram_target(peer_id), limit=100, filter=types.InputMessagesFilterPinned()
        )
        values = [item async for item in iterator]
        return [self._snapshot(peer_id, item, "pinned") for item in values if item and getattr(item, "pinned", False)], True

    async def get_publisher_trust(self, peer_id: int, sender_id: int | None) -> PublisherTrust:
        """按当前群成员实体验证 owner/admin/bot，任意权限异常均保守未知。"""
        if self._client is None or sender_id is None:
            return PublisherTrust.UNKNOWN
        try:
            target = self._telegram_target(peer_id)
            group_entity = await self._client.get_entity(target)
            if int(utils.get_peer_id(group_entity)) != peer_id:
                return PublisherTrust.UNKNOWN
            target = await self._client.get_input_entity(group_entity)
            self._input_peers[peer_id] = target
            permissions = await self._client.get_permissions(target, sender_id)
            participant = getattr(permissions, "participant", permissions)
            if bool(getattr(participant, "creator", False) or getattr(participant, "is_creator", False)):
                return PublisherTrust.OWNER
            if bool(getattr(permissions, "is_admin", False) or getattr(participant, "admin_rights", None)):
                return PublisherTrust.ADMIN
            entity = await self._client.get_entity(sender_id)
            if bool(getattr(entity, "bot", False)):
                return PublisherTrust.BOT
        except Exception:
            logger.warning("无法校验发布者权限，按未知发布者处理", exc_info=True)
        return PublisherTrust.UNKNOWN

    async def click_callback(self, peer_id: int, message_id: int, row: int | None, column: int | None) -> str:
        if self._client is None or row is None or column is None:
            raise RuntimeError("Invalid callback action")
        message = await self._client.get_messages(self._telegram_target(peer_id), ids=message_id)
        return str(await message.click(row, column))

    async def send_group_text(self, peer_id: int, text: str) -> int:
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        return int((await self._client.send_message(self._telegram_target(peer_id), text)).id)

    async def reply_to_message(self, peer_id: int, message_id: int, text: str) -> int:
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        return int((await self._client.send_message(self._telegram_target(peer_id), text, reply_to=message_id)).id)

    async def find_own_matching_top_level_text(self, peer_id: int, text: str, after_message_id: int) -> MessageSnapshot | None:
        """查询本人在原消息之后的相同顶层文本，供幂等确认使用。"""
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        me = await self._client.get_me()
        messages = await self.get_messages_after(peer_id, after_message_id, 500, None)
        for snapshot in messages:
            if snapshot.sender_id == getattr(me, "id", None) and not snapshot.is_reply and snapshot.text == text:
                return snapshot
        return None

    async def find_own_matching_reply(self, peer_id: int, message_id: int, text: str) -> MessageSnapshot | None:
        """查询本人对指定原消息的相同回复，供幂等确认使用。"""
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        me = await self._client.get_me()
        replies = await self._client.get_messages(self._telegram_target(peer_id), reply_to=message_id, limit=500)
        for raw in replies or []:
            snapshot = self._snapshot(peer_id, raw)
            if snapshot.sender_id == getattr(me, "id", None) and snapshot.text == text:
                return snapshot
        return None

    def build_group_link(self, identity: GroupIdentity) -> str | None:
        """构建公开群链接；私有群无链接时返回 None。"""
        return identity.display_link

    def build_message_link(self, identity: GroupIdentity, message_id: int) -> str | None:
        """构建公开深链，私有频道退化为 t.me/c，其他私有群返回 None。"""
        if identity.display_link:
            return f"{identity.display_link}/{message_id}"
        raw_id, peer_type = utils.resolve_id(identity.peer_id)
        if peer_type is types.PeerChannel:
            return f"https://t.me/c/{raw_id}/{message_id}"
        return None

    async def is_group_admin(self, entity: str, user_id: int) -> bool:
        """Return whether a message sender currently has admin rights in a group."""
        if self._client is None or not self._client.is_connected():
            return False
        permissions = await self._client.get_permissions(self._telegram_target(entity), user_id)
        participant = getattr(permissions, "participant", permissions)
        return bool(
            getattr(permissions, "is_admin", False)
            or getattr(participant, "admin_rights", None)
            or getattr(participant, "creator", False)
        )

    async def disconnect(self) -> None:
        """断开 Telegram 客户端连接。"""
        if self._client:
            await self._client.disconnect()
            logger.info("已断开 Telegram 连接")

    async def typing_indicator(self, entity: str) -> None:
        """向指定实体显示"正在输入..."状态，模拟真人打字延迟。

        异常时优雅降级：typing 状态是反检测增强的辅助功能，
        失败不应阻止消息发送。

        Args:
            entity: 目标实体（群组 username 或 ID）。
        """
        if self._client is None or not self._client.is_connected():
            return
        from telethon.tl.functions.messages import SetTypingRequest
        from telethon.tl.types import SendMessageTypingAction

        try:
            await self._client(SetTypingRequest(
                peer=self._telegram_target(entity),
                action=SendMessageTypingAction(),
            ))
        except Exception as e:
            logger.warning("设置 typing 状态失败 [%s]: %s", entity, e)
