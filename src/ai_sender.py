from __future__ import annotations

import asyncio
import logging
from collections import deque

from src.ai_client import AIClient
from src.sender import TelegramSender

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_SIZE = 5  # 短期记忆最多记 5 条 AI 自己说的话


class AISender:
    """AI 消息生成器：获取群聊上下文 + 调用 AIClient + 短期记忆管理。"""

    def __init__(self, telegram_sender: TelegramSender, ai_client: AIClient) -> None:
        self._sender = telegram_sender
        self._client = ai_client
        self._memory: dict[str, deque[str]] = {}  # group -> deque of own messages

    async def generate_message(
        self, group: str, prompt: str, context_count: int
    ) -> str:
        """根据群聊上下文和 AI 记忆生成一条回复。

        Args:
            group: 目标群组标识。
            prompt: 系统提示词。
            context_count: 获取最近多少条群聊消息作为上下文。

        Returns:
            AI 生成的回复文本。
        """
        # 1. 获取群聊上下文
        recent_messages = await self._sender._client.get_messages(
            group, limit=context_count
        )

        # 2. 构建上下文（最近在前 → 反转为最早在前）
        context_lines: list[str] = []
        for msg in reversed(recent_messages):
            text = msg.text
            if not text or not text.strip():
                continue
            # 优先用 username，其次 first_name，都没有用 "群友"
            sender = msg.sender
            if sender is not None:
                name = getattr(sender, "username", None) or getattr(sender, "first_name", None) or "群友"
            else:
                name = "群友"
            context_lines.append(f"{name}: {text}")

        # 3. 构建 messages
        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]

        # 添加群聊上下文
        if context_lines:
            messages.append({
                "role": "user",
                "content": "以下是最近的群聊记录：\n" + "\n".join(context_lines)
                + "\n\n请根据以上对话，自然地回复一条消息（不要重复别人说的话）。",
            })

        # 添加 AI 自己最近说的话（记忆）
        own_history = self._memory.get(group)
        if own_history:
            for own_msg in own_history:
                messages.append({"role": "assistant", "content": own_msg})

        # 4. 调用 AI（同步方法放到线程池）
        reply = await asyncio.to_thread(self._client.chat, messages)

        # 5. 存入记忆
        if group not in self._memory:
            self._memory[group] = deque(maxlen=DEFAULT_MEMORY_SIZE)
        self._memory[group].append(reply)

        logger.info("🤖 AI 生成回复 [%s]: %s", group, reply[:30])
        return reply
