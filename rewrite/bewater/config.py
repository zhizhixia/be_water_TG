"""完整配置读写、旧 dotenv 导入及严格字段校验。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from bewater.models import DomainError, Paths

DEFAULTS: dict[str, object] = {
    "api_id": 0,
    "api_hash": "",
    "phone": "",
    "groups": [],
    "proxy_host": "",
    "proxy_port": 0,
    "proxy_type": "http",
    "ai_enabled": False,
    "ai_consent": False,
    "ai_api_key": "",
    "ai_base_url": "https://api.deepseek.com/v1",
    "ai_model": "deepseek-chat",
    "ai_prompt": "根据最近群聊简短、相关地参与，不虚构身份或经历。不值得参与时输出 [SKIP]。",
    "ai_context_count": 5,
    "ai_temperature": 0.7,
    "ai_max_tokens": 500,
    "ai_timeout": 30,
    "daily_limit": 30,
    "idle_threshold_minutes": 10,
    "question_reply_pct": 70,
    "discussion_reply_pct": 15,
    "reply_delay_min": 20,
    "reply_delay_max": 90,
    "schedule_enabled": False,
    "schedule_morning_start": "08:00",
    "schedule_morning_end": "11:00",
    "schedule_afternoon_start": "14:00",
    "schedule_afternoon_end": "18:00",
    "lottery_scan_interval_minutes": 60,
    "daily_pinned_lottery_scan_enabled": True,
    "lottery_per_group_daily_limit": 10,
    "lottery_account_daily_limit": 30,
    "lottery_action_gap_min_seconds": 20,
    "lottery_action_gap_max_seconds": 60,
}
RANGES = {
    "api_id": (0, 2147483647),
    "proxy_port": (0, 65535),
    "daily_limit": (1, 1000),
    "idle_threshold_minutes": (1, 1440),
    "question_reply_pct": (0, 100),
    "discussion_reply_pct": (0, 100),
    "reply_delay_min": (0, 3600),
    "reply_delay_max": (0, 3600),
    "ai_context_count": (1, 20),
    "ai_max_tokens": (1, 4096),
    "ai_timeout": (1, 120),
    "lottery_scan_interval_minutes": (15, 1440),
    "lottery_per_group_daily_limit": (1, 50),
    "lottery_account_daily_limit": (1, 200),
    "lottery_action_gap_min_seconds": (5, 600),
    "lottery_action_gap_max_seconds": (5, 600),
}
SECRETS = {"api_hash", "ai_api_key"}


def legacy_message_name(reference: str) -> str:
    """旧复杂引用映射为独立安全文件名，实际读取仍由安全迁移器决定。"""
    if re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]+\.txt", reference):
        return reference
    return "imported-" + hashlib.sha256(reference.encode("utf-8")).hexdigest()[:12] + ".txt"


def normalize_ref(value: str) -> str:
    """仅接受群用户名或 Telegram 群引用，不执行加入操作。"""
    value = value.strip().rstrip("/")
    if value.startswith("@"):
        value = "https://t.me/" + value[1:]
    if value.startswith("t.me/"):
        value = "https://" + value
    if not re.fullmatch(r"https://t\.me/(?:[A-Za-z][A-Za-z0-9_]{3,}|\+[A-Za-z0-9_-]+)", value):
        raise DomainError("请输入有效的 Telegram 群用户名或群链接", "groups")
    return value


def validate_group(group: object) -> dict[str, object]:
    """校验每群任务开关，不用字符串真值代替布尔值。"""
    if not isinstance(group, dict) or set(group) - {
        "ref",
        "chat",
        "checkin",
        "lottery",
        "authorized",
        "messages",
    }:
        raise DomainError("群组配置格式无效", "groups")
    value = {
        "ref": "",
        "chat": True,
        "checkin": False,
        "lottery": False,
        "authorized": False,
        "messages": "messages.txt",
        **group,
    }
    for key in ("chat", "checkin", "lottery", "authorized"):
        if type(value[key]) is not bool:
            raise DomainError("群组开关必须是布尔值", "groups")
    if not isinstance(value["ref"], str):
        raise DomainError("群链接必须是文本", "groups")
    value["ref"] = normalize_ref(value["ref"])
    if not isinstance(value["messages"], str) or not re.fullmatch(
        r"[A-Za-z0-9_\-\u4e00-\u9fff]+\.txt", value["messages"]
    ):
        raise DomainError("消息文件只允许普通 txt 文件名", "groups")
    return value


def validate(values: dict[str, object], *, start: bool = False) -> None:
    """统一校验配置模型和启动必要条件。"""
    for key, default in DEFAULTS.items():
        value = values[key]
        if type(default) is bool and type(value) is not bool:
            raise DomainError("开关必须是布尔值", key)
        if isinstance(default, str) and (not isinstance(value, str) or len(value) > 16000):
            raise DomainError("文本字段无效或过长", key)
    for key, (minimum, maximum) in RANGES.items():
        value = values[key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise DomainError(f"数值必须在 {minimum} 到 {maximum} 之间", key)
    if type(values["ai_temperature"]) not in (int, float) or not 0 <= values["ai_temperature"] <= 2:
        raise DomainError("温度必须在 0 到 2 之间", "ai_temperature")
    _validate_relations(values)
    if not isinstance(values["groups"], list) or len(values["groups"]) > 100:
        raise DomainError("群组列表无效或超过100个", "groups")
    groups = [validate_group(item) for item in values["groups"]]
    if len({g["ref"] for g in groups}) != len(groups):
        raise DomainError("授权群不能重复", "groups")
    values["groups"] = groups
    if start:
        _validate_start(values)


def _validate_relations(values: dict[str, object]) -> None:
    """校验时间范围、额度、代理与 AI 地址。"""
    for low, high in (
        ("reply_delay_min", "reply_delay_max"),
        ("lottery_action_gap_min_seconds", "lottery_action_gap_max_seconds"),
        ("lottery_per_group_daily_limit", "lottery_account_daily_limit"),
    ):
        if values[low] > values[high]:
            raise DomainError("上限不能小于下限", high)
    for period in ("morning", "afternoon"):
        for edge in ("start", "end"):
            key = f"schedule_{period}_{edge}"
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(values[key])):
                raise DomainError("时间格式应为 HH:MM", key)
        if values[f"schedule_{period}_start"] >= values[f"schedule_{period}_end"]:
            raise DomainError("结束时间必须晚于开始时间", f"schedule_{period}_end")
    if bool(values["proxy_host"]) != bool(values["proxy_port"]):
        raise DomainError("代理地址与端口必须同时填写", "proxy_host")
    if values["proxy_type"] not in {"http", "socks5", "socks4"}:
        raise DomainError("代理类型无效", "proxy_type")
    address = urlparse(str(values["ai_base_url"]))
    if (
        address.scheme not in {"http", "https"}
        or not address.hostname
        or address.username
        or address.password
    ):
        raise DomainError("AI 地址必须是无内嵌凭据的 HTTP(S) 地址", "ai_base_url")


def _validate_start(values: dict[str, object]) -> None:
    """开始才要求完整凭据、有效任务和明确的授权。"""
    if not values["api_id"] or not re.fullmatch(r"[0-9a-fA-F]{32}", str(values["api_hash"])):
        raise DomainError("请填写有效的 API ID 和32位 API Hash", "api_hash")
    if not re.fullmatch(r"\+\d{7,15}", str(values["phone"])):
        raise DomainError("手机号需包含国际区号", "phone")
    if not any(
        g["authorized"] and any(g[k] for k in ("chat", "checkin", "lottery"))
        for g in values["groups"]
    ):
        raise DomainError("至少配置一个已确认许可并启用任务的群", "groups")
    if values["ai_enabled"] and (not values["ai_api_key"] or not values["ai_consent"]):
        raise DomainError("启用 AI 需要密钥及文本外发确认", "ai_consent")


class ConfigStore:
    """单一配置事实来源；版本保护和原子保存避免网页覆盖新配置。"""

    def __init__(self, paths: Paths) -> None:
        """不在构造时连接外部服务或修改旧配置。"""
        self.paths = paths
        self.lock = threading.RLock()

    def load(self) -> tuple[dict[str, object], str]:
        """返回完整有效值及不透明版本标记。"""
        with self.lock:
            if self.paths.config.exists():
                raw = self.paths.config.read_bytes()
                try:
                    values = {**copy.deepcopy(DEFAULTS), **json.loads(raw)}
                except (ValueError, TypeError):
                    raise DomainError("新版配置损坏，请从备份恢复", status=409)
            else:
                values = self._legacy()
                raw = json.dumps(values, sort_keys=True, ensure_ascii=False).encode()
            validate(values)
            return values, hashlib.sha256(raw).hexdigest()

    def public(self) -> dict[str, object]:
        """脱敏完整配置，不遗漏任何功能开关。"""
        values, revision = self.load()
        for key in SECRETS:
            values[key + "_set"] = bool(values[key])
            values[key] = ""
        return {"values": values, "revision": revision}

    def update(self, patch: dict[str, object], revision: str) -> dict[str, object]:
        """合并明确字段；缺项保持原值，密钥清除必须显式指定。"""
        with self.lock:
            values, current = self.load()
            if revision != current:
                raise DomainError("配置已变化，请重新加载后保存", status=409)
            unknown = set(patch) - set(DEFAULTS) - {"clear_secrets"}
            if unknown:
                raise DomainError("包含未知配置项")
            clear = patch.get("clear_secrets", [])
            if not isinstance(clear, list) or any(k not in SECRETS for k in clear):
                raise DomainError("密钥清除请求无效")
            for key, value in patch.items():
                if key == "clear_secrets":
                    continue
                if key in SECRETS and value == "":
                    continue
                values[key] = value
            for key in clear:
                values[key] = ""
            validate(values)
            self._write(values)
            return self.public()

    def _write(self, values: dict[str, object]) -> None:
        """用独占临时文件原子替换，不破坏旧 dotenv。"""
        self.paths.prepare()
        descriptor, name = tempfile.mkstemp(prefix=".settings-", dir=self.paths.config.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(values, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.paths.config)
        finally:
            Path(name).unlink(missing_ok=True)

    def _legacy(self) -> dict[str, object]:
        """只读导入旧配置；任务开关尊重原显式值，授权需升级后确认。"""
        values = copy.deepcopy(DEFAULTS)
        path = self.paths.data / "config/.env"
        if not path.exists():
            return values
        legacy = dotenv_values(path, interpolate=False)
        for key, default in DEFAULTS.items():
            raw = legacy.get(key.upper())
            if raw is None or key == "groups":
                continue
            if type(default) is bool:
                values[key] = raw.lower() in {"true", "1", "yes"}
            elif type(default) in {int, float}:
                try:
                    values[key] = type(default)(raw) if raw.strip() else default
                except ValueError as exc:
                    raise DomainError(
                        f"旧版配置 {key.upper()} 不是有效数字，请修正 config/.env 后重试", key
                    ) from exc
            else:
                values[key] = raw
        refs = re.split(
            r"[,，\n]+", legacy.get("TARGET_GROUPS") or legacy.get("TARGET_GROUP") or ""
        )
        mapping = dict(
            part.split("|", 1)
            for part in (legacy.get("MESSAGE_FILES") or "").split(",")
            if "|" in part
        )
        groups = []
        for ref in filter(str.strip, refs):
            normalized = normalize_ref(ref)
            if any(g["ref"] == normalized for g in groups):
                continue
            groups.append(
                {
                    "ref": normalized,
                    "authorized": False,
                    "chat": True,
                    "checkin": str(legacy.get("DAILY_CHECKIN_ENABLED", "false")).lower() == "true",
                    "lottery": str(legacy.get("LOTTERY_ENABLED", "false")).lower() == "true",
                    "messages": legacy_message_name(mapping.get(ref, "messages.txt")),
                }
            )
        values["groups"] = groups
        return values
