"""新版应用的共享领域值；不包含 Telegram 或网页副作用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
VERSION = "2.0.0"


def now() -> datetime:
    """返回上海时区当前时间。"""
    return datetime.now(SHANGHAI)


@dataclass(frozen=True)
class Paths:
    """分离只读资源和用户运行数据。"""

    resources: Path
    data: Path

    def prepare(self) -> None:
        """创建固定运行子目录。"""
        for name in ("config/messages", "state", "session", "logs", "backups"):
            (self.data / name).mkdir(parents=True, exist_ok=True)

    @property
    def config(self) -> Path:
        """返回新版配置位置，旧 dotenv 保留作为迁移来源。"""
        return self.data / "config/settings.json"

    @property
    def database(self) -> Path:
        """新版使用独立表，不改写旧账本。"""
        return self.data / "state/be_water.db"

    @property
    def resource_dir(self) -> Path:
        """向已验证的平台安全读取器提供资源根。"""
        return self.resources

    @property
    def data_dir(self) -> Path:
        """向平台迁移器提供数据根。"""
        return self.data

    @property
    def env_path(self) -> Path:
        """旧 dotenv 只作为兼容导入来源。"""
        return self.data / "config/.env"

    @property
    def state_db_path(self) -> Path:
        """兼容原生迁移器的固定数据库路径。"""
        return self.database

    @property
    def sender_session_path(self) -> Path:
        """兼容原生迁移器的固定会话路径。"""
        return self.session

    @property
    def messages_dir(self) -> Path:
        """返回用户可编辑消息根。"""
        return self.data / "config/messages"

    @property
    def logs_dir(self) -> Path:
        """返回轮转日志目录。"""
        return self.data / "logs"

    @property
    def backups_dir(self) -> Path:
        """返回持久备份目录。"""
        return self.data / "backups"

    def ensure_directories(self) -> None:
        """保持迁移器的目录初始化契约。"""
        self.prepare()

    @property
    def session(self) -> Path:
        """保持旧登录会话路径兼容。"""
        return self.data / "session/sender_session.session"


@dataclass(frozen=True)
class Identity:
    """群配置别名与 Telegram marked ID 的对应关系。"""

    ref: str
    peer_id: int
    title: str
    username: str | None = None


@dataclass(frozen=True)
class Button:
    """不可变按钮快照；callback 内容禁止进入公开响应。"""

    text: str
    kind: str
    data: bytes = field(default=b"", repr=False)


@dataclass(frozen=True)
class Message:
    """普通参与与抽奖共用的消息事实。"""

    peer_id: int
    id: int
    text: str
    date: datetime
    sender_id: int | None = None
    own: bool = False
    reply_to: int | None = None
    buttons: tuple[tuple[Button, ...], ...] = ()
    pinned: bool = False
    source: str = "recent"


@dataclass(frozen=True)
class Action:
    """仅引用原消息的白名单动作。"""

    kind: str
    message_id: int
    text: str = ""
    row: int | None = None
    column: int | None = None


class DomainError(ValueError):
    """可安全显示的业务错误。"""

    def __init__(self, message: str, field: str = "", status: int = 422) -> None:
        """记录字段与 HTTP 错误类别，不接受凭据原文。"""
        super().__init__(message)
        self.field = field
        self.status = status
