from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import TYPE_CHECKING, Any

from telethon.errors import FloodWaitError, RPCError

from src.config import Settings
from src.interval import get_random_interval
from src.sender import TelegramSender
from ui.message_manager import MessageManager

if TYPE_CHECKING:
    from web_manager import EventBus, SendLoopManager
    from src.ai_sender import AISender

logger = logging.getLogger(__name__)

RETRY_DELAYS = [30, 60, 120]
MAX_RETRIES = 3
RETRY_FAIL_WAIT = 300
MAX_FLOOD_RETRIES = 5


class SendState(Enum):
    """发送循环状态机枚举。

    状态转移白名单见 web_manager.SendLoopManager.transition。
    """

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    WAITING_CODE = "waiting_code"


@dataclass
class SendRuntime:
    """发送循环运行时状态：由 SendLoopManager 持有，send_loop 读写。

    与 SendState 枚举分离：枚举描述状态机阶段，本类承载计数等运行数据。
    所有字段读写应在 manager._state_lock 下进行。
    """
    stopped: bool = False
    paused: bool = False
    total_count: int = 0
    per_group_counts: dict[str, int] = field(default_factory=dict)
    on_paused_callback: Callable[..., Any] | None = field(default=None)


async def send_loop(
    sender: TelegramSender,
    settings: Settings,
    manager: "SendLoopManager",
    message_manager: MessageManager,
    event_bus: EventBus | None = None,
    ai_sender: AISender | None = None,
) -> None:
    """多群组异步发送主循环。

    通过后台线程的 asyncio event loop 运行。
    停止信号由 manager.stop_event 提供，可在 FloodWait 等待期间被中断。
    """
    stop_event = manager.stop_event
    runtime = SendRuntime()
    # 兼容旧变量名引用，便于最小改动函数体内部
    state = runtime

    # 初始化每群组计数器
    for group in settings.target_groups:
        if group not in runtime.per_group_counts:
            runtime.per_group_counts[group] = 0

    paused_notified = False
    schedule_wait_count = 0

    while not stop_event.is_set():
        # ---- 定时窗口检查 ----
        if settings.schedule_enabled and not stop_event.is_set():
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
                    if not runtime.paused:
                        runtime.paused = True
                    if runtime.on_paused_callback:
                        runtime.on_paused_callback()
                    schedule_wait_count = 0
                if not runtime.paused:
                    logger.info("不在允许时间段内，等待 60 秒后重检...")
                await asyncio.sleep(60)
                continue
            else:
                schedule_wait_count = 0

        # ---- 暂停检查 ----
        while runtime.paused and not stop_event.is_set():
            if not paused_notified and runtime.on_paused_callback:
                runtime.on_paused_callback()
                paused_notified = True
            await asyncio.sleep(1)
        paused_notified = False

        if stop_event.is_set():
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
            if not stop_event.is_set() and settings.anti_detect:
                think = random.randint(
                    settings.thinking_delay_min, settings.thinking_delay_max
                )
                logger.info("思考中 %d 秒...", think)
                await asyncio.sleep(think)

            # ---- 一轮：向每个群组发一条 ----
            for group in settings.target_groups:
                if stop_event.is_set():
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
                        new_total, new_per = manager.increment_count(group)
                        logger.info(
                            "[%s] 已发送 (本组: %d, 总计: %d)",
                            group, new_per, new_total,
                        )
                        sent = True
                        if event_bus:
                            snap_total, snap_per = manager.runtime_counts_snapshot()
                            await event_bus.emit_counter(snap_total, snap_per)
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
                        # 可中断等待：每 0.5s 探测 stop_event，置位则立即跳出
                        remaining = e.seconds
                        while remaining > 0 and not stop_event.is_set():
                            await asyncio.sleep(min(0.5, remaining))
                            remaining -= 0.5
                        if stop_event.is_set():
                            logger.info("FloodWait 等待期间收到停止信号，退出")
                            break
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

                if not sent and not stop_event.is_set():
                    logger.error(
                        "[%s] 发送失败 (已达最大重试次数 %d)，等待 %d 秒后继续",
                        group, MAX_RETRIES, RETRY_FAIL_WAIT,
                    )
                    if event_bus:
                        await event_bus.emit_countdown(RETRY_FAIL_WAIT)
                    waited = 0
                    while waited < RETRY_FAIL_WAIT and not stop_event.is_set():
                        await asyncio.sleep(1)
                        waited += 1
                        if event_bus:
                            await event_bus.emit_countdown(RETRY_FAIL_WAIT - waited)

                # 群组间随机间隔
                if not stop_event.is_set():
                    gap = random.randint(
                        settings.group_gap_min, settings.group_gap_max
                    )
                    if gap > 0:
                        await asyncio.sleep(gap)

        # ---- 等待下一轮 ----
        if not stop_event.is_set():
            interval = get_random_interval(settings.min_interval, settings.max_interval)
            logger.info("下一轮将在 %d 秒后开始...", interval)

            elapsed = 0
            while elapsed < interval and not stop_event.is_set():
                await asyncio.sleep(1)
                elapsed += 1
                if event_bus:
                    await event_bus.emit_countdown(interval - elapsed)

    logger.info("发送循环已退出 (总计发送 %d)", runtime.total_count)
