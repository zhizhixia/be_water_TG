from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import flet as ft
from telethon.errors import FloodWaitError, RPCError

from src.config import Settings
from src.interval import get_random_interval
from src.sender import TelegramSender
from ui.message_manager import MessageManager

logger = logging.getLogger(__name__)

RETRY_DELAYS = [30, 60, 120]
MAX_RETRIES = 3
RETRY_FAIL_WAIT = 300


@dataclass
class SendState:
    """发送循环的状态机。

    由外部控制器（如状态面板）读写，send_loop 内部仅读取
    stopped/paused 并写入计数器。
    """

    stopped: bool = False
    paused: bool = False
    total_count: int = 0
    per_group_counts: dict[str, int] = field(default_factory=dict)
    on_paused_callback: Callable[..., Any] | None = field(default=None)


async def send_loop(
    page: ft.Page,
    sender: TelegramSender,
    settings: Settings,
    state: SendState,
    message_manager: MessageManager,
) -> None:
    """多群组异步发送主循环。

    通过 page.run_task() 运行在 Flet 事件循环上。
    不支持直接调用 asyncio.run()。

    控制语义：
    - stopped: 退出循环（外部设置）
    - paused: 完成当前轮（所有群组）→ 在下一轮开始前挂起
    - 每群组发送失败不影响其他群组
    - 重试策略：3 次，退避 [30, 60, 120] 秒，全失败后等待 300 秒

    Args:
        page: Flet Page 实例（用于 page.update() 刷新 UI 计数器）。
        sender: 已启动的 Telegram 发送器实例。
        settings: 项目配置（包含 target_groups、间隔等）。
        state: 共享的发送状态（外部读写 stopped/paused，内部写入计数器）。
        message_manager: 每群组独立的消息选择器管理器。
    """

    # 初始化每群组计数器（保留已有值，避免重置）
    for group in settings.target_groups:
        if group not in state.per_group_counts:
            state.per_group_counts[group] = 0

    while not state.stopped:
        # ── 暂停检查：挂起直至恢复或停止 ──
        if state.paused and state.on_paused_callback:
            state.on_paused_callback()
        while state.paused and not state.stopped:
            await asyncio.sleep(1)

        if state.stopped:
            break

        # ── 一轮：向每个群组发送一条消息 ──
        for group in settings.target_groups:
            if state.stopped:
                break

            # 获取该群组的消息
            try:
                message = message_manager.get_message(group)
            except Exception:
                logger.exception("获取消息失败 [%s]", group)
                continue

            # ── 带重试的发送 ──
            sent = False
            for attempt in range(MAX_RETRIES):
                try:
                    await sender.send_message(message, target_group=group)
                    state.per_group_counts[group] += 1
                    state.total_count += 1
                    logger.info(
                        "✅ [%s] 已发送 (本组: %d, 总计: %d)",
                        group,
                        state.per_group_counts[group],
                        state.total_count,
                    )
                    sent = True
                    break
                except FloodWaitError as e:
                    logger.warning("⚠️ FloodWait %ds，等待中...", e.seconds)
                    await asyncio.sleep(e.seconds)
                    # 继续重试循环
                except (ConnectionError, OSError) as e:
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        logger.error(
                            "❌ 网络错误 [%s] (第 %d/%d 次): %s，%d 秒后重试",
                            group,
                            attempt + 1,
                            MAX_RETRIES,
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "❌ 网络错误 [%s] 重试 %d 次均失败: %s",
                            group,
                            MAX_RETRIES,
                            e,
                        )
                except RPCError as e:
                    logger.error("❌ Telegram RPC 错误 [%s]: %s", group, e)
                    break  # RPC 错误不重试
                except Exception:
                    logger.exception("❌ 未知错误 [%s]", group)
                    break  # 未知错误不重试

            if not sent and not state.stopped:
                logger.error(
                    "❌ [%s] 发送失败 (已达最大重试次数 %d)",
                    group,
                    MAX_RETRIES,
                )

            # 同轮内群组间短暂间隔
            if not state.stopped:
                await asyncio.sleep(1)

            # 刷新 UI 计数器（若 page 可用）
            if page is not None:
                try:
                    page.update()
                except Exception:
                    pass  # page 可能已销毁，忽略刷新失败

        # ── 等待下一轮 ──
        if not state.stopped:
            interval = get_random_interval(settings.min_interval, settings.max_interval)
            logger.info("⏳ 下一轮将在 %d 秒后开始...", interval)

            # 分段 sleep，使 stop 响应更灵敏（最慢 1 秒内响应）
            elapsed = 0
            while elapsed < interval and not state.stopped:
                await asyncio.sleep(1)
                elapsed += 1

    logger.info("发送循环已退出 (总计发送: %d)", state.total_count)
