import asyncio
import pytest
from src.lottery_queue import LotteryQueue


@pytest.mark.asyncio
async def test_single_worker_serializes_jobs_and_waits_only_between_lotteries():
    sleeps, events = [], []
    async def sleeper(value): sleeps.append(value)
    queue = LotteryQueue(random_int=lambda low, high: 37, sleeper=sleeper)
    async def one(): events.extend(["one-a", "one-b"])
    async def two(): events.append("two")
    await queue.put(one); await queue.put(two)
    await queue.run_once(); await queue.run_once()
    assert events == ["one-a", "one-b", "two"]
    assert sleeps == [37]

@pytest.mark.asyncio
async def test_stop_cancels_not_yet_called_jobs():
    queue = LotteryQueue(); called = []
    async def job(): called.append(True)
    await queue.put(job); queue.stop()
    assert await queue.run_once() is False
    assert called == []
