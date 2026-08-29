from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class FakeClock:
    """可推进的上海时钟，`now` 调用记录可由测试观察。"""

    def __init__(self, value: datetime | None = None) -> None:
        self.tz = ZoneInfo("Asia/Shanghai")
        self.value = value or datetime(2026, 8, 29, 23, 59, tzinfo=self.tz)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class FakeSleeper:
    """不等待的 sleeper，记录每次请求的秒数。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeRandom:
    """可预测随机源，记录 randint 的边界。"""

    def __init__(self, value: int = 0) -> None:
        self.value = value
        self.calls: list[tuple[int, int]] = []

    def randint(self, lower: int, upper: int) -> int:
        self.calls.append((lower, upper))
        return min(max(self.value, lower), upper)


class FakeTelegramSender:
    """受控 Telegram fake，只记录调用，永不构造真实客户端。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def send_group_text(self, peer_id: int, text: str) -> int:
        self.calls.append(("send_group_text", peer_id, text))
        return len(self.calls)

    async def click_callback(self, peer_id: int, message_id: int, row: int, column: int) -> str:
        self.calls.append(("click_callback", peer_id, message_id, row, column))
        return "ok"


class FakeLotteryAI:
    """抽奖 AI fake，返回预设结构化结果并记录输入。"""

    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result or {"is_lottery": False, "confidence": 0.0}
        self.calls: list[dict[str, object]] = []

    async def analyze(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return self.result
