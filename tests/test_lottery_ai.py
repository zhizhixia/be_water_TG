from __future__ import annotations

import asyncio

import pytest

from src.lottery_ai import analyze


class RecordingAI:
    """记录 AI 输入且不产生网络调用的替身。"""

    def __init__(self, result) -> None:
        self.result = result
        self.payload = None

    async def analyze(self, payload):
        self.payload = payload
        return self.result


@pytest.mark.asyncio
async def test_ai_only_accepts_complete_schema_and_never_sends_media() -> None:
    """AI 输入仅含允许字段，固定 schema 缺字段必须失败关闭。"""
    ai = RecordingAI({"is_lottery": True, "confidence": 0.8, "summary": "抽奖", "deadline": None, "required_actions": ["参与"], "reason": "x"})
    result = await analyze(ai, {"text": "抽奖", "context": "群上下文", "buttons": [], "media": "秘密二进制".encode()})
    assert result is not None and result.is_lottery
    assert ai.payload == {"text": "抽奖", "context": "群上下文", "buttons": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"is_lottery": True, "confidence": 0.9}, {"is_lottery": True, "confidence": 0.79, "summary": "", "deadline": None, "required_actions": [], "reason": "x"}])
async def test_ai_invalid_or_low_confidence_fails_closed(result) -> None:
    """AI 非法、缺 schema 或低阈值都不能授权自动动作。"""
    assert await analyze(RecordingAI(result), {"text": "抽奖"}) is None


@pytest.mark.asyncio
async def test_ai_timeout_fails_closed() -> None:
    """AI 超时或异常必须安全降级为无结果。"""
    class TimeoutAI:
        async def analyze(self, _payload):
            raise asyncio.TimeoutError()

    assert await analyze(TimeoutAI(), {"text": "抽奖"}) is None
