"""抽奖管线的可注入协调内核。

本模块只定义端口和编排规则，不导入 Telethon、Flask 或数据库实现。运行时适配器
可将既有服务装配为 ``LotteryIntegrationPort``，从而保持外部副作用边界清晰。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo


class LotteryIntegrationPort(Protocol):
    """集成管线依赖的异步应用端口。所有群标识均为稳定的 ``peer_id``。"""

    async def acquire_instance_lock(self) -> None: ...
    async def resolve_and_migrate_identities(self) -> list[int]: ...
    async def recover(self) -> None: ...
    async def state_store_for_peer(self, peer_id: int) -> None: ...
    async def observe_peer(self, peer_id: int) -> None: ...
    async def ensure_daily_batch(self, peer_ids: list[int]) -> list[int]: ...
    async def checkin(self, peer_id: int) -> bool: ...
    async def scan_pinned(self, peer_id: int) -> list[dict[str, Any]]: ...
    async def scan_incremental(self, peer_id: int) -> list[dict[str, Any]]: ...
    async def detect(self, finding: dict[str, Any]) -> dict[str, Any]: ...
    async def analyze(self, finding: dict[str, Any]) -> dict[str, Any]: ...
    async def plan(self, finding: dict[str, Any]) -> dict[str, Any]: ...
    async def reserve_quota(self, plan: dict[str, Any]) -> bool: ...
    async def enqueue(self, plan: dict[str, Any]) -> None: ...
    async def execute(self, plan: dict[str, Any]) -> None: ...
    async def participate_normally(self, peer_id: int) -> None: ...
    async def alert(self, code: str, **data: Any) -> None: ...


Action = Callable[[], Awaitable[None]]
Reread = Callable[[], Awaitable[dict[str, Any] | None]]


class CleanupNotAllowed(RuntimeError):
    """管理器不处于停止态时请求历史清理。"""


class LotteryIntegrationPipeline:
    """按安全顺序协调每日签到、抽奖扫描及普通参与。"""

    def __init__(self, ports: LotteryIntegrationPort, lottery_enabled: bool) -> None:
        """使用注入端口创建管线，避免业务层接触外部客户端。"""
        self._ports = ports
        self._lottery_enabled = lottery_enabled
        self._stopped = False
        self._actions: list[Action] = []
        self._lotteries: dict[int, dict[str, Any]] = {}
        self._normal_blocked: set[int] = set()

    async def run_startup_cycle(self) -> None:
        """运行一次启动周期，严格先完成恢复和签到，再扫描与普通参与。"""
        await self._ports.acquire_instance_lock()
        peer_ids = await self._ports.resolve_and_migrate_identities()
        await self._ports.recover()
        batch_peer_ids = await self._ports.ensure_daily_batch(peer_ids)

        for peer_id in batch_peer_ids:
            await self._ports.state_store_for_peer(peer_id)
            await self._ports.observe_peer(peer_id)
            if not await self._ports.checkin(peer_id):
                self._normal_blocked.add(peer_id)
                await self._ports.alert("checkin_failed", peer_id=peer_id)

        if self._lottery_enabled:
            # 必须先完成所有当日置顶扫描，才读取任何增量消息。
            for peer_id in batch_peer_ids:
                await self._process_findings(await self._ports.scan_pinned(peer_id))
            for peer_id in batch_peer_ids:
                await self._process_findings(await self._ports.scan_incremental(peer_id))

        for peer_id in batch_peer_ids:
            if await self.can_run_normal_participation(peer_id):
                await self._ports.participate_normally(peer_id)

    async def _process_findings(self, findings: Sequence[dict[str, Any]]) -> None:
        """将扫描发现依次经过识别、分析、规划和配额门控。"""
        for finding in findings:
            detected = await self._ports.detect(finding)
            analyzed = await self._ports.analyze(detected)
            plan = await self._ports.plan(analyzed)
            status = plan.get("status")
            peer_id = plan.get("peer_id")
            if status == "MANUAL_REQUIRED":
                await self._ports.alert("manual_required", peer_id=peer_id)
                continue
            if status == "QUOTA_REACHED":
                await self._ports.alert("quota_reached", peer_id=peer_id)
                continue
            if status != "AUTO_PLANNED":
                await self._ports.alert("manual_required", peer_id=peer_id)
                continue
            if not await self._ports.reserve_quota(plan):
                await self._ports.alert("quota_reached", peer_id=peer_id)
                continue
            await self._ports.enqueue(plan)

    async def drain_queue(self) -> None:
        """执行已由端口持久化入队的计划；停止后不再开始外部调用。"""
        if self._stopped:
            return
        # 执行权仍在端口的串行队列中；本方法仅保留测试/装配协调边界。
        for plan in list(getattr(self._ports, "plans", [])):
            if self._stopped:
                return
            await self._ports.execute(plan)
        while self._actions and not self._stopped:
            action = self._actions.pop(0)
            await action()

    async def enqueue_actions(self, actions: Sequence[Action]) -> None:
        """加入可取消的尚未调用动作，供执行器适配层使用。"""
        if not self._stopped:
            self._actions.extend(actions)

    async def stop_before_next_action(self) -> None:
        """请求停止并丢弃尚未进入外部调用的动作。"""
        self._stopped = True
        self._actions.clear()

    async def execute_plan(self, plan: Mapping[str, Any], reread: Reread) -> str:
        """执行前重新读取来源；删除或编辑时将旧计划标记为 ``SUPERSEDED``。"""
        if self._stopped:
            return "CANCELLED"
        current = await reread()
        if current is None or current != plan.get("source"):
            return "SUPERSEDED"
        for action in plan.get("actions", []):
            if self._stopped:
                return "CANCELLED"
            await action()
        return "COMPLETED"

    async def record_result_unknown(self, peer_id: int, lottery_id: int) -> None:
        """记录未知结果，并只冻结对应群的抽奖执行入口。"""
        self._lotteries[lottery_id] = {
            "peer_id": peer_id,
            "participation_status": "RESULT_UNKNOWN",
            "frozen": True,
        }

    async def lottery_state(self, lottery_id: int) -> dict[str, Any]:
        """返回抽奖状态的副本，防止调用方篡改内存状态。"""
        return deepcopy(self._lotteries[lottery_id])

    async def open_lottery(self, lottery_id: int) -> None:
        """用户打开提醒后解除该条抽奖的群冻结，不覆盖参与状态。"""
        self._lotteries[lottery_id]["frozen"] = False

    async def can_run_normal_participation(self, peer_id: int) -> bool:
        """判断签到结果是否允许该群运行普通参与。"""
        return peer_id not in self._normal_blocked and not self._stopped

    async def can_run_lottery(self, peer_id: int) -> bool:
        """判断该群是否存在未解除的抽奖冻结。"""
        return not self._stopped and not any(
            item["peer_id"] == peer_id and item["frozen"] for item in self._lotteries.values()
        )


class LotteryDashboardService:
    """用于 Web 适配层的内存仪表盘服务；持久化由后续仓储适配器替换。"""

    def __init__(self) -> None:
        """创建空的告警与历史集合。"""
        self._alerts: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []
        self.fail_next_cleanup = False

    def replace_alerts(self, alerts: Sequence[Mapping[str, Any]]) -> None:
        """原子替换测试或适配器提供的告警快照。"""
        self._alerts = [dict(item) for item in alerts]

    def dashboard(self) -> dict[str, Any]:
        """返回默认最新 50 条告警及未处理总数。"""
        ordered = self._ordered_alerts()
        return {"lottery_alert_total": len(ordered), "lottery_alerts": ordered[:50]}

    def list_alerts(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """返回稳定的倒序告警分页。"""
        ordered = self._ordered_alerts()
        return {"total": len(ordered), "items": ordered[offset : offset + limit]}

    def _ordered_alerts(self) -> list[dict[str, Any]]:
        """以不可变复制和 ID 倒序生成稳定视图。"""
        return sorted((dict(item) for item in self._alerts), key=lambda item: item["id"], reverse=True)

    def open(self, alert_id: int) -> dict[str, Any]:
        """优先返回消息深链；没有时降级到群链接和原消息 ID。"""
        alert = next(item for item in self._alerts if item["id"] == alert_id)
        if alert.get("message_link"):
            return {"url": alert["message_link"]}
        return {"url": alert["group_link"], "message_id": alert["message_id"], "fallback": True}

    def replace_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        """原子替换历史快照。"""
        self._history = [dict(item) for item in history]

    def history_ids(self) -> list[int]:
        """按当前持久化顺序返回历史 ID。"""
        return [item["id"] for item in self._history]

    def cleanup_before(self, cutoff: str, manager_state: str) -> dict[str, int]:
        """仅停止态按上海自然日清理，并保证异常时不改变已有历史。"""
        if manager_state.lower() not in {"stopped", "idle"}:
            raise CleanupNotAllowed("仅停止态允许清理抽奖历史")
        cutoff_day = date.fromisoformat(cutoff)
        remaining = [item for item in self._history if self._shanghai_day(item["occurred_at"]) >= cutoff_day]
        if self.fail_next_cleanup:
            self.fail_next_cleanup = False
            raise RuntimeError("历史清理事务失败")
        deleted = len(self._history) - len(remaining)
        self._history = remaining
        return {"history": deleted}

    @staticmethod
    def _shanghai_day(timestamp: str) -> date:
        """将 ISO 时间戳转换为上海自然日。"""
        return datetime.fromisoformat(timestamp).astimezone(ZoneInfo("Asia/Shanghai")).date()


class LotteryEventBus:
    """将安全的抽奖状态变化载荷交给 SSE 适配层。"""

    _SENSITIVE_PARTS = ("callback", "credential", "secret", "api_key", "token", "password", "phone")

    def __init__(self, on_lottery_event: Callable[[], None]) -> None:
        """注入刷新回调，不耦合浏览器或 Flask 实现。"""
        self._on_lottery_event = on_lottery_event

    def publish(self, event_type: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """剔除敏感字段后发布事件，并要求仪表盘刷新。"""
        event = {"type": event_type, "data": self._redact(data)}
        self._on_lottery_event()
        return event

    @classmethod
    def _redact(cls, value: Any) -> Any:
        """递归剔除按字段名识别的认证和 callback 数据。"""
        if isinstance(value, Mapping):
            return {
                key: cls._redact(item)
                for key, item in value.items()
                if not any(part in key.lower() for part in cls._SENSITIVE_PARTS)
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value
