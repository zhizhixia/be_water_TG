from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def normalize_group_link(link: str) -> str:
    """标准化群组链接为统一的 https://t.me/ 格式。

    支持的输入格式:
      - @username
      - t.me/username
      - https://t.me/username

    Args:
        link: 原始群组链接。

    Returns:
        标准化后的群组链接。
    """
    link = link.strip()

    # @username → https://t.me/username
    if link.startswith("@"):
        return f"https://t.me/{link[1:]}"

    # t.me/username → https://t.me/username
    if link.startswith("t.me/"):
        return f"https://{link}"

    # https://t.me/username → 不变
    if link.startswith("https://t.me/"):
        return link

    logger.warning("无法识别的群组链接格式: %s", link)
    return link


def parse_group_links(raw: str) -> list[str]:
    """解析逗号分隔的群组链接字符串。

    支持英文逗号（,）和中文逗号（，）作为分隔符，
    自动去除空白、过滤空字符串、去重并标准化链接。

    Args:
        raw: 原始用户输入字符串。

    Returns:
        标准化后的群组链接列表（去重、保留顺序）。
    """
    if not raw:
        return []

    # 支持中文逗号
    raw = raw.replace("，", ",")
    parts = [part.strip() for part in raw.split(",")]
    parts = [part for part in parts if part]

    # 去重并保留顺序
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)

    return [normalize_group_link(link) for link in unique]


def validate_group_links(links: list[str]) -> None:
    """校验群组链接列表是否有效。

    Args:
        links: 群组链接列表。

    Raises:
        ValueError: 列表为空或包含无效链接时抛出。
    """
    if not links:
        raise ValueError("至少需要一个目标群组")

    pattern = re.compile(r"^https://t\.me/\S+$")
    for link in links:
        if not pattern.match(link):
            raise ValueError(f"无效的群组链接: {link}")
        if "/+" in link:
            logger.warning(
                "可能是私密邀请链接，可能无法通过用户账号访问: %s", link
            )
