"""浏览器交互验证专用服务：匿名端口替身，绝不创建真实 Telegram 客户端。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from waitress import serve
from bewater.app import create_app
from bewater.config import ConfigStore
from bewater.models import Button, Identity, Message, Paths, now
from conftest import FakeTelegram

ROOT = Path(__file__).resolve().parents[1]
PATHS = Paths(ROOT, ROOT.parent / ".hermes/rewrite/browser-live")
IDENTITY = Identity(
    "https://t.me/test_group", -1000000000123, "匿名演示群（测试专用）", "test_group"
)


class BrowserGateway(FakeTelegram):
    """浏览器使用真实控制器和业务服务，仅平台端口为替身。"""

    def __init__(self, values, paths):
        """构造公开测试公告，不读真实会话或配置。"""
        at = now()
        end = (at + timedelta(hours=1)).isoformat(timespec="seconds")
        message = Message(
            IDENTITY.peer_id,
            1,
            f"抽奖\n奖品：测试礼物\n点击“参与”按钮\n截止：{end}",
            at,
            8,
            buttons=((Button("参与", "callback", b"public-test"),),),
            pinned=True,
        )
        super().__init__(IDENTITY, [message])
        self.polls = 0

    async def login(self, challenge):
        """测试服务不登录、不发送验证码。"""
        return None

    async def close(self):
        """测试没有需要释放的真实网络连接。"""
        return None

    async def after(self, peer, cursor, limit=100):
        """首次增量观察产生问题，不依赖重复拉取上下文的次数。"""
        self.polls += 1
        if self.polls == 1:
            self.messages.append(Message(peer, 2, "请教大家，Python服务怎么部署更稳定？", now(), 7))
        return await super().after(peer, cursor, limit)


if __name__ == "__main__":
    PATHS.prepare()
    config = ConfigStore(PATHS)
    if not PATHS.config.exists():
        _, revision = config.load()
        config.update(
            {
                "api_id": 12345,
                "api_hash": "a" * 32,
                "phone": "+10000000000",
                "question_reply_pct": 100,
                "reply_delay_min": 0,
                "reply_delay_max": 0,
                "groups": [
                    {
                        "ref": IDENTITY.ref,
                        "chat": True,
                        "checkin": True,
                        "lottery": True,
                        "authorized": True,
                        "messages": "messages.txt",
                    }
                ],
            },
            revision,
        )
    app = create_app(PATHS, telegram_factory=BrowserGateway)
    print("匿名浏览器验证服务：http://127.0.0.1:5058，不连接真实 Telegram", flush=True)
    serve(app, host="127.0.0.1", port=5058, threads=12)
