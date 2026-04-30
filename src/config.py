from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    """项目配置数据类。"""

    api_id: int
    api_hash: str
    phone: str
    target_group: str
    min_interval: int = 60
    max_interval: int = 180
    proxy_host: str | None = None
    proxy_port: int | None = None


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
    target_group = os.getenv("TARGET_GROUP")

    if not api_id_str:
        raise ValueError("API_ID is required in .env file")
    if not api_hash:
        raise ValueError("API_HASH is required in .env file")
    if not phone:
        raise ValueError("PHONE is required in .env file")
    if not target_group:
        raise ValueError("TARGET_GROUP is required in .env file")

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

    proxy_port = int(proxy_port_str) if proxy_port_str else None

    if proxy_host and not proxy_port:
        raise ValueError("PROXY_HOST is set but PROXY_PORT is missing")
    if proxy_port and not proxy_host:
        raise ValueError("PROXY_PORT is set but PROXY_HOST is missing")

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        target_group=target_group,
        min_interval=min_interval,
        max_interval=max_interval,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
    )
