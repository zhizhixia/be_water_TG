from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.ai_client import AIClient
from src.config import Settings


class TestAIClient:
    """AIClient 类测试。"""

    @patch("src.ai_client.openai.OpenAI")
    def test_chat_returns_content(self, mock_openai_class: MagicMock) -> None:
        """chat() 正常返回模型回复内容。"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # 构造 mock 响应：choices[0].message.content = "你好！"
        mock_choice = MagicMock()
        mock_choice.message.content = "你好！"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        client = AIClient(api_key="sk-test")
        result = client.chat([
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ])

        assert result == "你好！"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.ai_client.openai.OpenAI")
    def test_chat_raises_on_api_error(self, mock_openai_class: MagicMock) -> None:
        """API 异常时正确向上传播错误。"""
        import openai

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = openai.APIError(
            message="模拟 API 错误",
            request=MagicMock(),
            body=None,
        )

        client = AIClient(api_key="sk-test")
        with pytest.raises(openai.OpenAIError):
            client.chat([{"role": "user", "content": "test"}])

    @patch("src.ai_client.openai.OpenAI")
    def test_empty_content_returns_empty_string(self, mock_openai_class: MagicMock) -> None:
        """content 为 None 时返回空字符串（不返回 None）。"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        client = AIClient(api_key="sk-test")
        result = client.chat([{"role": "user", "content": "test"}])

        assert result == ""

    @patch("src.ai_client.openai.OpenAI")
    def test_chat_passes_custom_parameters(self, mock_openai_class: MagicMock) -> None:
        """chat() 将 model/temperature/max_tokens 正确传递给 API。"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        client = AIClient(
            api_key="sk-test",
            model="deepseek-chat",
            temperature=0.9,
            max_tokens=256,
        )
        client.chat([{"role": "user", "content": "hello"}])

        mock_client.chat.completions.create.assert_called_once_with(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.9,
            max_tokens=256,
        )

    @patch("src.ai_client.openai.OpenAI")
    def test_timeout_passed_to_client(self, mock_openai_class: MagicMock) -> None:
        """timeout 参数正确传递给 openai.OpenAI 构造函数。"""
        AIClient(api_key="sk-test", timeout=15.0)
        mock_openai_class.assert_called_once()
        _, kwargs = mock_openai_class.call_args
        assert kwargs["timeout"] == 15.0


class TestSettingsAI:
    """Settings 中 AI 字段默认值测试。"""

    def test_ai_fields_default_values(self) -> None:
        """AI 字段默认值不破坏向后兼容。"""
        s = Settings(api_id=1, api_hash="x", phone="+86")
        assert s.ai_enabled is False
        assert s.ai_api_key == ""
        assert s.ai_base_url == "https://api.deepseek.com/v1"
        assert s.ai_model == "deepseek-chat"
        assert s.ai_prompt == ""
        assert s.ai_context_count == 5

    def test_ai_fields_custom_values(self) -> None:
        """AI 字段可设置自定义值。"""
        s = Settings(
            api_id=1,
            api_hash="x",
            phone="+86",
            ai_enabled=True,
            ai_api_key="sk-123",
            ai_base_url="https://custom.api/v1",
            ai_model="custom-model",
            ai_prompt="你是一个客服",
            ai_context_count=10,
        )
        assert s.ai_enabled is True
        assert s.ai_api_key == "sk-123"
        assert s.ai_base_url == "https://custom.api/v1"
        assert s.ai_model == "custom-model"
        assert s.ai_prompt == "你是一个客服"
        assert s.ai_context_count == 10
