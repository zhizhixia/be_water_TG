"""所有真实 Telegram 调用集中于此，SDK 实体不会泄漏到业务层。"""

from __future__ import annotations

import re

from datetime import datetime

from telethon import TelegramClient, functions, types, utils
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
)

from bewater.models import Action, Button, DomainError, Identity, Message, Paths, SHANGHAI


class Telegram:
    """可替换的真实适配器；构造不连接网络。"""

    def __init__(self, values: dict[str, object], paths: Paths) -> None:
        """注入配置与会话路径，不加载进程级 dotenv。"""
        proxy = None
        if values["proxy_host"]:
            proxy = (values["proxy_type"], values["proxy_host"], values["proxy_port"])
        self.client = TelegramClient(
            str(paths.session),
            values["api_id"],
            values["api_hash"],
            proxy=proxy,
            flood_sleep_threshold=0,
            request_retries=0,
            connection_retries=2,
        )
        self.values = values
        self.peers: dict[int, object] = {}
        self.me: int | None = None

    async def login(self, challenge) -> None:
        """验证码/两步密码仅通过当前挑战获取，错误允许有限次数重输。"""
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.values["phone"])
            await self._authenticate(challenge)
        self.me = int((await self.client.get_me()).id)

    async def _authenticate(self, challenge) -> None:
        """按服务端要求切换挑战类型，不记录认证输入。"""
        kind = "code"
        for _ in range(3):
            secret = await challenge(kind)
            try:
                if kind == "password":
                    await self.client.sign_in(password=secret)
                else:
                    await self.client.sign_in(self.values["phone"], secret)
                return
            except SessionPasswordNeededError:
                kind = "password"
            except (PhoneCodeInvalidError, PasswordHashInvalidError):
                continue
        raise DomainError("认证连续失败，请停止后重新开始")

    async def close(self) -> None:
        """清理客户端连接。"""
        await self.client.disconnect()

    async def resolve(self, ref: str) -> Identity:
        """只解析已加入的群；禁止自动加入与将频道/单聊当作群。"""
        if "/+" in ref:
            invite = await self.client(
                functions.messages.CheckChatInviteRequest(ref.rsplit("+", 1)[-1])
            )
            if not isinstance(invite, types.ChatInviteAlready):
                raise DomainError("私密群尚未加入，请人工处理")
            entity = invite.chat
        else:
            entity = await self.client.get_entity(ref)
        if not isinstance(entity, (types.Chat, types.Channel)):
            raise DomainError("授权对象不是 Telegram 群")
        if isinstance(entity, types.Channel) and not entity.megagroup:
            raise DomainError("只允许群和超级群，不允许广播频道")
        if getattr(entity, "left", False) or getattr(entity, "deactivated", False):
            raise DomainError("账号不在该群或该群已停用")
        peer = int(utils.get_peer_id(entity))
        self.peers[peer] = await self.client.get_input_entity(entity)
        return Identity(ref, peer, entity.title, getattr(entity, "username", None))

    def _peer(self, peer: int) -> object:
        """没有已解析身份时拒绝向数据库字符串或猜测实体发送。"""
        if type(peer) is not int or peer not in self.peers:
            raise DomainError("群身份未确认，拒绝访问")
        return self.peers[peer]

    def snapshot(self, peer: int, raw, source: str = "recent") -> Message:
        """按真实 Telethon 二维按钮结构生成不可变快照。"""
        rows = []
        for row in raw.buttons or []:
            buttons = []
            for wrapped in row:
                button = getattr(wrapped, "button", wrapped)
                kind = (
                    "callback" if isinstance(button, types.KeyboardButtonCallback) else "external"
                )
                buttons.append(
                    Button(
                        str(button.text), kind, bytes(button.data) if kind == "callback" else b""
                    )
                )
            rows.append(tuple(buttons))
        timestamp = raw.date or datetime.now(SHANGHAI)
        return Message(
            peer,
            int(raw.id),
            raw.raw_text or "",
            timestamp.astimezone(SHANGHAI),
            getattr(raw, "sender_id", None),
            bool(raw.out),
            getattr(getattr(raw, "reply_to", None), "reply_to_msg_id", None),
            tuple(rows),
            bool(raw.pinned),
            source,
        )

    async def recent(self, peer: int, limit: int = 20) -> list[Message]:
        """返回按 ID 正序的近期消息。"""
        values = await self.client.get_messages(self._peer(peer), limit=limit)
        return [self.snapshot(peer, raw) for raw in reversed(values) if raw]

    async def latest(self, peer: int) -> int:
        """仅用于冷启动建立普通消息基线。"""
        values = await self.client.get_messages(self._peer(peer), limit=1)
        return int(values[0].id) if values else 0

    async def after(self, peer: int, cursor: int, limit: int = 100) -> list[Message]:
        """从游标之后最早一条开始读取，避免取最新页造成漏扫。"""
        result = []
        async for raw in self.client.iter_messages(
            self._peer(peer), min_id=cursor, reverse=True, limit=limit
        ):
            result.append(self.snapshot(peer, raw, "incremental"))
        return result

    async def pinned(self, peer: int) -> list[Message]:
        """完整枚举当前置顶，出错时不假装完整。"""
        result = []
        async for raw in self.client.iter_messages(
            self._peer(peer), filter=types.InputMessagesFilterPinned()
        ):
            if raw.pinned:
                result.append(self.snapshot(peer, raw, "pinned"))
        return result

    async def message(self, peer: int, message_id: int) -> Message | None:
        """执行前重新读取指定消息。"""
        raw = await self.client.get_messages(self._peer(peer), ids=message_id)
        return self.snapshot(peer, raw) if raw else None

    async def trust(self, peer: int, sender_id: int | None) -> str:
        """查询失败转未知，唯独 FloodWait 继续传播到统一冷却。"""
        from telethon.errors import FloodWaitError

        try:
            return await self._current_trust(peer, sender_id)
        except FloodWaitError:
            raise
        except Exception:
            return "unknown"

    async def _current_trust(self, peer: int, sender_id: int | None) -> str:
        """刷新群实体及发布者成员资格，已离开或被禁者不得自动执行。"""
        if sender_id is None:
            return "unknown"
        group = await self.client.get_entity(self._peer(peer))
        if int(utils.get_peer_id(group)) != peer:
            return "unknown"
        self.peers[peer] = await self.client.get_input_entity(group)
        permissions = await self.client.get_permissions(self._peer(peer), sender_id)
        if permissions.has_left or permissions.is_banned:
            return "unknown"
        if permissions.is_creator:
            return "owner"
        if permissions.is_admin:
            return "admin"
        sender = await self.client.get_entity(sender_id)
        return "bot" if getattr(sender, "bot", False) else "member"

    async def perform(
        self, peer: int, action: Action, original: Message | None = None
    ) -> int | None:
        """只提交已复核白名单动作，不自动重试未知外部结果。"""
        target = self._peer(peer)
        if action.kind == "callback":
            if original is None or action.row is None or action.column is None:
                raise DomainError("回调缺少原消息证据")
            button = original.buttons[action.row][action.column]
            if button.kind != "callback":
                raise DomainError("按钮不是允许的回调")
            answer = await self.client(
                functions.messages.GetBotCallbackAnswerRequest(
                    target, action.message_id, data=button.data
                )
            )
            message = getattr(answer, "message", None) or ""
            confirmed = re.fullmatch(
                r"(?:已(?:成功)?参与|参与成功|成功参与)(?:[！!。.,， ]*(?:等待开奖|祝你好运))?[！!。 ]*",
                message,
            )
            if getattr(answer, "url", None) or not confirmed:
                raise DomainError("按钮返回未明确确认参与，保守记为结果未知")
            return None
        if action.kind not in {"text", "reply"}:
            raise DomainError("不支持的外部动作")
        result = await self.client.send_message(
            target,
            action.text,
            reply_to=action.message_id if action.kind == "reply" else None,
            parse_mode=None,
            link_preview=False,
        )
        return int(result.id)
