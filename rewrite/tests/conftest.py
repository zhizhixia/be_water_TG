"""新版测试默认使用匿名数据，禁止真实 Telegram 或 AI 连接。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bewater.config import ConfigStore
from bewater.models import Identity, Paths, SHANGHAI
from bewater.store import Store
from bewater.runtime import Controller


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """隔离本机数据和外部 socket，任何真实网络漏用立即失败。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    def deny(*args, **kwargs):
        """测试没有外网授权。"""
        raise AssertionError("测试禁止真实外部连接")

    original_connect = socket.socket.connect
    socketpair_code = getattr(socket.socketpair, "__code__", None)

    def guarded_connect(sock, address):
        """仅放行标准库 socketpair 自身的本地唤醒管道，不放行代理流量。"""
        if socketpair_code is not None and sys._getframe(1).f_code is socketpair_code:
            return original_connect(sock, address)
        return deny(sock, address)

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def paths(tmp_path):
    """临时数据根与候选真实资源根分离。"""
    value = Paths(Path(__file__).resolve().parents[1], tmp_path / "runtime")
    value.prepare()
    return value


@pytest.fixture
def at():
    """固定上海时间，不依赖机器时区和当前日期。"""
    return datetime(2026, 10, 18, 10, 0, tzinfo=SHANGHAI)


@pytest.fixture
def setup(paths, at):
    """创建真实配置和 SQLite 组成的匿名应用底座。"""
    config = ConfigStore(paths)
    _, rev = config.load()
    public = config.update(
        {
            "api_id": 12345,
            "api_hash": "a" * 32,
            "phone": "+10000000000",
            "groups": [
                {
                    "ref": "https://t.me/allowed_group",
                    "chat": True,
                    "checkin": True,
                    "lottery": True,
                    "authorized": True,
                    "messages": "messages.txt",
                }
            ],
        },
        rev,
    )
    assert public["revision"]
    store = Store(paths.database)
    identity = Identity("https://t.me/allowed_group", -1000000000123, "匿名授权群", "allowed_group")
    store.identity(identity, at)
    controller = Controller(paths, config, store)
    controller.state = "running"
    values, _ = config.load()
    return controller, identity, values


class FakeTelegram:
    """真实业务可调用的匿名外部端口，不替代规划、门禁或 SQLite。"""

    def __init__(self, identity, messages=None):
        """记录外部尝试及可变服务端事实。"""
        self.identity = identity
        self.messages = messages or []
        self.calls = []
        self.role = "admin"
        self.fail = None

    async def resolve(self, ref):
        """返回已确认身份。"""
        return replace(self.identity, ref=ref)

    async def recent(self, peer, limit=20):
        """返回可变消息列表的最后一页。"""
        return self.messages[-limit:]

    async def after(self, peer, cursor, limit=100):
        """按最早未处理 ID 正序分页。"""
        return [m for m in self.messages if m.id > cursor][:limit]

    async def latest(self, peer):
        """返回基线最高 ID。"""
        return max([m.id for m in self.messages], default=0)

    async def pinned(self, peer):
        """返回当前置顶事实。"""
        return [m for m in self.messages if m.pinned]

    async def trust(self, peer, sender):
        """允许用降权模拟执行前权限变化。"""
        return self.role

    async def message(self, peer, message_id):
        """消息可被测试删除或编辑。"""
        return next((m for m in self.messages if m.id == message_id), None)

    async def perform(self, peer, action, original=None):
        """只记录真正进入外部调用边界的动作。"""
        self.calls.append((peer, action))
        if self.fail:
            raise self.fail
        return 900 + len(self.calls)
