from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ParticipationStatus(StrEnum):
    DETECTED = "DETECTED"; AUTO_PLANNED = "AUTO_PLANNED"; EXECUTING = "EXECUTING"; PARTICIPATED = "PARTICIPATED"; MANUAL_REQUIRED = "MANUAL_REQUIRED"; PARTIAL = "PARTIAL"; RESULT_UNKNOWN = "RESULT_UNKNOWN"; EXPIRED = "EXPIRED"; DUPLICATE = "DUPLICATE"


class AlertStatus(StrEnum):
    NONE = "NONE"; PENDING = "PENDING"; OPENED = "OPENED"


class ActionStatus(StrEnum):
    PENDING = "PENDING"; EXECUTING = "EXECUTING"; COMPLETED = "COMPLETED"; FAILED = "FAILED"; RESULT_UNKNOWN = "RESULT_UNKNOWN"; CANCELLED = "CANCELLED"; SUPERSEDED = "SUPERSEDED"


class ActionType(StrEnum):
    CLICK_BUTTON = "CLICK_BUTTON"; SEND_TEXT = "SEND_TEXT"; REPLY_TEXT = "REPLY_TEXT"


@dataclass(frozen=True)
class ButtonSnapshot:
    text: str
    kind: str
    callback_data: bytes | None = None


@dataclass(frozen=True)
class MessageSnapshot:
    peer_id: int
    message_id: int
    text: str
    buttons: tuple[tuple[ButtonSnapshot, ...], ...] = ()
    occurred_at: str | None = None
    publisher_trust: str = "unknown"
    is_reply: bool = False
    sender_id: int | None = None
    pinned: bool = False
    source: str = "incremental"
    deadline: str | None = None


@dataclass(frozen=True)
class LotteryAction:
    kind: ActionType
    source_signature: str
    row: int | None = None
    column: int | None = None
    text: str | None = None


@dataclass(frozen=True)
class LotteryEntry:
    id: int
    peer_id: int
    message_id: int
    participation_status: ParticipationStatus
    alert_status: AlertStatus
    summary: str
