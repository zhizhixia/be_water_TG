from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """集中管理程序资源与用户运行数据的绝对路径。"""

    resource_dir: Path
    data_dir: Path

    @classmethod
    def from_environment(
        cls,
        local_appdata: Path | str | None = None,
        resource_dir: Path | str | None = None,
    ) -> "RuntimePaths":
        """从可注入的 LOCALAPPDATA 构造与当前目录无关的运行路径。"""
        base_dir = local_appdata or os.getenv("LOCALAPPDATA")
        if not base_dir:
            raise RuntimeError("LOCALAPPDATA is required for runtime data")
        resources = resource_dir or Path(__file__).resolve().parent.parent
        paths = cls(Path(resources).resolve(), Path(base_dir).resolve() / "BeWaterTG")
        paths.ensure_directories()
        return paths

    def ensure_directories(self) -> None:
        for directory in (
            self.env_path.parent,
            self.state_db_path.parent,
            self.sender_session_path.parent,
            self.logs_dir,
            self.backups_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def env_path(self) -> Path:
        """返回用户配置文件路径。"""
        return self.data_dir / "config" / ".env"

    @property
    def state_db_path(self) -> Path:
        """返回默认状态数据库路径。"""
        return self.data_dir / "state" / "be_water.db"

    @property
    def sender_session_path(self) -> Path:
        """返回 Telegram 发送账号会话文件路径。"""
        return self.data_dir / "session" / "sender_session.session"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"
