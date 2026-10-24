from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon import types, utils

from src.group_identity import GroupIdentity
from src.config import Settings
from src.runtime_paths import RuntimePaths
from src.sender import PublisherTrust, TelegramSender


class FakeClient:
    """记录受控适配器调用的本地 Telegram 替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.messages = [
            SimpleNamespace(id=3, message="新", date=datetime(2026, 8, 29, 1, tzinfo=timezone.utc), buttons=None, reply_to=None, pinned=False),
            SimpleNamespace(id=2, message="旧", date=datetime(2026, 8, 29, 0, tzinfo=timezone.utc), buttons=None, reply_to=None, pinned=False),
        ]

    async def get_messages(self, peer_id, **kwargs):
        self.calls.append(("get_messages", peer_id, kwargs))
        if "ids" in kwargs:
            return next((item for item in self.messages if item.id == kwargs["ids"]), None)
        return self.messages

    async def send_message(self, peer_id, text, **kwargs):
        self.calls.append(("send_message", peer_id, text, kwargs))
        return SimpleNamespace(id=88)


@pytest.mark.asyncio
async def test_sender_uses_runtime_session_path(monkeypatch, tmp_path) -> None:
    """真实 Telegram 客户端接收集中运行目录中的 session 文件路径。"""
    created: list[str] = []

    class SessionClient:
        def __init__(self, session, *_args, **_kwargs) -> None:
            created.append(session)

        async def connect(self) -> None:
            return None

        async def is_user_authorized(self) -> bool:
            return True

    monkeypatch.setattr("src.sender.TelegramClient", SessionClient)
    paths = RuntimePaths.from_environment(tmp_path / "appdata", tmp_path / "resources")
    settings = Settings(api_id=1, api_hash="x" * 32, phone="+1", target_groups=["https://t.me/group"])

    await TelegramSender(settings, runtime_paths=paths).start()

    assert created == [str(paths.sender_session_path)]


@pytest.mark.asyncio
async def test_sender_returns_sorted_immutable_snapshots_and_send_ids() -> None:
    """读取必须升序快照，发送和回复必须返回 Telegram 消息号。"""
    sender = TelegramSender.__new__(TelegramSender)
    sender._client = FakeClient()

    messages = await sender.get_messages_after(9, 1)

    assert [item.message_id for item in messages] == [2, 3]
    assert messages[0].text == "旧"
    assert await sender.send_group_text(9, "+1") == 88
    assert await sender.reply_to_message(9, 3, "参加") == 88
    with pytest.raises(AttributeError):
        messages[0].text = "编辑后"


def test_sender_button_and_link_fallbacks_are_explicit() -> None:
    """按钮类型和公开、私有、群会话链接必须按可用信息降级。"""
    callback = SimpleNamespace(text="参与", data=b"go")
    url = SimpleNamespace(text="规则", url="https://example.com")
    web = SimpleNamespace(text="打开", url="https://example.com", webview=object())
    message = SimpleNamespace(
        id=5, message="抽奖", date=None, reply_to=None, pinned=False,
        buttons=SimpleNamespace(rows=[SimpleNamespace(buttons=[callback, url, web])]),
    )
    snapshot = TelegramSender._snapshot(100, message)
    sender = TelegramSender.__new__(TelegramSender)

    assert [button.kind for button in snapshot.buttons[0]] == ["callback", "url", "web_app"]
    assert snapshot.buttons[0][0].callback_data == b"go"
    assert sender.build_message_link(GroupIdentity(1, "x", "群", "public", "https://t.me/public"), 5) == "https://t.me/public/5"
    channel_peer_id = utils.get_peer_id(types.PeerChannel(1))
    assert sender.build_message_link(GroupIdentity(channel_peer_id, "x", "群", None, None), 5) == "https://t.me/c/1/5"
    assert sender.build_group_link(GroupIdentity(1, "x", "群", None, None)) is None


@pytest.mark.asyncio
async def test_sender_publisher_permission_failure_is_unknown(monkeypatch) -> None:
    """当前群角色读取失败必须保守映射为 unknown。"""
    sender = TelegramSender.__new__(TelegramSender)

    class PermissionsClient:
        async def get_permissions(self, *_args):
            raise RuntimeError("无权限")

    sender._client = PermissionsClient()
    assert await sender.get_publisher_trust(1, 7) is PublisherTrust.UNKNOWN


@pytest.mark.asyncio
async def test_resolved_channel_uses_marked_peer_id_and_typed_input_peer() -> None:
    """频道身份必须使用 Telethon marked ID，后续数字状态键映射回 InputPeer。"""
    entity = types.Channel(
        id=123, title="允许群", photo=types.ChatPhotoEmpty(), date=None,
        megagroup=True, access_hash=456,
    )
    input_peer = types.InputPeerChannel(123, 456)

    class Client:
        async def get_entity(self, ref):
            assert ref == "https://t.me/allowed"
            return entity

        async def get_input_entity(self, value):
            assert value is entity
            return input_peer

        async def send_message(self, target, text):
            assert target is input_peer
            return SimpleNamespace(id=8)

    sender = TelegramSender.__new__(TelegramSender)
    sender._client = Client()
    sender._input_peers = {}
    identity = await sender.resolve_group_identity("https://t.me/allowed")

    assert identity.peer_id == -1000000000123
    assert await sender.send_group_text(identity.peer_id, "签到") == 8


@pytest.mark.asyncio
async def test_typing_indicator_maps_stable_string_to_typed_input_peer() -> None:
    """主循环传入数字状态键时，typing 请求也必须使用缓存的 InputPeer。"""
    input_peer = types.InputPeerChannel(123, 456)
    requests = []

    class Client:
        def is_connected(self):
            return True

        async def __call__(self, request):
            requests.append(request)

    sender = TelegramSender.__new__(TelegramSender)
    sender._client = Client()
    sender._input_peers = {-1000000000123: input_peer}

    await sender.typing_indicator("-1000000000123")

    assert len(requests) == 1
    assert requests[0].peer is input_peer


@pytest.mark.asyncio
async def test_publisher_trust_refreshes_group_entity_before_permission_check() -> None:
    """执行前信任复核必须重取群实体并刷新 InputPeer。"""
    entity = types.Channel(
        id=123, title="允许群", photo=types.ChatPhotoEmpty(), date=None,
        megagroup=True, access_hash=789,
    )
    old_peer = types.InputPeerChannel(123, 456)
    refreshed_peer = types.InputPeerChannel(123, 789)
    calls = []

    class Client:
        async def get_entity(self, target):
            calls.append(("entity", target))
            return entity

        async def get_input_entity(self, value):
            calls.append(("input", value))
            return refreshed_peer

        async def get_permissions(self, target, sender_id):
            calls.append(("permissions", target, sender_id))
            return SimpleNamespace(is_admin=True, participant=SimpleNamespace())

    sender = TelegramSender.__new__(TelegramSender)
    sender._client = Client()
    sender._input_peers = {-1000000000123: old_peer}

    assert await sender.get_publisher_trust(-1000000000123, 9) is PublisherTrust.ADMIN
    assert calls == [
        ("entity", old_peer),
        ("input", entity),
        ("permissions", refreshed_peer, 9),
    ]


@pytest.mark.asyncio
async def test_real_tl_buttons_and_pinned_filter_are_adapted() -> None:
    """Telethon 1.44 二维按钮和置顶过滤器必须走真实 SDK 数据结构。"""
    message = SimpleNamespace(
        id=5, message="抽奖", date=None, reply_to=None, pinned=True,
        buttons=[[
            types.KeyboardButtonCallback("参与", b"go"),
            types.KeyboardButtonUrl("规则", "https://example.com"),
        ]],
    )
    input_peer = types.InputPeerChannel(123, 456)

    class Client:
        def iter_messages(self, target, **kwargs):
            assert target is input_peer
            assert isinstance(kwargs["filter"], types.InputMessagesFilterPinned)

            async def values():
                yield message
            return values()

    sender = TelegramSender.__new__(TelegramSender)
    sender._client = Client()
    sender._input_peers = {-1000000000123: input_peer}
    pinned, complete = await sender.get_current_pinned_messages(-1000000000123)

    assert complete is True
    assert [button.kind for button in pinned[0].buttons[0]] == ["callback", "url"]
