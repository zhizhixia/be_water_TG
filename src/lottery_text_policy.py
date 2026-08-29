from __future__ import annotations

import re
import unicodedata


_EXTERNAL_PATTERN = re.compile(
    r"(?:https?://|www\.|@|\+?\d[\d -]{6,}|[\w.+-]+@[\w.-]+)",
    re.IGNORECASE,
)
_ALLOWED_CATEGORIES = {"L", "N", "P", "Z", "S", "M"}


def is_safe_fixed_text(text: str) -> bool:
    """只允许最多 32 个字符的本地固定文本，拒绝外联和身份信息。"""
    normalized = unicodedata.normalize("NFKC", text)
    if not normalized or len(normalized) > 32 or normalized != normalized.strip():
        return False
    if any(character in {"\n", "\r", "\u2028", "\u2029"} for character in normalized):
        return False
    if _EXTERNAL_PATTERN.search(normalized):
        return False
    return all(unicodedata.category(character)[0] in _ALLOWED_CATEGORIES for character in normalized)
