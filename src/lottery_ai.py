from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AIResult:
    is_lottery: bool
    confidence: float
    summary: str
    deadline: str | None
    required_actions: tuple[str, ...]
    reason: str


class LotteryAI(Protocol):
    async def analyze(self, payload: dict[str, object]) -> dict[str, object]: ...


async def analyze(ai: LotteryAI, payload: dict[str, object]) -> AIResult | None:
    """验证 AI 固定 schema；无效、低置信度结果均 fail closed。"""
    safe_payload = {key: payload[key] for key in ("text", "context", "buttons") if key in payload}
    try:
        raw: Any = await ai.analyze(safe_payload)
    except (TimeoutError, OSError, ValueError):
        return None
    required = {"is_lottery", "confidence", "summary", "deadline", "required_actions", "reason"}
    if not isinstance(raw, dict) or set(raw) != required:
        return None
    if (
        not isinstance(raw["is_lottery"], bool)
        or not isinstance(raw["confidence"], (int, float))
        or isinstance(raw["confidence"], bool)
        or not isinstance(raw["summary"], str)
        or raw["deadline"] is not None and not isinstance(raw["deadline"], str)
        or not isinstance(raw["required_actions"], list)
        or not all(isinstance(action, str) for action in raw["required_actions"])
        or not isinstance(raw["reason"], str)
        or float(raw["confidence"]) < 0.80
    ):
        return None
    return AIResult(raw["is_lottery"], float(raw["confidence"]), raw["summary"], raw["deadline"], tuple(raw["required_actions"]), raw["reason"] )
