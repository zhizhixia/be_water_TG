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
        self._sent_texts: set[str] = set()  # 精确追踪 AI 自己发过的文本

    async def should_skip(self, group: str, context_count: int = 5) -> bool:
        """检查 AI 上一条消息是否仍在群聊最近消息中。
        
        如果是 → 说明无人回复，本轮跳过该群组。
        
        Args:
            group: 目标群组标识。
            context_count: 获取多少条最近消息来比较。
            
        Returns:
            True 如果需要跳过该群组。
        """
        own_history = self._memory.get(group)
        if not own_history:
            return False
        
        last_reply = own_history[-1]
        recent = await self._sender.get_recent_messages(group, limit=context_count)
        for msg in recent:
            if msg.text and msg.text.strip() == last_reply:
                logger.info("⏭ 上条消息仍在上下文 [%s]: %s", group, last_reply[:30])
                return True
        return False

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
        recent_messages = await self._sender.get_recent_messages(
            group, limit=context_count
        )

        # 2. 构建上下文
        context_lines: list[str] = []
        if recent_messages:
            for msg in reversed(recent_messages):
                text = msg.text
                if not text or not text.strip():
                    continue
                sender = msg.sender
                if sender is not None:
                    name = (getattr(sender, "username", None)
                            or getattr(sender, "first_name", None)
                            or f"id{sender.id}")
                else:
                    name = "群友"
                # 标注是否为 AI 之前的发言
                if text in self._sent_texts:
                    name += " (AI自己)"
                context_lines.append(f"[{name}]: {text}")

        # 3. 构建 messages
        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]

        if context_lines:
            messages.append({
                "role": "user",
                "content": "群聊记录（从早到晚）：\n" + "\n".join(context_lines)
                + "\n\n请根据以上对话，自然地回复一条消息。",
            })
        else:
            # 无上下文时，给出通用指令
            messages.append({
                "role": "user",
                "content": "请自然地发一条群聊消息。",
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
        self._sent_texts.add(reply)

        logger.info("🤖 AI 生成回复 [%s]: %s", group, reply[:30])
        return reply
