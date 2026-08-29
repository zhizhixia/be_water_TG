"""任务 14--16 的抽奖/签到端到端验收契约。

本模块只使用 fake/mock，并约束一层小型可注入的应用服务接口；它不连接
Telegram、AI 或真实网络，也不依赖 Telethon 的内部对象。
"""
from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest


@dataclass
class FakePorts:
    """记录集成层对外部端口的调用，供顺序和安全边界断言。"""

    events: list[str] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    normal_groups: list[int] = field(default_factory=list)
    state_store_peer_ids: list[int] = field(default_factory=list)
    observer_peer_ids: list[int] = field(default_factory=list)
    frozen_groups: set[int] = field(default_factory=set)
    checkin_results: dict[int, bool] = field(default_factory=dict)
    planned_status: str = "AUTO_PLANNED"

    async def acquire_instance_lock(self) -> None:
        self.events.append("lock")

    async def resolve_and_migrate_identities(self) -> list[int]:
        self.events.append("identity_migration")
        return [101, 202]

    async def recover(self) -> None:
        self.events.append("recover")

    async def state_store_for_peer(self, peer_id: int) -> None:
        self.events.append(f"state:{peer_id}")
        self.state_store_peer_ids.append(peer_id)

    async def observe_peer(self, peer_id: int) -> None:
        self.events.append(f"observer:{peer_id}")
        self.observer_peer_ids.append(peer_id)

    async def ensure_daily_batch(self, peer_ids: list[int]) -> list[int]:
        assert peer_ids == [101, 202]
        self.events.append("daily_batch")
        return peer_ids

    async def checkin(self, peer_id: int) -> bool:
        self.events.append(f"checkin:{peer_id}")
        return self.checkin_results.get(peer_id, True)

    async def scan_pinned(self, peer_id: int) -> list[dict[str, Any]]:
        self.events.append(f"pinned:{peer_id}")
        return [{"peer_id": peer_id, "message_id": peer_id + 1}]

    async def scan_incremental(self, peer_id: int) -> list[dict[str, Any]]:
        self.events.append(f"incremental:{peer_id}")
        return [{"peer_id": peer_id, "message_id": peer_id + 2}]

    async def detect(self, finding: dict[str, Any]) -> dict[str, Any]:
        self.events.append(f"detect:{finding['peer_id']}")
        return finding

    async def analyze(self, finding: dict[str, Any]) -> dict[str, Any]:
        self.events.append(f"ai:{finding['peer_id']}")
        return finding

    async def plan(self, finding: dict[str, Any]) -> dict[str, Any]:
        self.events.append(f"plan:{finding['peer_id']}")
        return {**finding, "status": self.planned_status}

    async def reserve_quota(self, plan: dict[str, Any]) -> bool:
        self.events.append(f"quota:{plan['peer_id']}")
        return plan["status"] == "AUTO_PLANNED"

    async def enqueue(self, plan: dict[str, Any]) -> None:
        self.events.append(f"enqueue:{plan['peer_id']}")
        self.plans.append(plan)

    async def execute(self, plan: dict[str, Any]) -> None:
        self.events.append(f"execute:{plan['peer_id']}")

    async def participate_normally(self, peer_id: int) -> None:
        self.events.append(f"normal:{peer_id}")
        self.normal_groups.append(peer_id)

    async def alert(self, code: str, **data: Any) -> None:
        self.alerts.append({"code": code, **data})


@pytest.fixture
def integration_module() -> Any:
    """延迟导入待实现的集成边界，保证每条契约可独立报告 RED 原因。"""
    return importlib.import_module("src.lottery_integration")


@pytest.mark.asyncio
async def test_startup_runs_the_safe_pipeline_in_required_order(integration_module: Any) -> None:
    """锁、身份迁移、恢复、每日任务和扫描必须先于普通参与。"""
    ports = FakePorts()
    pipeline = integration_module.LotteryIntegrationPipeline(ports=ports, lottery_enabled=True)

    await pipeline.run_startup_cycle()

    assert ports.events[:4] == ["lock", "identity_migration", "recover", "daily_batch"]
    assert ports.events.index("checkin:101") < ports.events.index("pinned:101")
    assert ports.events.index("pinned:202") < ports.events.index("incremental:101")
    assert ports.events.index("incremental:202") < ports.events.index("normal:101")
    assert ports.events[-2:] == ["normal:101", "normal:202"]
    assert ports.state_store_peer_ids == [101, 202]
    assert ports.observer_peer_ids == [101, 202]
    assert all(isinstance(peer_id, int) for peer_id in [*ports.state_store_peer_ids, *ports.observer_peer_ids, *ports.normal_groups])
    assert all("https://" not in event and "@" not in event for event in ports.events)


@pytest.mark.asyncio
async def test_failed_checkin_keeps_lottery_running_but_blocks_normal_participation(integration_module: Any) -> None:
    """签到失败当日只阻塞该群普通参与，不能阻塞抽奖扫描或入队。"""
    ports = FakePorts(checkin_results={101: False})
    pipeline = integration_module.LotteryIntegrationPipeline(ports=ports, lottery_enabled=True)

    await pipeline.run_startup_cycle()

    assert any(plan["peer_id"] == 101 for plan in ports.plans)
    assert ports.normal_groups == [202]
    assert {item["code"] for item in ports.alerts} >= {"checkin_failed"}


@pytest.mark.asyncio
async def test_enabled_pipeline_spies_every_lottery_stage_and_uses_peer_ids(integration_module: Any) -> None:
    """启用后真实编排必须经过扫描、检测、AI、规划、配额、串行队列和执行器。"""
    ports = FakePorts()
    pipeline = integration_module.LotteryIntegrationPipeline(ports=ports, lottery_enabled=True)

    await pipeline.run_startup_cycle()
    await pipeline.drain_queue()

    for peer_id in (101, 202):
        stages = [f"detect:{peer_id}", f"ai:{peer_id}", f"plan:{peer_id}", f"quota:{peer_id}", f"enqueue:{peer_id}", f"execute:{peer_id}"]
        positions = [ports.events.index(stage) for stage in stages]
        assert positions == sorted(positions)
    assert all(isinstance(plan["peer_id"], int) for plan in ports.plans)


@pytest.mark.asyncio
async def test_lottery_disabled_does_not_scan_or_enqueue(integration_module: Any) -> None:
    """LOTTERY_ENABLED=false 时不得读取置顶/增量消息或建立抽奖计划。"""
    ports = FakePorts()
    pipeline = integration_module.LotteryIntegrationPipeline(ports=ports, lottery_enabled=False)

    await pipeline.run_startup_cycle()

    forbidden = ("pinned:", "incremental:", "detect:", "ai:", "plan:", "quota:", "enqueue:", "execute:")
    assert not any(name.startswith(forbidden) for name in ports.events)
    assert ports.plans == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected_code", [("MANUAL_REQUIRED", "manual_required"), ("QUOTA_REACHED", "quota_reached")])
async def test_untrusted_or_quota_lottery_only_warns_without_execution(integration_module: Any, status: str, expected_code: str) -> None:
    """普通成员、权限未知和已达配额均只能告警，绝不入全局执行队列。"""
    ports = FakePorts(planned_status=status)
    pipeline = integration_module.LotteryIntegrationPipeline(ports=ports, lottery_enabled=True)

    await pipeline.run_startup_cycle()

    assert ports.plans == []
    assert {item["code"] for item in ports.alerts} >= {expected_code}


@pytest.mark.asyncio
async def test_result_unknown_freezes_only_its_lottery_and_open_unfreezes_without_status_loss(integration_module: Any) -> None:
    """未知 callback 结果冻结单群抽奖；用户打开后仅解除冻结且保留参与状态。"""
    ports = FakePorts()
    pipeline = integration_module.LotteryIntegrationPipeline(ports=ports, lottery_enabled=True)

    await pipeline.record_result_unknown(peer_id=101, lottery_id=7)
    before = await pipeline.lottery_state(7)
    await pipeline.open_lottery(7)
    after = await pipeline.lottery_state(7)

    assert before["participation_status"] == after["participation_status"] == "RESULT_UNKNOWN"
    assert before["frozen"] is True and after["frozen"] is False
    assert await pipeline.can_run_normal_participation(101) is True
    assert await pipeline.can_run_lottery(101) is True


@pytest.mark.asyncio
async def test_stop_cancels_not_yet_called_queue_work(integration_module: Any) -> None:
    """停止后未进入 Telegram 调用的队列项必须取消，当前项后续步骤不得开始。"""
    calls: list[str] = []

    async def telegram_call(name: str) -> None:
        calls.append(name)

    pipeline = integration_module.LotteryIntegrationPipeline(ports=FakePorts(), lottery_enabled=True)
    await pipeline.enqueue_actions([lambda: telegram_call("first"), lambda: telegram_call("second")])
    await pipeline.stop_before_next_action()
    await pipeline.drain_queue()

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reread", [None, {"text": "edited", "buttons": []}])
async def test_changed_or_deleted_source_supersedes_old_plan_before_action(integration_module: Any, reread: Any) -> None:
    """执行前重读发现删除或编辑时，旧计划必须 SUPERSEDED 且不能触发动作。"""
    invoked = False

    async def action() -> None:
        nonlocal invoked
        invoked = True

    pipeline = integration_module.LotteryIntegrationPipeline(ports=FakePorts(), lottery_enabled=True)
    result = await pipeline.execute_plan(
        plan={"id": 8, "source": {"text": "original", "buttons": []}, "actions": [action]},
        reread=lambda: asyncio.sleep(0, result=reread),
    )

    assert result == "SUPERSEDED"
    assert invoked is False


def test_dashboard_defaults_to_fifty_and_returns_total_and_stable_page(integration_module: Any) -> None:
    """Dashboard 与分页 API 默认最新 50 条，并同时返回未处理总数。"""
    service = integration_module.LotteryDashboardService()
    service.replace_alerts([{"id": number, "summary": f"lottery-{number}"} for number in range(1, 56)])

    first = service.dashboard()
    second = service.list_alerts(offset=50)

    assert first["lottery_alert_total"] == 55
    assert [item["id"] for item in first["lottery_alerts"]] == list(range(55, 5, -1))
    assert [item["id"] for item in second["items"]] == [5, 4, 3, 2, 1]


def test_open_returns_message_link_or_group_fallback(integration_module: Any) -> None:
    """打开提醒优先返回消息链接，不能构造时必须返回带群和消息 ID 的降级链接。"""
    service = integration_module.LotteryDashboardService()
    service.replace_alerts([
        {"id": 1, "peer_id": 100, "message_id": 9, "message_link": "https://t.me/a/9"},
        {"id": 2, "peer_id": 200, "message_id": 10, "group_link": "https://t.me/c/200"},
    ])

    assert service.open(1)["url"] == "https://t.me/a/9"
    assert service.open(2) == {"url": "https://t.me/c/200", "message_id": 10, "fallback": True}


def test_cleanup_is_stopped_only_shanghai_day_based_and_atomic(integration_module: Any) -> None:
    """清理只允许停止态，按上海自然日删除，并在失败时保留完整数据。"""
    service = integration_module.LotteryDashboardService()
    service.replace_history([
        {"id": 1, "occurred_at": "2026-08-28T15:59:59+00:00"},  # 上海 8 月 28 日
        {"id": 2, "occurred_at": "2026-08-28T16:00:00+00:00"},  # 上海 8 月 29 日
    ])

    with pytest.raises(integration_module.CleanupNotAllowed):
        service.cleanup_before("2026-08-29", manager_state="running")
    assert service.cleanup_before("2026-08-29", manager_state="stopped") == {"history": 1}
    assert service.history_ids() == [2]

    service.fail_next_cleanup = True
    with pytest.raises(RuntimeError):
        service.cleanup_before("2026-08-30", manager_state="idle")
    assert service.history_ids() == [2]


def test_sse_lottery_events_request_refresh_and_redact_secrets(integration_module: Any) -> None:
    """抽奖 SSE 事件驱动刷新，且 callback、密钥和凭据绝不能进入浏览器载荷。"""
    dashboard = SimpleNamespace(refreshes=0)
    dashboard.load_dashboard = lambda: setattr(dashboard, "refreshes", dashboard.refreshes + 1)
    bus = integration_module.LotteryEventBus(on_lottery_event=dashboard.load_dashboard)

    event = bus.publish("lottery_updated", {
        "id": 9, "summary": "safe", "callback_data": "secret-callback",
        "api_key": "secret-key", "credentials": {"phone": "+1"},
    })

    assert dashboard.refreshes == 1
    assert event["type"] == "lottery_updated"
    assert event["data"] == {"id": 9, "summary": "safe"}
