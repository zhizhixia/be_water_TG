"""线程安全且不包含敏感信息的应用运行状态。"""
from __future__ import annotations

import threading
from typing import TypedDict


class RuntimeStateSnapshot(TypedDict):
    """公开给本地接口的固定字段运行状态快照。"""

    configured: bool
    migration_status: str
    setup_status: str


class ApplicationRuntimeState:
    """原子维护配置结果与固定迁移、设置状态码。"""

    MIGRATION_STATUSES = frozenset(
        {
            "not_started",
            "ready",
            "custom_database_requires_manual_copy",
            "database_destination_conflict",
            "migration_failed",
            "manual_database_invalid",
        }
    )
    SETUP_STATUSES = frozenset({"not_checked", "configured", "configuration_required"})

    def __init__(self) -> None:
        """创建尚未迁移或检查配置的安全初始状态。"""
        self._lock = threading.Lock()
        self._configured = False
        self._migration_status = "not_started"
        self._setup_status = "not_checked"

    def update(
        self,
        *,
        configured: bool | None = None,
        migration_status: str | None = None,
        setup_status: str | None = None,
    ) -> None:
        """校验并在同一临界区内原子更新指定状态字段。"""
        if migration_status is not None and migration_status not in self.MIGRATION_STATUSES:
            raise ValueError("migration_status 必须是固定状态码")
        if setup_status is not None and setup_status not in self.SETUP_STATUSES:
            raise ValueError("setup_status 必须是固定状态码")
        if configured is not None and not isinstance(configured, bool):
            raise TypeError("configured 必须是布尔值")
        with self._lock:
            if configured is not None:
                self._configured = configured
            if migration_status is not None:
                self._migration_status = migration_status
            if setup_status is not None:
                self._setup_status = setup_status

    def snapshot(self) -> RuntimeStateSnapshot:
        """在线程锁保护下返回不含凭据、路径和异常的独立快照。"""
        with self._lock:
            return {
                "configured": self._configured,
                "migration_status": self._migration_status,
                "setup_status": self._setup_status,
            }
