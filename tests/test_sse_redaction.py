"""通用 SSE 事件脱敏回归测试。"""
from __future__ import annotations

import asyncio
import json
import logging

from web_manager import EventBus, LogQueueHandler


def test_emit_alert_redacts_sensitive_values_but_preserves_alert_context() -> None:
    """告警消息中的敏感文本不得广播，类型、群和状态码必须保留。"""
    bus = EventBus()
    queue = bus.subscribe()
    api_key = "api-key-should-not-reach-sse"
    token = "token-should-not-reach-sse"
    password = "password-should-not-reach-sse"
    callback = "https://callback.invalid/secret"
    phone = "+" + "86" + "138" + "1234" + "5678"

    asyncio.run(
        bus.emit_alert(
            "安全群-1001",
            "AUTH_FAILED",
            (
                f"认证失败 api_key={api_key}; token={token}; password={password}; "
                f"callback={callback}; phone={phone}; 请检查配置"
            ),
        )
    )

    event = queue.get(timeout=1)[1]
    payload = json.dumps(event, ensure_ascii=False)
    for sensitive_value in (api_key, token, password, callback, phone):
        assert sensitive_value not in payload
    assert event["type"] == "alert"
    assert event["data"]["group"] == "安全群-1001"
    assert event["data"]["code"] == "AUTH_FAILED"
    assert "认证失败" in event["data"]["message"]
    assert "请检查配置" in event["data"]["message"]


def test_emit_log_redacts_sensitive_values_but_preserves_safe_text() -> None:
    """普通日志消息脱敏后，级别和非敏感文本仍保持兼容。"""
    bus = EventBus()
    queue = bus.subscribe()
    token = "log-token-should-not-reach-sse"

    asyncio.run(bus.emit_log("warning", f"群-42 重试中 token={token}，稍后恢复"))

    event = queue.get(timeout=1)[1]
    assert token not in event["data"]["message"]
    assert event == {
        "type": "log",
        "data": {
            "level": "warning",
            "message": "群-42 重试中 token=[已脱敏]，稍后恢复",
        },
    }


def test_log_queue_handler_redacts_all_supported_sensitive_values() -> None:
    """日志处理器格式化后的 API key、密码、回调和手机号不得进入 SSE。"""
    bus = EventBus()
    queue = bus.subscribe()
    handler = LogQueueHandler(bus)
    test_logger = logging.getLogger("test_sse_redaction_handler")
    test_logger.handlers.clear()
    test_logger.propagate = False
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)

    api_key = "handler-api-key-secret"
    password = "handler-password-secret"
    callback = "https://handler-callback.invalid/private"
    phone = "+" + "86" + "139" + "1234" + "5678"
    test_logger.warning(
        "连接失败 api_key=%s password=%s callback=%s phone=%s，保留这段安全说明",
        api_key,
        password,
        callback,
        phone,
    )

    event = queue.get(timeout=1)[1]
    payload = json.dumps(event, ensure_ascii=False)
    for sensitive_value in (api_key, password, callback, phone):
        assert sensitive_value not in payload
    assert event["type"] == "log"
    assert event["data"]["level"] == "warning"
    assert "保留这段安全说明" in event["data"]["message"]


def test_publish_recursively_redacts_sensitive_fields_without_dropping_safe_fields() -> None:
    """通用发布边界递归剔除敏感字段，同时保留事件类型、状态和群标识。"""
    bus = EventBus()
    queue = bus.subscribe()

    bus._publish(
        {
            "type": "health",
            "data": {
                "state": "degraded",
                "group": "群-777",
                "token": "nested-token-secret",
                "details": [
                    {"callback_url": "https://nested.invalid/callback", "reason": "安全原因"},
                    {"phone": "+" + "86" + "137" + "1234" + "5678", "status": "retrying"},
                ],
            },
        }
    )

    event = queue.get(timeout=1)[1]
    assert event == {
        "type": "health",
        "data": {
            "state": "degraded",
            "group": "群-777",
            "details": [{"reason": "安全原因"}, {"status": "retrying"}],
        },
    }
