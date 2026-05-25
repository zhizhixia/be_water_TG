from __future__ import annotations

import logging

import openai

logger = logging.getLogger(__name__)


class AIClient:
    """DeepSeek LLM 客户端封装，使用 openai SDK 连接 DeepSeek API。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.7,
        max_tokens: int = 500,
        timeout: float = 30.0,
    ) -> None:
        """初始化 AI 客户端。

        Args:
            api_key: DeepSeek API 密钥。
            base_url: API 基础 URL。
            model: 模型名称。
            temperature: 生成温度 (0-2)，越高越随机。
            max_tokens: 最大生成 token 数。
            timeout: 请求超时时间（秒）。
        """
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def chat(self, messages: list[dict]) -> str:
        """发送对话消息并返回模型回复。

        Args:
            messages: 消息列表，格式为
                [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

        Returns:
            模型返回的文本内容。

        Raises:
            openai.OpenAIError: API 调用失败时抛出。
        """
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            return response.choices[0].message.content or ""
        except openai.OpenAIError:
            logger.exception("DeepSeek API 调用失败")
            raise
