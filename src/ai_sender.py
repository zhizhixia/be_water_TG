from __future__ import annotations

import asyncio
import json
import logging
import re
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
                + "\n\n请根据以上对话，自然地回复一条消息。"
                + "如果没有相关且有价值的内容可补充，只回复 [SKIP]。",
            })
        else:
            # 无上下文时，给出通用指令
            messages.append({
                "role": "user",
                "content": "请结合群聊主题自然地发一条消息；若无法确定相关内容，只回复 [SKIP]。",
            })

        # 添加 AI 自己最近说的话（记忆）
        own_history = self._memory.get(group)
        if own_history:
            for own_msg in own_history:
                messages.append({"role": "assistant", "content": own_msg})

        # 4. 调用 AI（同步方法放到线程池）
        reply = await asyncio.to_thread(self._client.chat, messages)

        logger.info("🤖 AI 生成候选 [%s]: %s", group, reply[:30])
        return reply

    async def classify_message(
        self,
        group: str,
        text: str,
        context_count: int = 5,
    ) -> str:
        """Semantically classify a new group message for participation policy."""
        del group, context_count
        messages = [
            {
                "role": "system",
                "content": (
                    "你是群聊参与分类器。只输出 QUESTION、DISCUSSION 或 IRRELEVANT。"
                    "QUESTION 表示对群友提出问题、求助或请求解决；"
                    "DISCUSSION 表示有实质内容的普通讨论；"
                    "IRRELEVANT 表示寒暄、表情、广告、链接或无需参与的低信息内容。"
                ),
            },
            {"role": "user", "content": text.strip()},
        ]
        reply = await asyncio.to_thread(self._client.chat, messages)
        labels = re.findall(r"\b(QUESTION|DISCUSSION|IRRELEVANT)\b", reply.upper())
        if len(set(labels)) != 1:
            raise ValueError(f"Unexpected classification response: {reply!r}")
        return labels[0].casefold()

    async def analyze(self, payload: dict[str, object]) -> dict[str, object]:
        """调用 AI 识别抽奖，并仅返回固定的结构化结果。"""
        safe_payload = {
            key: payload[key]
            for key in ("text", "context", "buttons")
            if key in payload
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是抽奖识别器。仅输出一个 JSON 对象，不要 Markdown、代码围栏或额外文本。"
                    "对象必须且只能包含以下字段："
                    "is_lottery（布尔值）、confidence（0 到 1 的数字）、summary（字符串）、"
                    "deadline（字符串或 null）、required_actions（字符串数组）、reason（字符串）。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(safe_payload, ensure_ascii=False),
            },
        ]
        try:
            reply = await asyncio.to_thread(self._client.chat, messages)
        except (OSError, TimeoutError):
            raise
        except Exception as exc:
            raise OSError("抽奖 AI 调用失败") from exc
        try:
            result = json.loads(reply)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("抽奖 AI 返回的不是有效 JSON") from exc
        required = {
            "is_lottery",
            "confidence",
            "summary",
            "deadline",
            "required_actions",
            "reason",
        }
        if not isinstance(result, dict) or set(result) != required:
            raise ValueError("抽奖 AI 返回未遵循固定 schema")
        return result

    def commit_sent(self, group: str, text: str) -> None:
        """Commit AI memory after the external send is durably confirmed."""
        if group not in self._memory:
            self._memory[group] = deque(maxlen=DEFAULT_MEMORY_SIZE)
        self._memory[group].append(text)
        self._sent_texts.add(text)
        logger.info("🤖 已提交发送历史 [%s]: %s", group, text[:30])
