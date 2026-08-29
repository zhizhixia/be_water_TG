from __future__ import annotations

import json

import pytest

from src.ai_sender import AISender


class RecordingClient:
    """记录模型请求并返回预设响应。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[dict[str, str]] | None = None

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return self.reply


@pytest.mark.asyncio
async def test_analyze_only_sends_lottery_text_context_and_buttons_to_ai() -> None:
    """抽奖识别请求绝不将媒体数据交给模型。"""
    expected = {
        "is_lottery": True,
        "confidence": 0.95,
        "summary": "限时抽奖",
        "deadline": "2026-09-01T12:00:00+08:00",
        "required_actions": ["点击参与"],
        "reason": "消息明确说明抽奖规则",
    }
    client = RecordingClient(json.dumps(expected, ensure_ascii=False))
    sender = AISender(telegram_sender=None, ai_client=client)  # type: ignore[arg-type]
    media = "绝不发送的媒体内容"

    result = await sender.analyze({
        "text": "点击按钮参与抽奖",
        "context": "群聊上下文",
        "buttons": [{"text": "参与"}],
        "media": media,
    })

    assert result == expected
    assert client.messages is not None
    assert json.loads(client.messages[1]["content"]) == {
        "text": "点击按钮参与抽奖",
        "context": "群聊上下文",
        "buttons": [{"text": "参与"}],
    }
    assert media not in json.dumps(client.messages, ensure_ascii=False)
    assert all("media" not in message["content"] for message in client.messages)
    assert "is_lottery" in client.messages[0]["content"]
    assert "confidence" in client.messages[0]["content"]
    assert "summary" in client.messages[0]["content"]
    assert "deadline" in client.messages[0]["content"]
    assert "required_actions" in client.messages[0]["content"]
    assert "reason" in client.messages[0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "```json\n{}\n```",
    "not json",
    "[]",
    '{"is_lottery": false}',
    '{"is_lottery": false, "confidence": 0, "summary": "", "deadline": null, "required_actions": [], "reason": "", "publisher_trust": "trusted"}',
])
async def test_analyze_rejects_non_object_or_invalid_json_reply(reply: str) -> None:
    """代码围栏、无效 JSON 与非固定对象都必须失败关闭。"""
    sender = AISender(telegram_sender=None, ai_client=RecordingClient(reply))  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await sender.analyze({"text": "抽奖"})


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("网络失败"), TimeoutError("超时")])
async def test_analyze_propagates_fail_closed_client_errors(error: Exception) -> None:
    """可降级的客户端故障必须以允许的异常类型上抛。"""
    class FailingClient:
        """模拟同步客户端调用失败。"""

        def chat(self, _messages: list[dict[str, str]]) -> str:
            raise error

    sender = AISender(telegram_sender=None, ai_client=FailingClient())  # type: ignore[arg-type]

    with pytest.raises(type(error)):
        await sender.analyze({"text": "抽奖"})


@pytest.mark.asyncio
async def test_analyze_wraps_unexpected_client_error_as_oserror() -> None:
    """未知客户端异常也转换为 lottery_ai 可安全降级的错误。"""
    class FailingClient:
        """模拟非预期同步客户端错误。"""

        def chat(self, _messages: list[dict[str, str]]) -> str:
            raise RuntimeError("异常")

    sender = AISender(telegram_sender=None, ai_client=FailingClient())  # type: ignore[arg-type]

    with pytest.raises(OSError):
        await sender.analyze({"text": "抽奖"})
