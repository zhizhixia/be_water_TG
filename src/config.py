from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

from src.group_parser import normalize_group_link
from src.runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """项目配置数据类。"""

    api_id: int
    api_hash: str
    phone: str
    target_groups: list[str] = field(default_factory=list)
    target_group: str = ""  # Deprecated: use target_groups instead
    min_interval: int = 60
    max_interval: int = 180
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_type: str = "http"
    message_files: dict[str, str] = field(default_factory=dict)

    # AI 聊天模式配置
    ai_enabled: bool = False
    ai_api_key: str = ""
    ai_base_url: str = "https://api.deepseek.com/v1"
    ai_model: str = "deepseek-chat"
    ai_prompt: str = ""
    ai_context_count: int = 5
    ai_temperature: float = 0.7
    ai_max_tokens: int = 500
    ai_timeout: int = 30

    # 定时运行窗口
    schedule_enabled: bool = False
    schedule_morning_start: str = "08:00"
    schedule_morning_end: str = "11:00"
    schedule_afternoon_start: str = "14:00"
    schedule_afternoon_end: str = "18:00"
    anti_detect: bool = False

    # 反检测增强参数（真人模拟：打字延迟、思考延迟、跳过轮次）
    typing_delay_min: int = 3
    typing_delay_max: int = 8
    thinking_delay_min: int = 5
    thinking_delay_max: int = 25
    skip_round_pct: int = 10

    # 群组间发送间隔（秒），用于控制每轮内向不同群组发送之间的最小/最大等待
    group_gap_min: int = 1
    group_gap_max: int = 1

    # 授权群组自动参与策略（所有目标群组共用）
    daily_limit: int = 30
    idle_threshold_minutes: int = 10
    question_reply_pct: int = 70
    discussion_reply_pct: int = 15
    reply_delay_min: int = 20
    reply_delay_max: int = 90
    state_db_path: str = "state/be_water.db"
    lottery_enabled: bool = True
    lottery_scan_interval_minutes: int = 60
    daily_pinned_lottery_scan_enabled: bool = True
    daily_checkin_enabled: bool = True
    lottery_per_group_daily_limit: int = 10
    lottery_account_daily_limit: int = 30
    lottery_action_gap_min_seconds: int = 20
    lottery_action_gap_max_seconds: int = 60

    def __post_init__(self) -> None:
        """同步 target_group 与 target_groups 以保持向后兼容。"""
        normalized_groups: list[str] = []
        for group in self.target_groups:
            normalized = normalize_group_link(str(group).strip())
            if normalized and normalized not in normalized_groups:
                normalized_groups.append(normalized)
        self.target_groups = normalized_groups
        if self.target_group:
            self.target_group = normalize_group_link(self.target_group.strip())
        self.message_files = {
            normalize_group_link(str(group).strip()): str(path).strip()
            for group, path in self.message_files.items()
            if str(group).strip() and str(path).strip()
        }
        if self.target_groups and not self.target_group:
            self.target_group = self.target_groups[0]
        elif self.target_group and not self.target_groups:
            self.target_groups = [self.target_group]
        validate_participation_settings(self)


class SettingsValidationError(ValueError):
    """A configuration error associated with a Web form field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def validate_participation_settings(settings: Settings) -> None:
    """校验自动参与参数，配置文件和 Web API 共用。"""
    if settings.daily_limit <= 0:
        raise ValueError("DAILY_LIMIT must be greater than 0")
    if settings.idle_threshold_minutes <= 0:
        raise ValueError("IDLE_THRESHOLD_MINUTES must be greater than 0")
    for name, value in (
        ("QUESTION_REPLY_PCT", settings.question_reply_pct),
        ("DISCUSSION_REPLY_PCT", settings.discussion_reply_pct),
    ):
        if not 0 <= value <= 100:
            raise ValueError(f"{name} must be between 0 and 100")
    if settings.reply_delay_min < 0:
        raise ValueError("REPLY_DELAY_MIN must be at least 0")
    if settings.reply_delay_max < settings.reply_delay_min:
        raise ValueError("REPLY_DELAY_MAX must be >= REPLY_DELAY_MIN")
    if not settings.state_db_path.strip():
        raise ValueError("STATE_DB_PATH must not be empty")
    if not 15 <= settings.lottery_scan_interval_minutes <= 1440:
        raise ValueError("LOTTERY_SCAN_INTERVAL_MINUTES must be between 15 and 1440")
    if not 1 <= settings.lottery_per_group_daily_limit <= 50:
        raise ValueError("LOTTERY_PER_GROUP_DAILY_LIMIT must be between 1 and 50")
    if not 1 <= settings.lottery_account_daily_limit <= 200:
        raise ValueError("LOTTERY_ACCOUNT_DAILY_LIMIT must be between 1 and 200")
    if settings.lottery_account_daily_limit < settings.lottery_per_group_daily_limit:
        raise ValueError("LOTTERY_ACCOUNT_DAILY_LIMIT must be >= LOTTERY_PER_GROUP_DAILY_LIMIT")
    if not 5 <= settings.lottery_action_gap_min_seconds <= 600:
        raise ValueError("LOTTERY_ACTION_GAP_MIN_SECONDS must be between 5 and 600")
    if not settings.lottery_action_gap_min_seconds <= settings.lottery_action_gap_max_seconds <= 600:
        raise ValueError("LOTTERY_ACTION_GAP_MAX_SECONDS must be between min gap and 600")


def validate_settings_for_save(settings: Settings) -> None:
    """Validate all values needed to persist a configuration that can start."""
    if settings.api_id <= 0:
        raise SettingsValidationError("api_id", "API ID must be greater than 0")
    if not settings.api_hash.strip():
        raise SettingsValidationError("api_hash", "API Hash is required")
    if not settings.phone.strip():
        raise SettingsValidationError("phone", "Phone is required")
    if not settings.target_groups:
        raise SettingsValidationError("target_groups", "At least one target group is required")
    if settings.min_interval <= 0 or settings.max_interval <= settings.min_interval:
        raise SettingsValidationError("max_interval", "MAX_INTERVAL must be greater than MIN_INTERVAL")
    if bool(settings.proxy_host) != bool(settings.proxy_port):
        raise SettingsValidationError("proxy_host", "Proxy host and port must be provided together")
    if settings.ai_enabled and not settings.ai_api_key.strip():
        raise SettingsValidationError("ai_api_key", "AI API key is required when AI is enabled")
    if settings.ai_context_count <= 0:
        raise SettingsValidationError("ai_context_count", "AI context count must be greater than 0")
    if not 0 <= settings.ai_temperature <= 2:
        raise SettingsValidationError("ai_temperature", "AI temperature must be between 0 and 2")
    if settings.ai_max_tokens <= 0 or settings.ai_timeout <= 0:
        raise SettingsValidationError("ai_max_tokens", "AI token and timeout values must be positive")
    if settings.schedule_enabled:
        try:
            windows = (
                (time.fromisoformat(settings.schedule_morning_start), time.fromisoformat(settings.schedule_morning_end)),
                (time.fromisoformat(settings.schedule_afternoon_start), time.fromisoformat(settings.schedule_afternoon_end)),
            )
        except ValueError as exc:
            raise SettingsValidationError("schedule_morning_start", "Invalid schedule time") from exc
        if any(start > end for start, end in windows):
            raise SettingsValidationError("schedule_morning_start", "Schedule start must not be after end")
    try:
        validate_participation_settings(settings)
    except ValueError as exc:
        field = str(exc).split(" must", 1)[0].lower()
        raise SettingsValidationError(field, str(exc)) from exc


def _parse_legacy_message_file(part: str) -> tuple[str, str]:
    """解析旧格式 MESSAGE_FILES 中的一个条目（: 分隔）。

    兼容以下场景：
    - group:path（简单格式）
    - https://t.me/group:path（含 URL，: 在 URL 中）
    - https://t.me/group:C:\\path（含 Windows 绝对路径）
    """
    # 无 URL：简单分割
    if "://" not in part:
        group, path = part.split(":", 1)
        return group, path

    # 含 URL + Windows 绝对路径 (如 C:\...) — 排除 :// URL 冒号
    drive_match = re.search(r"[a-zA-Z]:[\\/](?!/)", part)
    if drive_match:
        # 分隔符是驱动器字母前的最后一个 :
        prefix = part[: drive_match.start()]
        idx = prefix.rfind(":")
        if idx > 0:
            return part[:idx].strip(), part[idx + 1 :].strip()

    # 含 URL + 相对路径：最后一个 : 是分隔符
    idx = part.rfind(":")
    if idx > 0:
        return part[:idx].strip(), part[idx + 1 :].strip()

    return "", ""


def load_settings(runtime_paths: RuntimePaths | None = None) -> Settings:
    """从 .env 文件加载配置并校验。

    Returns:
        Settings 实例。

    Raises:
        ValueError: 配置缺失或无效时抛出。
    """
    paths = runtime_paths or RuntimePaths.from_environment()
    load_dotenv(dotenv_path=paths.env_path)

    api_id_str = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    phone = os.getenv("PHONE")

    if not api_id_str:
        raise ValueError("API_ID is required in .env file")
    if not api_hash:
        raise ValueError("API_HASH is required in .env file")
    if not phone:
        raise ValueError("PHONE is required in .env file")

    # 目标群组: 优先使用 TARGET_GROUPS (逗号分隔), 回退到 TARGET_GROUP
    target_groups_str = os.getenv("TARGET_GROUPS")
    target_group_str = os.getenv("TARGET_GROUP")

    target_groups: list[str] = []
    target_group: str = ""

    if target_groups_str and target_group_str:
        logger.warning("同时配置了 TARGET_GROUPS 和 TARGET_GROUP，将使用 TARGET_GROUPS")
        target_groups = [g.strip() for g in target_groups_str.replace("，", ",").split(",") if g.strip()]
    elif target_groups_str:
        target_groups = [g.strip() for g in target_groups_str.replace("，", ",").split(",") if g.strip()]
    elif target_group_str:
        target_group = target_group_str
        target_groups = [target_group_str]
    else:
        raise ValueError("必须配置 TARGET_GROUPS 或 TARGET_GROUP")

    try:
        api_id = int(api_id_str)
    except ValueError:
        raise ValueError(f"API_ID must be an integer, got: {api_id_str}")

    min_interval = int(os.getenv("MIN_INTERVAL", "60"))
    max_interval = int(os.getenv("MAX_INTERVAL", "180"))

    if max_interval <= min_interval:
        raise ValueError(
            f"MAX_INTERVAL ({max_interval}) must be greater than MIN_INTERVAL ({min_interval})"
        )

    # 代理配置（可选）
    proxy_host = os.getenv("PROXY_HOST")  # 例如 127.0.0.1
    proxy_port_str = os.getenv("PROXY_PORT")  # 例如 7890
    proxy_type = os.getenv("PROXY_TYPE", "http")

    proxy_port = int(proxy_port_str) if proxy_port_str else None

    if proxy_host and not proxy_port:
        raise ValueError("PROXY_HOST is set but PROXY_PORT is missing")
    if proxy_port and not proxy_host:
        raise ValueError("PROXY_PORT is set but PROXY_HOST is missing")

    # 消息文件映射 (可选): MESSAGE_FILES=群组链接|文件路径,群组2|文件2
    # 注意: 群组链接含完整 URL (https://t.me/xxx)，分隔符用 | 避免与 URL 的 : 冲突
    message_files: dict[str, str] = {}
    mf_raw = os.getenv("MESSAGE_FILES", "")
    if mf_raw.strip():
        for part in mf_raw.split(","):
            part = part.strip()
            if not part:
                continue
            # 优先用 | 分隔（新格式），兼容 : 分隔（旧格式）
            if "|" in part:
                group, path = part.split("|", 1)
            elif ":" in part:
                group, path = _parse_legacy_message_file(part)
                if not group or not path:
                    continue
            else:
                continue
            message_files[group.strip()] = path.strip()

    # AI 聊天模式配置
    ai_enabled = os.getenv("AI_ENABLED", "").lower() in ("true", "1", "yes")
    ai_api_key = os.getenv("AI_API_KEY", "")
    ai_base_url = os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1")
    ai_model = os.getenv("AI_MODEL", "deepseek-chat")
    ai_prompt = os.getenv("AI_PROMPT", "")
    ai_context_count = int(os.getenv("AI_CONTEXT_COUNT", "5"))
    ai_temperature = float(os.getenv("AI_TEMPERATURE", "0.7"))
    ai_max_tokens = int(os.getenv("AI_MAX_TOKENS", "500"))
    ai_timeout = int(os.getenv("AI_TIMEOUT", "30"))

    # 定时运行窗口
    schedule_enabled = os.getenv("SCHEDULE_ENABLED", "").lower() in ("true", "1", "yes")
    schedule_morning_start = os.getenv("SCHEDULE_MORNING_START", "08:00")
    schedule_morning_end = os.getenv("SCHEDULE_MORNING_END", "11:00")
    schedule_afternoon_start = os.getenv("SCHEDULE_AFTERNOON_START", "14:00")
    schedule_afternoon_end = os.getenv("SCHEDULE_AFTERNOON_END", "18:00")
    anti_detect = os.getenv("ANTI_DETECT", "").lower() in ("true", "1", "yes")
    typing_delay_min = int(os.getenv("TYPING_DELAY_MIN", "3"))
    typing_delay_max = int(os.getenv("TYPING_DELAY_MAX", "8"))
    thinking_delay_min = int(os.getenv("THINKING_DELAY_MIN", "5"))
    thinking_delay_max = int(os.getenv("THINKING_DELAY_MAX", "25"))
    skip_round_pct = int(os.getenv("SKIP_ROUND_PCT", "10"))
    group_gap_min = int(os.getenv("GROUP_GAP_MIN", "1"))
    group_gap_max = int(os.getenv("GROUP_GAP_MAX", "1"))
    daily_limit = int(os.getenv("DAILY_LIMIT", "30"))
    idle_threshold_minutes = int(os.getenv("IDLE_THRESHOLD_MINUTES", "10"))
    question_reply_pct = int(os.getenv("QUESTION_REPLY_PCT", "70"))
    discussion_reply_pct = int(os.getenv("DISCUSSION_REPLY_PCT", "15"))
    reply_delay_min = int(os.getenv("REPLY_DELAY_MIN", "20"))
    reply_delay_max = int(os.getenv("REPLY_DELAY_MAX", "90"))
    state_db_path = str(paths.state_db_path)
    lottery_enabled = os.getenv("LOTTERY_ENABLED", "true").lower() in ("true", "1", "yes")
    lottery_scan_interval_minutes = int(os.getenv("LOTTERY_SCAN_INTERVAL_MINUTES", "60"))
    daily_pinned_lottery_scan_enabled = os.getenv("DAILY_PINNED_LOTTERY_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
    daily_checkin_enabled = os.getenv("DAILY_CHECKIN_ENABLED", "true").lower() in ("true", "1", "yes")
    lottery_per_group_daily_limit = int(os.getenv("LOTTERY_PER_GROUP_DAILY_LIMIT", "10"))
    lottery_account_daily_limit = int(os.getenv("LOTTERY_ACCOUNT_DAILY_LIMIT", "30"))
    lottery_action_gap_min_seconds = int(os.getenv("LOTTERY_ACTION_GAP_MIN_SECONDS", "20"))
    lottery_action_gap_max_seconds = int(os.getenv("LOTTERY_ACTION_GAP_MAX_SECONDS", "60"))

    if group_gap_max < group_gap_min:
        raise ValueError(
            f"GROUP_GAP_MAX ({group_gap_max}) must be >= GROUP_GAP_MIN ({group_gap_min})"
        )

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        target_groups=target_groups,
        target_group=target_group,
        min_interval=min_interval,
        max_interval=max_interval,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_type=proxy_type,
        message_files=message_files,
        ai_enabled=ai_enabled,
        ai_api_key=ai_api_key,
        ai_base_url=ai_base_url,
        ai_model=ai_model,
        ai_prompt=ai_prompt,
        ai_context_count=ai_context_count,
        ai_temperature=ai_temperature,
        ai_max_tokens=ai_max_tokens,
        ai_timeout=ai_timeout,
        schedule_enabled=schedule_enabled,
        schedule_morning_start=schedule_morning_start,
        schedule_morning_end=schedule_morning_end,
        schedule_afternoon_start=schedule_afternoon_start,
        schedule_afternoon_end=schedule_afternoon_end,
        anti_detect=anti_detect,
        typing_delay_min=typing_delay_min,
        typing_delay_max=typing_delay_max,
        thinking_delay_min=thinking_delay_min,
        thinking_delay_max=thinking_delay_max,
        skip_round_pct=skip_round_pct,
        group_gap_min=group_gap_min,
        group_gap_max=group_gap_max,
        daily_limit=daily_limit,
        idle_threshold_minutes=idle_threshold_minutes,
        question_reply_pct=question_reply_pct,
        discussion_reply_pct=discussion_reply_pct,
        reply_delay_min=reply_delay_min,
        reply_delay_max=reply_delay_max,
        state_db_path=state_db_path,
        lottery_enabled=lottery_enabled,
        lottery_scan_interval_minutes=lottery_scan_interval_minutes,
        daily_pinned_lottery_scan_enabled=daily_pinned_lottery_scan_enabled,
        daily_checkin_enabled=daily_checkin_enabled,
        lottery_per_group_daily_limit=lottery_per_group_daily_limit,
        lottery_account_daily_limit=lottery_account_daily_limit,
        lottery_action_gap_min_seconds=lottery_action_gap_min_seconds,
        lottery_action_gap_max_seconds=lottery_action_gap_max_seconds,
    )


def save_settings(
    settings: Settings,
    path: str | None = None,
    runtime_paths: RuntimePaths | None = None,
) -> None:
    """将 Settings 持久化到 .env 文件。

    Args:
        settings: Settings 实例。
        path: .env 文件路径，默认为项目根目录下的 .env。
    """
    paths = runtime_paths or RuntimePaths.from_environment()
    if path is None:
        path = str(paths.env_path)

    # 构建新值字典
    new_values: dict[str, str] = {}
    new_values["API_ID"] = str(settings.api_id)
    new_values["API_HASH"] = settings.api_hash
    new_values["PHONE"] = settings.phone
    new_values["MIN_INTERVAL"] = str(settings.min_interval)
    new_values["MAX_INTERVAL"] = str(settings.max_interval)
    new_values["PROXY_HOST"] = settings.proxy_host or ""
    new_values["PROXY_PORT"] = str(settings.proxy_port) if settings.proxy_port else ""
    new_values["PROXY_TYPE"] = settings.proxy_type
    new_values["MESSAGE_FILES"] = ",".join(
        f"{g}|{p}" for g, p in settings.message_files.items()
    )
    new_values["AI_ENABLED"] = "true" if settings.ai_enabled else "false"
    new_values["AI_API_KEY"] = settings.ai_api_key
    new_values["AI_BASE_URL"] = settings.ai_base_url
    new_values["AI_MODEL"] = settings.ai_model
    new_values["AI_PROMPT"] = settings.ai_prompt
    new_values["AI_CONTEXT_COUNT"] = str(settings.ai_context_count)
    new_values["AI_TEMPERATURE"] = str(settings.ai_temperature)
    new_values["AI_MAX_TOKENS"] = str(settings.ai_max_tokens)
    new_values["AI_TIMEOUT"] = str(settings.ai_timeout)

    new_values["SCHEDULE_ENABLED"] = "true" if settings.schedule_enabled else "false"
    new_values["SCHEDULE_MORNING_START"] = settings.schedule_morning_start
    new_values["SCHEDULE_MORNING_END"] = settings.schedule_morning_end
    new_values["SCHEDULE_AFTERNOON_START"] = settings.schedule_afternoon_start
    new_values["SCHEDULE_AFTERNOON_END"] = settings.schedule_afternoon_end
    new_values["ANTI_DETECT"] = "true" if settings.anti_detect else "false"
    new_values["TYPING_DELAY_MIN"] = str(settings.typing_delay_min)
    new_values["TYPING_DELAY_MAX"] = str(settings.typing_delay_max)
    new_values["THINKING_DELAY_MIN"] = str(settings.thinking_delay_min)
    new_values["THINKING_DELAY_MAX"] = str(settings.thinking_delay_max)
    new_values["SKIP_ROUND_PCT"] = str(settings.skip_round_pct)
    new_values["GROUP_GAP_MIN"] = str(settings.group_gap_min)
    new_values["GROUP_GAP_MAX"] = str(settings.group_gap_max)
    new_values["DAILY_LIMIT"] = str(settings.daily_limit)
    new_values["IDLE_THRESHOLD_MINUTES"] = str(settings.idle_threshold_minutes)
    new_values["QUESTION_REPLY_PCT"] = str(settings.question_reply_pct)
    new_values["DISCUSSION_REPLY_PCT"] = str(settings.discussion_reply_pct)
    new_values["REPLY_DELAY_MIN"] = str(settings.reply_delay_min)
    new_values["REPLY_DELAY_MAX"] = str(settings.reply_delay_max)
    new_values["STATE_DB_PATH"] = str(paths.state_db_path)
    new_values["LOTTERY_ENABLED"] = str(settings.lottery_enabled).lower()
    new_values["LOTTERY_SCAN_INTERVAL_MINUTES"] = str(settings.lottery_scan_interval_minutes)
    new_values["DAILY_PINNED_LOTTERY_SCAN_ENABLED"] = str(settings.daily_pinned_lottery_scan_enabled).lower()
    new_values["DAILY_CHECKIN_ENABLED"] = str(settings.daily_checkin_enabled).lower()
    new_values["LOTTERY_PER_GROUP_DAILY_LIMIT"] = str(settings.lottery_per_group_daily_limit)
    new_values["LOTTERY_ACCOUNT_DAILY_LIMIT"] = str(settings.lottery_account_daily_limit)
    new_values["LOTTERY_ACTION_GAP_MIN_SECONDS"] = str(settings.lottery_action_gap_min_seconds)
    new_values["LOTTERY_ACTION_GAP_MAX_SECONDS"] = str(settings.lottery_action_gap_max_seconds)

    env_path = Path(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    # 读取现有 .env 文件（如果存在）
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    # 判断 .env 中原有 TARGET_GROUP 还是 TARGET_GROUPS
    has_target_group = False
    has_target_groups = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key == "TARGET_GROUP":
            has_target_group = True
        elif key == "TARGET_GROUPS":
            has_target_groups = True

    # 向后兼容：原 .env 只有 TARGET_GROUP 时更新 TARGET_GROUP
    use_target_group = has_target_group and not has_target_groups
    if use_target_group:
        if settings.target_groups:
            new_values["TARGET_GROUP"] = settings.target_groups[0]
    elif settings.target_groups:
        new_values["TARGET_GROUPS"] = ",".join(settings.target_groups)

    seen_keys: set[str] = set()
    output_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        # 保留空行和注释
        if not stripped or stripped.startswith("#"):
            output_lines.append(line)
            continue

        if "=" not in stripped:
            output_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in new_values:
            output_lines.append(f"{key}={new_values[key]}\n")
            seen_keys.add(key)
        else:
            output_lines.append(line)

    # 追加文件中不存在的键
    for key, value in new_values.items():
        if key not in seen_keys:
            output_lines.append(f"{key}={value}\n")

    # 先写入同目录临时文件，再原子替换；失败必须通知调用者。
    temp_path = env_path.with_suffix(f"{env_path.suffix}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.writelines(output_lines)
        os.replace(temp_path, env_path)
    except OSError as e:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.error("写入 .env 文件失败 (%s): %s", path, e)
        raise
