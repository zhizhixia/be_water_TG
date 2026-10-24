import asyncio
import threading

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
async def test_run_once_observes_pre_set_stop_event():
    """外部停止信号已设置时，run_once 不会启动排队作业。"""
    stop_event = threading.Event()
    stop_event.set()
    queue = LotteryQueue()
    called = []

    async def job():
        called.append(True)

    await queue.put(job)

    assert await queue.run_once(stop_event=stop_event) is False
    assert called == []


@pytest.mark.asyncio
async def test_stop_event_during_inter_lottery_wait_returns_before_next_job():
    """条目间等待检测到停止后，在一个短等待边界内结束且不执行下一作业。"""
    stop_event = threading.Event()
    sleeps, called = [], []

    async def sleeper(seconds):
        sleeps.append(seconds)
        stop_event.set()

    queue = LotteryQueue(random_int=lambda _low, _high: 20, sleeper=sleeper)

    async def first_job():
        called.append("first")

    async def second_job():
        called.append("second")

    await queue.put(first_job)
    await queue.put(second_job)

    assert await queue.run_once(stop_event=stop_event) is True
    assert await queue.run_once(stop_event=stop_event) is False
    assert called == ["first"]
    assert sleeps and sleeps[0] < 20


@pytest.mark.asyncio
async def test_stop_rejects_new_jobs_and_clears_unstarted_jobs():
    """停止会清空未开始作业，并拒绝之后的新增作业。"""
    queue = LotteryQueue()
    called = []

    async def queued_job():
        called.append("queued")

    async def new_job():
        called.append("new")

    assert await queue.put(queued_job) is True
    queue.stop()

    assert await queue.put(new_job) is False
    assert await queue.run_once() is False
    assert called == []


@pytest.mark.asyncio
async def test_pause_gate_keeps_unstarted_job_queued_until_resume():
    """暂停或非时段不执行未开始任务，恢复后仍可逐个处理。"""
    queue = LotteryQueue()
    called = []

    async def job():
        called.append(True)

    await queue.put(job)
    assert await queue.run_once(can_run=lambda: False) is False
    assert called == []
    assert await queue.run_once(can_run=lambda: True) is True
    assert called == [True]
