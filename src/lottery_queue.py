from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class LotteryQueue:
    """全账号单 worker 队列；停止会丢弃尚未进入 Telegram 调用的作业。"""
    def __init__(self, random_int: Callable[[int, int], int] | None = None,
                 sleeper: Callable[[float], Awaitable[object]] | None = None) -> None:
        self._queue: asyncio.Queue[Callable[[], Awaitable[None]]] = asyncio.Queue()
        self._random_int = random_int or __import__("random").randint
        self._sleeper = sleeper or asyncio.sleep
        self.stopped = False
        self._started = False
        self._worker_lock = asyncio.Lock()

    async def put(self, job: Callable[[], Awaitable[None]]) -> bool:
        """时段关闭或停止后拒绝新增任务。"""
        if self.stopped: return False
        await self._queue.put(job)
        return True

    async def run_once(self) -> bool:
        """串行执行一个作业，并仅在不同抽奖之间注入 20–60 秒等待。"""
        async with self._worker_lock:
            if self.stopped or self._queue.empty(): return False
            if self._started:
                await self._sleeper(self._random_int(20, 60))
                if self.stopped: return False
            job = await self._queue.get()
            if self.stopped: return False
            self._started = True
            await job()
            return True

    def stop(self) -> None:
        """取消未开始作业；当前调用结束后不会再开启下一动作。"""
        self.stopped = True
        while not self._queue.empty(): self._queue.get_nowait()
