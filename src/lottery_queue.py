from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol


class StopEvent(Protocol):
    """可由队列轮询的外部停止信号。"""

    def is_set(self) -> bool:
        """返回停止信号是否已设置。"""


class LotteryQueue:
    """全账号单 worker 队列；停止会丢弃尚未进入 Telegram 调用的作业。"""
    def __init__(self, random_int: Callable[[int, int], int] | None = None,
                 sleeper: Callable[[float], Awaitable[object]] | None = None,
                 wait_min_seconds: int = 20, wait_max_seconds: int = 60) -> None:
        if wait_min_seconds <= 0:
            raise ValueError("等待下限必须为正值")
        if wait_max_seconds <= 0:
            raise ValueError("等待上限必须为正值")
        if wait_min_seconds > wait_max_seconds:
            raise ValueError("等待下限不能大于等待上限")
        self._queue: asyncio.Queue[Callable[[], Awaitable[None]]] = asyncio.Queue()
        self._random_int = random_int or __import__("random").randint
        self._sleeper = sleeper or asyncio.sleep
        self._wait_min_seconds = wait_min_seconds
        self._wait_max_seconds = wait_max_seconds
        self.stopped = False
        self._started = False
        self._worker_lock = asyncio.Lock()

    async def put(self, job: Callable[[], Awaitable[None]]) -> bool:
        """时段关闭或停止后拒绝新增任务。"""
        if self.stopped: return False
        await self._queue.put(job)
        return True

    async def run_once(self, stop_event: StopEvent | None = None,
                       can_run: Callable[[], bool] | None = None) -> bool:
        """串行执行一个作业，并在外部停止信号触发时中断条目间等待。"""
        async with self._worker_lock:
            if self._is_stopped(stop_event) or self._queue.empty() or (can_run and not can_run()):
                return False
            if self._started:
                wait_seconds = self._random_int(self._wait_min_seconds, self._wait_max_seconds)
                if stop_event is None:
                    await self._sleeper(wait_seconds)
                else:
                    await self._wait_interruptibly(wait_seconds, stop_event)
                if self._is_stopped(stop_event) or (can_run and not can_run()): return False
            job = await self._queue.get()
            if self._is_stopped(stop_event):
                return False
            if can_run and not can_run():
                await self._queue.put(job)
                return False
            self._started = True
            await job()
            return True

    async def _wait_interruptibly(self, seconds: int, stop_event: StopEvent) -> None:
        """以短等待边界轮询外部停止信号，避免阻塞完整抽奖间隔。"""
        remaining = seconds
        while remaining > 0 and not self._is_stopped(stop_event):
            interval = min(0.1, remaining)
            await self._sleeper(interval)
            remaining -= interval

    def _is_stopped(self, stop_event: StopEvent | None) -> bool:
        """统一判断队列自身和外部停止信号。"""
        return self.stopped or (stop_event is not None and stop_event.is_set())

    def stop(self) -> None:
        """取消未开始作业；当前调用结束后不会再开启下一动作。"""
        self.stopped = True
        while not self._queue.empty(): self._queue.get_nowait()
