from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from telethon.errors import FloodWaitError, RPCError

from src.config import Settings
from src.interval import get_random_interval
from src.sender import TelegramSender
from ui.message_manager import MessageManager

if TYPE_CHECKING:
    from web_manager import EventBus
    from src.ai_sender import AISender

logger = logging.getLogger(__name__)

RETRY_DELAYS = [30, 60, 120]
MAX_RETRIES = 3
RETRY_FAIL_WAIT = 300
MAX_FLOOD_RETRIES = 5


@dataclass
class SendState:
    """发送循环的状态机。

    由外部控制器读写，send_loop 内部只读 stopped/paused 并写入计数器。
    """

    stopped: bool = False
    paused: bool = False
    total_count: int = 0
    per_group_counts: dict[str, int] = field(default_factory=dict)
    on_paused_callback: Callable[..., Any] | None = field(default=None)


async def send_loop(
    sender: TelegramSender,
    settings: Settings,
    state: SendState,
    message_manager: MessageManager,
    event_bus: EventBus | None = None,
    ai_sender: AISender | None = None,
) -> None:
    """多群组异步发送主循环。

    通过后台线程的 asyncio event loop 运行。
    支持定时窗口、AI 模式、反检测增强。

    控制语义：
    - stopped: 退出循环
    - paused: 完成当前轮后挂起
    """

    # 初始化每群组计数器
    for group in settings.target_groups:
        if group not in state.per_group_counts:
            state.per_group_counts[group] = 0

    paused_notified = False
    schedule_wait_count = 0

    while not state.stopped:
        # ---- 定时窗口检查 ----
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
                    logger.warning("已等待超 2 小时，自动暂停，请手动恢复")
                    if not state.paused:
                        state.paused = True
                    if state.on_paused_callback:
                        state.on_paused_callback()
                    schedule_wait_count = 0
                if not state.paused:
                    logger.info("不在允许时间段内，等待 60 秒后重检...")
                await asyncio.sleep(60)
                continue
            else:
                schedule_wait_count = 0

        # ---- 暂停检查 ----
        while state.paused and not state.stopped:
            if not paused_notified and state.on_paused_callback:
                state.on_paused_callback()
                paused_notified = True
            await asyncio.sleep(1)
        paused_notified = False

        if state.stopped:
            break

        # ---- 反检测：潜水回合 ----
        skip_round = (
            settings.anti_detect
            and random.randint(1, 100) <= settings.skip_round_pct
        )
        if skip_round:
            logger.info("本轮潜水，跳过")
            if event_bus:
                await event_bus.emit_countdown(0)
        else:
            # ---- 反检测：思考延迟 ----
            if not state.stopped and settings.anti_detect:
                think = random.randint(
                    settings.thinking_delay_min, settings.thinking_delay_max
                )
                logger.info("思考中 %d 秒...", think)
                await asyncio.sleep(think)

            # ---- 一轮：向每个群组发一条 ----
            for group in settings.target_groups:
                if state.stopped:
                    break

                if settings.ai_enabled and ai_sender is not None:
                    try:
                        if await ai_sender.should_skip(group, settings.ai_context_count):
                            continue
                    except Exception:
                        pass
                    try:
                        message = await ai_sender.generate_message(
                            group, settings.ai_prompt, settings.ai_context_count
                        )
                        if len(message) > 4000:
                            message = message[:4000]
                            logger.warning("AI 回复超长，已截断到 4000 字符")
                        if settings.anti_detect and random.random() < 0.3:
                            short_len = random.randint(5, 15)
                            message = message[:short_len]
                        if settings.anti_detect and random.random() < 0.3:
                            emojis = ["😄", "😂", "🤣", "😅", "👍", "🔥", "🎉", "💪"]
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

                # ---- 反检测：打字模拟 ----
                if settings.anti_detect:
                    typing_delay = random.randint(
                        settings.typing_delay_min, settings.typing_delay_max
                    )
                    await sender.typing_indicator(group)
                    await asyncio.sleep(typing_delay)

                # ---- 带重试的发送 ----
                sent = False
                flood_retries = 0
                for attempt in range(MAX_RETRIES):
                    try:
                        await sender.send_message(message, target_group=group)
                        state.per_group_counts[group] += 1
                        state.total_count += 1
                        logger.info(
                            "[%s] 已发送 (本组: %d, 总计: %d)",
                            group,
                            state.per_group_counts[group],
                            state.total_count,
                        )
                        sent = True
                        if event_bus:
                            await event_bus.emit_counter(state.total_count, state.per_group_counts)
                        break
                    except FloodWaitError as e:
                        flood_retries += 1
                        if flood_retries > MAX_FLOOD_RETRIES:
                            logger.error(
                                "[%s] FloodWait 超过 %d 次，放弃本轮发送",
                                group, MAX_FLOOD_RETRIES,
                            )
                            break
                        logger.warning(
                            "FloodWait %ds (第 %d/%d 次)，等待中...",
                            e.seconds, flood_retries, MAX_FLOOD_RETRIES,
                        )
                        await asyncio.sleep(e.seconds)
                    except (ConnectionError, OSError) as e:
                        if attempt < MAX_RETRIES - 1:
                            delay = RETRY_DELAYS[attempt]
                            logger.error(
                                "网络错误 [%s] (第 %d/%d 次): %s，%d 秒后重试",
                                group, attempt + 1, MAX_RETRIES, e, delay,
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                "网络错误 [%s] 重试 %d 次均失败: %s",
                                group, MAX_RETRIES, e,
                            )
                    except RPCError as e:
                        logger.error("Telegram RPC 错误 [%s]: %s", group, e)
                        break
                    except Exception:
                        logger.exception("未知错误 [%s]", group)
                        break

                if not sent and not state.stopped:
                    logger.error(
                        "[%s] 发送失败 (已达最大重试次数 %d)，等待 %d 秒后继续",
                        group, MAX_RETRIES, RETRY_FAIL_WAIT,
                    )
                    if event_bus:
                        await event_bus.emit_countdown(RETRY_FAIL_WAIT)
                    waited = 0
                    while waited < RETRY_FAIL_WAIT and not state.stopped:
                        await asyncio.sleep(1)
                        waited += 1
                        if event_bus:
                            await event_bus.emit_countdown(RETRY_FAIL_WAIT - waited)

                # 群组间随机间隔
                if not state.stopped:
                    gap = random.randint(
                        settings.group_gap_min, settings.group_gap_max
                    )
                    if gap > 0:
                        await asyncio.sleep(gap)

        # ---- 等待下一轮 ----
        if not state.stopped:
            interval = get_random_interval(settings.min_interval, settings.max_interval)
            logger.info("下一轮将在 %d 秒后开始...", interval)

            elapsed = 0
            while elapsed < interval and not state.stopped:
                await asyncio.sleep(1)
                elapsed += 1
                if event_bus:
                    await event_bus.emit_countdown(interval - elapsed)

    logger.info("发送循环已退出 (总计发送 %d)", state.total_count)
