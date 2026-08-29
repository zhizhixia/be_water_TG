import asyncio
import pytest
from src.lottery_queue import LotteryQueue


@pytest.mark.asyncio
async def test_single_worker_serializes_jobs_and_waits_only_between_lotteries():
    sleeps, events, random_calls = [], [], []

    def random_int(low, high):
        random_calls.append((low, high))
        return 37

    async def sleeper(value): sleeps.append(value)
    queue = LotteryQueue(random_int=random_int, sleeper=sleeper)
    async def one(): events.extend(["one-a", "one-b"])
    async def two(): events.append("two")
    await queue.put(one); await queue.put(two)
    await queue.run_once(); await queue.run_once()
    assert events == ["one-a", "one-b", "two"]
    assert random_calls == [(20, 60)]
    assert sleeps == [37]


@pytest.mark.asyncio
async def test_uses_injected_wait_bounds_between_distinct_lottery_entries():
    sleeps, random_calls = [], []

    def random_int(low, high):
        random_calls.append((low, high))
        return 8

    async def sleeper(value):
        sleeps.append(value)

    queue = LotteryQueue(random_int=random_int, sleeper=sleeper, wait_min_seconds=7, wait_max_seconds=9)
    await queue.put(lambda: completed())
    await queue.put(lambda: completed())

    await queue.run_once()
    await queue.run_once()

    assert random_calls == [(7, 9)]
    assert sleeps == [8]


@pytest.mark.parametrize(
    ("wait_min_seconds", "wait_max_seconds", "message"),
    [(0, 60, "等待下限必须为正值"), (20, 0, "等待上限必须为正值"), (61, 60, "等待下限不能大于等待上限")],
)
def test_rejects_invalid_wait_bounds(wait_min_seconds, wait_max_seconds, message):
    with pytest.raises(ValueError, match=message):
        LotteryQueue(wait_min_seconds=wait_min_seconds, wait_max_seconds=wait_max_seconds)


async def completed():
    """提供不会产生外部副作用的已完成任务。"""


@pytest.mark.asyncio
async def test_stop_cancels_not_yet_called_jobs():
    queue = LotteryQueue(); called = []
    async def job(): called.append(True)
    await queue.put(job); queue.stop()
    assert await queue.run_once() is False
    assert called == []
