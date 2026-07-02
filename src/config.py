from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

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

    def __post_init__(self) -> None:
        """同步 target_group 与 target_groups 以保持向后兼容。"""
        if self.target_groups and not self.target_group:
            self.target_group = self.target_groups[0]
        elif self.target_group and not self.target_groups:
            self.target_groups = [self.target_group]


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


def load_settings() -> Settings:
    """从 .env 文件加载配置并校验。

    Returns:
        Settings 实例。

    Raises:
        ValueError: 配置缺失或无效时抛出。
    """
    load_dotenv()

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
    )


def save_settings(settings: Settings, path: str | None = None) -> None:
    """将 Settings 持久化到 .env 文件。

    Args:
        settings: Settings 实例。
        path: .env 文件路径，默认为项目根目录下的 .env。
    """
    if path is None:
        path = str(Path(__file__).resolve().parent.parent / ".env")

    # 构建新值字典
    new_values: dict[str, str] = {}
    new_values["API_ID"] = str(settings.api_id)
    new_values["API_HASH"] = settings.api_hash
    new_values["PHONE"] = settings.phone
    new_values["MIN_INTERVAL"] = str(settings.min_interval)
    new_values["MAX_INTERVAL"] = str(settings.max_interval)
    if settings.proxy_host is not None:
        new_values["PROXY_HOST"] = settings.proxy_host
    if settings.proxy_port is not None:
        new_values["PROXY_PORT"] = str(settings.proxy_port)
    if settings.proxy_type != "http":
        new_values["PROXY_TYPE"] = settings.proxy_type
    if settings.message_files:
        new_values["MESSAGE_FILES"] = ",".join(
            f"{g}|{p}" for g, p in settings.message_files.items()
        )
    if settings.ai_enabled:
        new_values["AI_ENABLED"] = "true"
    if settings.ai_api_key:
        new_values["AI_API_KEY"] = settings.ai_api_key
    new_values["AI_BASE_URL"] = settings.ai_base_url
    new_values["AI_MODEL"] = settings.ai_model
    if settings.ai_prompt:
        new_values["AI_PROMPT"] = settings.ai_prompt
    new_values["AI_CONTEXT_COUNT"] = str(settings.ai_context_count)
    if settings.ai_temperature != 0.7:
        new_values["AI_TEMPERATURE"] = str(settings.ai_temperature)
    if settings.ai_max_tokens != 500:
        new_values["AI_MAX_TOKENS"] = str(settings.ai_max_tokens)
    if settings.ai_timeout != 30:
        new_values["AI_TIMEOUT"] = str(settings.ai_timeout)

    if settings.schedule_enabled:
        new_values["SCHEDULE_ENABLED"] = "true"
    new_values["SCHEDULE_MORNING_START"] = settings.schedule_morning_start
    new_values["SCHEDULE_MORNING_END"] = settings.schedule_morning_end
    new_values["SCHEDULE_AFTERNOON_START"] = settings.schedule_afternoon_start
    new_values["SCHEDULE_AFTERNOON_END"] = settings.schedule_afternoon_end
    if settings.anti_detect:
        new_values["ANTI_DETECT"] = "true"
    if settings.typing_delay_min != 3:
        new_values["TYPING_DELAY_MIN"] = str(settings.typing_delay_min)
    if settings.typing_delay_max != 8:
        new_values["TYPING_DELAY_MAX"] = str(settings.typing_delay_max)
    if settings.thinking_delay_min != 5:
        new_values["THINKING_DELAY_MIN"] = str(settings.thinking_delay_min)
    if settings.thinking_delay_max != 25:
        new_values["THINKING_DELAY_MAX"] = str(settings.thinking_delay_max)
    if settings.skip_round_pct != 10:
        new_values["SKIP_ROUND_PCT"] = str(settings.skip_round_pct)
    if settings.group_gap_min != 1:
        new_values["GROUP_GAP_MIN"] = str(settings.group_gap_min)
    if settings.group_gap_max != 1:
        new_values["GROUP_GAP_MAX"] = str(settings.group_gap_max)

    env_path = Path(path)
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

    # 写入文件，失败时仅记录日志
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(output_lines)
    except OSError as e:
        logger.error("写入 .env 文件失败 (%s): %s", path, e)
