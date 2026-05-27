from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

import flet as ft
from telethon.errors import FloodWaitError, RPCError

from src.config import Settings
from src.interval import get_random_interval
from src.sender import TelegramSender
from ui.message_manager import MessageManager

if TYPE_CHECKING:
    from ui.status_panel import StatusPanel
    from src.ai_sender import AISender

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
    status_panel: "StatusPanel | None" = None,
    ai_sender: "AISender | None" = None,
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
        status_panel: 可选的 StatusPanel 实例，用于更新计数器和倒计时 UI。
    """

    # 初始化每群组计数器（保留已有值，避免重置）
    for group in settings.target_groups:
        if group not in state.per_group_counts:
            state.per_group_counts[group] = 0

    paused_notified = False
    schedule_wait_count = 0
    while not state.stopped:
        # ── 定时窗口检查 ──
        if settings.schedule_enabled and not state.stopped:
            now = datetime.now().time()
            in_morning = (
                time.fromisoformat(settings.schedule_morning_start)
                <= now
                <= time.fromisoformat(settings.schedule_morning_end)
            )
            in_afternoon = (
                time.fromisoformat(settings.schedule_afternoon_start)
                <= now
                <= time.fromisoformat(settings.schedule_afternoon_end)
            )
            if not (in_morning or in_afternoon):
                schedule_wait_count += 1
                if schedule_wait_count > 120:
                    logger.warning("⏰ 已等待超 2 小时，自动暂停，请手动恢复")
                    if not state.paused:
                        state.paused = True
                    if state.on_paused_callback:
                        state.on_paused_callback()
                    schedule_wait_count = 0
                if not state.paused:
                    logger.info("⏰ 不在允许时间段内，等待 60 秒后重检...")
                await asyncio.sleep(60)
                continue
            else:
                schedule_wait_count = 0  # 进入窗口时重置

        # ── 暂停检查：挂起直至恢复或停止 ──
        while state.paused and not state.stopped:
            if not paused_notified and state.on_paused_callback:
                state.on_paused_callback()
                paused_notified = True
            await asyncio.sleep(1)
        paused_notified = False

        if state.stopped:
            break

        # ── 一轮：向每个群组发送一条消息 ──
        for group in settings.target_groups:
            if state.stopped:
                break

            # 获取该群组的消息
            if settings.ai_enabled and ai_sender is not None:
                try:
                    message = await ai_sender.generate_message(
                        group, settings.ai_prompt, settings.ai_context_count
                    )
                    if len(message) > 4000:
                        message = message[:4000]
                        logger.warning("AI 回复超长，已截断到 4000 字符")
                    # AI 回复 emoji 尾巴
                    if settings.anti_detect and random.random() < 0.3:
                        emojis = ["😄", "👍", "😊", "😂", "💪", "🔥", "🎉", "🙏"]
                        count = random.randint(1, 2)
                        tail = "".join(random.choice(emojis) for _ in range(count))
                        message = message.rstrip() + tail
                except Exception:
                    logger.exception("AI 生成失败 [%s]，回退到 TXT", group)
                    try:
                        message = message_manager.get_message(group)
                    except Exception:
                        logger.exception("获取消息失败 [%s]", group)
                        continue
            else:
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
                    if status_panel:
                        try:
                            status_panel.update_counter(state.total_count, state.per_group_counts)
                        except Exception:
                            pass
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

            # 群组间随机间隔
            if not state.stopped:
                if settings.anti_detect:
                    gap = random.randint(2, 5)
                else:
                    gap = 1
                await asyncio.sleep(gap)

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
                if status_panel:
                    try:
                        status_panel.update_countdown(interval - elapsed)
                    except Exception:
                        pass

    logger.info("发送循环已退出 (总计发送: %d)", state.total_count)
