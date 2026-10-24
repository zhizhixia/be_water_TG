"""新旧共用反例：只替换外部边界，不修改任一版本源码。"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "rewrite"))
for name in re.findall(r"^([A-Z_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M):
    os.environ.pop(name, None)

from bewater.app import create_app as new_app
from bewater.config import ConfigStore
from bewater.models import Message, Paths, SHANGHAI
from bewater.policy import lottery_plan
from bewater.store import Store
from bewater.telegram import Telegram
from src.config import Settings, save_settings
from src.lottery_models import MessageSnapshot
from src.lottery_planner import plan
from src.lottery_store import LotteryStore
from src.sender import TelegramSender, PublisherTrust
from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState
from web_app import create_app as old_app

AT = datetime(2026, 10, 18, 10, tzinfo=SHANGHAI)
PEER = -1000000000123
RESULTS = []


def record(name, expected, old, new) -> None:
    """预期来自共同需求而非两版实现相互生成。"""
    RESULTS.append(
        {
            "scenario": name,
            "expected": expected,
            "old_actual": old,
            "new_actual": new,
            "old_pass": old == expected,
            "new_pass": new == expected,
        }
    )


def config_roundtrip(root: Path) -> None:
    """只保存普通额度，不应该把签到/抽奖由 false 变 true。"""
    old_paths = RuntimePaths(ROOT, root / "old")
    old_paths.ensure_directories()
    values = Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+10000000000",
        target_groups=["https://t.me/test_group"],
        lottery_enabled=False,
        daily_checkin_enabled=False,
    )
    save_settings(values, runtime_paths=old_paths)
    runtime = ApplicationRuntimeState()
    runtime.update(configured=True, migration_status="ready", setup_status="configured")
    client = old_app(runtime_paths=old_paths, runtime_state=runtime).test_client()
    payload = client.get("/api/config").get_json()["config"]
    payload["daily_limit"] = 25
    response = client.post("/api/config", json=payload)
    if response.status_code != 200:
        raise AssertionError("旧版配置探针无法实际保存")
    from src.config import load_settings

    saved = load_settings(old_paths)
    old = [saved.daily_checkin_enabled, saved.lottery_enabled]
    paths = Paths(ROOT / "rewrite", root / "new")
    paths.prepare()
    config = ConfigStore(paths)
    _, revision = config.load()
    config.update(
        {
            "groups": [
                {
                    "ref": "https://t.me/test_group",
                    "chat": True,
                    "checkin": False,
                    "lottery": False,
                    "authorized": True,
                    "messages": "messages.txt",
                }
            ]
        },
        revision,
    )
    client = new_app(paths).test_client()
    initial = client.get("/api/bootstrap").get_json()
    result = client.post(
        "/api/config",
        json={"revision": initial["config"]["revision"], "patch": {"daily_limit": 25}},
        headers={"X-CSRF-Token": initial["csrf"]},
    )
    assert result.status_code == 200
    group = config.load()[0]["groups"][0]
    record("仅保存额度不复活自动任务", [False, False], old, [group["checkin"], group["lottery"]])


def text_action() -> None:
    """原文固定回复是独立参与路径，不需要 callback。"""
    text = "抽奖\n奖品：耳机\n回复“+1”参与"
    old = plan(
        MessageSnapshot(
            PEER, 20, text, publisher_trust=PublisherTrust.ADMIN, pinned=True, source="pinned_today"
        )
    ).actions
    new = lottery_plan(
        Message(PEER, 20, text, AT, 9, pinned=True, source="pinned_today"), "admin", AT
    ).action
    record(
        "独立固定文本回复可规划",
        True,
        bool(old),
        new is not None and new.kind == "reply" and new.text == "+1",
    )


async def pagination() -> None:
    """真实 SDK 的默认降序与显式正序由端口替身准确模拟。"""
    rows = [
        SimpleNamespace(
            id=i,
            message="普通消息",
            raw_text="普通消息",
            date=AT,
            buttons=None,
            reply_to=None,
            sender_id=9,
            pinned=False,
            out=False,
        )
        for i in range(2, 603)
    ]

    class Client:
        """同一数据源同时支持新旧实际适配器。"""

        async def get_messages(self, peer, *, min_id, limit):
            """Telethon 默认返回最新消息优先。"""
            return list(reversed([m for m in rows if m.id > min_id]))[:limit]

        def iter_messages(self, peer, *, min_id, reverse, limit):
            """新适配器显式要求最早未处理消息优先。"""

            async def values():
                """按真实请求方向枚举同一个源。"""
                selected = [m for m in rows if m.id > min_id]
                if not reverse:
                    selected.reverse()
                for row in selected[:limit]:
                    yield row

            return values()

    old = TelegramSender.__new__(TelegramSender)
    old._client = Client()
    old._input_peers = {}
    new = Telegram.__new__(Telegram)
    new.client = Client()
    new.peers = {PEER: object()}
    old_rows = await old.get_messages_after(PEER, 1, 500)
    new_rows = await new.after(PEER, 1, 100)
    record("分页从最早未处理ID开始", 2, old_rows[0].message_id, new_rows[0].id)


def dedup(root: Path) -> None:
    """八日前不同消息的同内容不应永远阻止新一期。"""
    old = LotteryStore(root / "old-dedup.sqlite")
    entry = old.record_entry(PEER, 1, "same", None, "DETECTED")
    with closing(old._connect()) as db:
        db.execute(
            "UPDATE lottery_entry SET created_at=? WHERE id=?",
            ((AT - timedelta(days=8)).isoformat(), entry),
        )
        db.commit()
    old_result = old.is_duplicate(PEER - 1, 2, "same", None)
    new = Store(root / "new-dedup.sqlite")
    new.lottery(PEER, 1, "same", None, AT - timedelta(days=8), "MANUAL", "", "匿名记录")
    new_result = new.find_lottery(PEER - 1, 2, "same", None, AT) is not None
    record("跨群内容去重在七日后到期", False, old_result, new_result)


def main() -> None:
    """隔离数据并保存原始对比结果，失败不伪造通过。"""
    output = ROOT / ".hermes/rewrite/evidence"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="common-cases-", dir=output) as temporary:
        os.environ["LOCALAPPDATA"] = str(Path(temporary) / "appdata")
        config_roundtrip(Path(temporary))
        text_action()
        asyncio.run(pagination())
        dedup(Path(temporary))
    (output / "common-comparison.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
    assert all(row["new_pass"] for row in RESULTS)


if __name__ == "__main__":
    main()
