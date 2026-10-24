"""线程、网页控制及升级文件的端到端匿名验证。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from bewater.app import create_app
from bewater.content import AI
from bewater.models import Message
from bewater.upgrade import prepare_upgrade
from bewater.engine import Engine
from conftest import FakeTelegram


def wait_until(predicate, seconds=5):
    """等待可观察条件，不按固定长 sleep 猜测后台完成。"""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("后台未在时限内到达预期状态")


def test_cli_chinese_output_survives_western_encoding():
    """真实入口在英文控制台编码下仍输出UTF-8，不因中文帮助或启动信息崩溃。"""
    environment = dict(os.environ, PYTHONIOENCODING="cp1252")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), "--help"],
        env=environment, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "Be Water TG 本机控制台" in result.stdout.decode("utf-8")


def test_actual_web_start_auth_pause_resume_stop(setup, paths):
    """真实 Flask→控制器线程→认证Future→Engine，只有外部端口使用替身。"""
    _, identity, _ = setup

    class Gateway(FakeTelegram):
        """模拟账号要求验证码，然后正常进入可停止的生产调度。"""

        def __init__(self, values, runtime_paths):
            """不会创建 TelegramClient。"""
            super().__init__(identity)

        async def login(self, challenge):
            """输入只交给当前挑战。"""
            assert await challenge("code") == "public-test-only"

        async def close(self):
            """外部连接关闭属于生命周期的一部分。"""
            self.calls.append(("closed",))

    app = create_app(paths, telegram_factory=Gateway)
    client = app.test_client()
    controller = app.extensions["controller"]
    csrf = client.get("/api/bootstrap").get_json()["csrf"]
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/api/control/start", json={}, headers=headers).status_code == 200
    try:
        assert client.post("/api/control/start", json={}, headers=headers).status_code == 409
        wait_until(lambda: controller.status()["challenge"] is not None)
        challenge_id = controller.status()["challenge"]["id"]
        assert (
            client.post(
                "/api/auth",
                json={"id": challenge_id, "answer": "public-test-only"},
                headers=headers,
            ).status_code
            == 200
        )
        wait_until(lambda: controller.status()["state"] == "running")
        assert client.post("/api/control/pause", json={}, headers=headers).status_code == 200
        assert controller.state == "paused"
        assert client.post("/api/control/resume", json={}, headers=headers).status_code == 200
        assert controller.state == "running"
        assert (
            client.post(
                "/api/cleanup", json={"before": "2026-10-18", "preview": True}, headers=headers
            ).status_code
            == 409
        )
    finally:
        if controller.thread:
            client.post("/api/control/stop", json={}, headers=headers)
            wait_until(lambda: controller.thread is None)
    assert controller.state == "stopped"
    assert (
        client.post(
            "/api/auth", json={"id": challenge_id, "answer": "stale"}, headers=headers
        ).status_code
        == 409
    )


def test_upgrade_copies_messages_preserving_csv_and_does_not_overwrite(paths, tmp_path):
    """旧消息内容完整保留，升级重复运行不会覆盖用户编辑。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source = legacy / "messages.txt"
    source.write_text("原消息一，原消息二", encoding="utf-8")
    report = prepare_upgrade(legacy, paths, False)
    assert report.status == "ready"
    target = paths.messages_dir / "messages.txt"
    assert target.read_text(encoding="utf-8") == "原消息一，原消息二"
    target.write_text("用户后来修改", encoding="utf-8")
    prepare_upgrade(legacy, paths, False)
    assert target.read_text(encoding="utf-8") == "用户后来修改"
    assert source.read_text(encoding="utf-8") == "原消息一，原消息二"


def test_isolated_launch_never_imports_source_credentials(paths, tmp_path):
    """隔离启动不得顺便复制真实来源的 dotenv/session。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".env").write_text("PHONE=PRIVATE\n", encoding="utf-8")
    (legacy / "sender_session.session").write_bytes(b"PRIVATE")
    assert prepare_upgrade(legacy, paths, True).status == "ready"
    assert not paths.env_path.exists()
    assert not paths.session.exists()


@pytest.mark.asyncio
async def test_revoking_ai_consent_prevents_next_request(setup, at):
    """关闭AI或撤销文本外发同意立即阻断新请求，不依赖重启快照。"""
    controller, identity, values = setup
    engine = Engine(controller, FakeTelegram(identity), values, clock=lambda: at)
    engine.ai = object()
    _, rev = controller.config.load()
    controller.config.update(
        {"ai_enabled": True, "ai_consent": True, "ai_api_key": "public-test-only"}, rev
    )
    assert engine.ai_allowed()
    _, rev = controller.config.load()
    controller.config.update({"ai_consent": False}, rev)
    assert not engine.ai_allowed()


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [True, 0.79, float("nan"), float("inf"), 1.1])
async def test_invalid_ai_lottery_schema_never_grants_permission(at, confidence):
    """布尔伪装数字、低置信与非有限值都失败关闭。"""
    ai = AI.__new__(AI)

    async def request(system, text):
        """模型响应是外部不可信输入。"""
        return json.dumps(
            {
                "is_lottery": True,
                "confidence": confidence,
                "summary": "测试",
                "deadline": None,
                "required_actions": ["参与"],
                "reason": "测试",
            }
        )

    ai.request = request
    message = Message(-1000000000123, 1, "测试抽奖", at, 9)
    assert await ai.lottery(message) is None
