"""公共测试 fixtures，消除各测试文件重复的 setup 代码。"""
from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.config import Settings
from src.runtime_paths import RuntimePaths
from src.runtime_state import ApplicationRuntimeState
from tests.fakes import FakeClock, FakeLotteryAI, FakeRandom, FakeSleeper, FakeTelegramSender


CONFIG_ENV_NAMES = (
    "API_ID",
    "API_HASH",
    "PHONE",
    "TARGET_GROUP",
    "TARGET_GROUPS",
    "MIN_INTERVAL",
    "MAX_INTERVAL",
    "PROXY_HOST",
    "PROXY_PORT",
    "PROXY_TYPE",
    "MESSAGE_FILES",
    "AI_ENABLED",
    "AI_API_KEY",
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_PROMPT",
    "AI_CONTEXT_COUNT",
    "AI_TEMPERATURE",
    "AI_MAX_TOKENS",
    "AI_TIMEOUT",
    "SCHEDULE_ENABLED",
    "SCHEDULE_MORNING_START",
    "SCHEDULE_MORNING_END",
    "SCHEDULE_AFTERNOON_START",
    "SCHEDULE_AFTERNOON_END",
    "ANTI_DETECT",
    "TYPING_DELAY_MIN",
    "TYPING_DELAY_MAX",
    "THINKING_DELAY_MIN",
    "THINKING_DELAY_MAX",
    "SKIP_ROUND_PCT",
    "GROUP_GAP_MIN",
    "GROUP_GAP_MAX",
    "DAILY_LIMIT",
    "IDLE_THRESHOLD_MINUTES",
    "QUESTION_REPLY_PCT",
    "DISCUSSION_REPLY_PCT",
    "REPLY_DELAY_MIN",
    "REPLY_DELAY_MAX",
    "STATE_DB_PATH",
    "LOTTERY_ENABLED",
    "LOTTERY_SCAN_INTERVAL_MINUTES",
    "DAILY_PINNED_LOTTERY_SCAN_ENABLED",
    "DAILY_CHECKIN_ENABLED",
    "LOTTERY_PER_GROUP_DAILY_LIMIT",
    "LOTTERY_ACCOUNT_DAILY_LIMIT",
    "LOTTERY_ACTION_GAP_MIN_SECONDS",
    "LOTTERY_ACTION_GAP_MAX_SECONDS",
)


@pytest.fixture(autouse=True)
def isolate_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """将每个测试的运行目录和配置环境隔离到独立临时目录。"""
    for name in CONFIG_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    local_appdata = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    return local_appdata


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有测试禁用 socket，避免 fake 漏洞连接到外部服务。"""
    def denied(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def runtime_paths(tmp_path) -> RuntimePaths:
    """为每个 Web 测试提供独立且不读取真实环境的集中运行路径。"""
    paths = RuntimePaths(tmp_path / "resources", tmp_path / "runtime")
    paths.ensure_directories()
    return paths


@pytest.fixture
def runtime_state() -> ApplicationRuntimeState:
    """提供已迁移且已配置的默认非敏感应用运行态。"""
    state = ApplicationRuntimeState()
    state.update(
        configured=True,
        migration_status="ready",
        setup_status="configured",
    )
    return state


@pytest.fixture
def app(runtime_paths, runtime_state):
    """通过真实 Flask 工厂创建每测试隔离的应用。"""
    from web_app import create_app

    flask_app = create_app(runtime_paths=runtime_paths, runtime_state=runtime_state)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def manager(app):
    """返回当前测试 Flask 实例独占的发送管理器。"""
    return app.extensions["send_loop_manager"]


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_sleeper() -> FakeSleeper:
    return FakeSleeper()


@pytest.fixture
def fake_random() -> FakeRandom:
    return FakeRandom()


@pytest.fixture
def fake_lottery_ai() -> FakeLotteryAI:
    return FakeLotteryAI()


@pytest.fixture
def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """在隔离环境中设置 load_settings 所需的非敏感测试配置。"""
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "a" * 32)
    monkeypatch.setenv("PHONE", "+8610000000000")
    monkeypatch.setenv("TARGET_GROUP", "https://t.me/test_group")
    monkeypatch.setenv("MIN_INTERVAL", "60")
    monkeypatch.setenv("MAX_INTERVAL", "180")
    monkeypatch.setenv("GROUP_GAP_MIN", "1")
    monkeypatch.setenv("GROUP_GAP_MAX", "1")


@pytest.fixture
def make_settings() -> Settings:
    """构造一个合法的 Settings 实例（最小必填字段齐全）。"""
    return Settings(
        api_id=12345,
        api_hash="a" * 32,
        phone="+8613800138000",
        target_groups=["https://t.me/test_group"],
    )


@pytest.fixture
def fake_sender() -> FakeTelegramSender:
    """mock TelegramSender：所有方法是 AsyncMock，默认 connect/disconnect/send_message 无副作用。"""
    sender = FakeTelegramSender()
    sender.connect = AsyncMock(return_value=None)
    sender.start = AsyncMock(return_value=None)
    sender.send_message = AsyncMock(return_value=None)
    sender.typing_indicator = AsyncMock(return_value=None)
    sender.disconnect = AsyncMock(return_value=None)
    sender.get_recent_messages = AsyncMock(return_value=[])
    return sender


@pytest.fixture
def stop_event() -> threading.Event:
    """send_loop 停止信号事件，测试可控触发停止。"""
    return threading.Event()


@pytest.fixture
def asyncio_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    """独立 asyncio 事件循环，测试 send_loop 协程用。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)
