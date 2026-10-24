"""确定性参与策略与抽奖白名单；本模块没有外部副作用。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from bewater.models import Action, Message, SHANGHAI

ADMIN_WARNING_TERMS = ("警告", "禁言", "违反群规", "停止发送", "移出群", "管理员提醒")


@dataclass(frozen=True)
class LotteryPlan:
    """人工拒绝也是完整结果，绝不临时猜测执行参数。"""

    action: Action | None
    reason: str
    deadline: datetime | None = None


def in_window(config: dict[str, object], at: datetime) -> bool:
    """两个上海工作窗口采用左闭右开边界。"""
    if not config["schedule_enabled"]:
        return True
    current = at.astimezone(SHANGHAI).strftime("%H:%M")
    return any(
        config[f"schedule_{period}_start"] <= current < config[f"schedule_{period}_end"]
        for period in ("morning", "afternoon")
    )


def classify(text: str) -> str:
    """AI 不可用时只响应有内容的明确问题或讨论。"""
    value = text.strip()
    if len(value) < 8 or re.search(r"https?://|t\.me/|抽奖|签到", value, re.I):
        return "irrelevant"
    return (
        "question"
        if re.search(r"[?？]|求助|怎么|如何|请教|求解决|有没有|能否", value)
        else "discussion"
    )


def safety(messages: list[Message], own_ids: set[int], admin_ids: set[int]) -> str:
    """只将明确指向本账号的投诉或管理员警告升级为安全暂停。"""
    for message in messages:
        if message.own:
            continue
        text = message.text.casefold()
        targeted = message.reply_to in own_ids or bool(
            re.match(r"(?:你|这个账号|这个号|别刷|不要刷)", text)
        )
        if targeted and any(
            word in text for word in ("别发", "不要再发", "刷屏", "打扰", "spam", "stop posting")
        ):
            return "收到指向本账号的明确投诉"
        if message.sender_id in admin_ids and any(word in text for word in ADMIN_WARNING_TERMS):
            return "收到管理员安全警告"
    return ""


def signature(message: Message) -> str:
    """指纹包含原文、按钮顺序和 callback 摘要，不含群号。"""
    parts = [" ".join(unicodedata.normalize("NFKC", message.text).split())]
    for row_index, row in enumerate(message.buttons):
        for column_index, button in enumerate(row):
            text = " ".join(unicodedata.normalize("NFKC", button.text).split())
            digest = hashlib.sha256(button.data).hexdigest()
            parts.append(f"{row_index}:{column_index}:{text}:{button.kind}:{digest}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def fixed_text_safe(text: str) -> bool:
    """只允许短固定文本，拒绝身份信息、链接与控制字符。"""
    if not text or len(text) > 32 or text != text.strip():
        return False
    if any(unicodedata.category(char).startswith("C") or char in "\r\n" for char in text):
        return False
    if sum(char.isdecimal() for char in text) >= 7:
        return False
    if re.search(r"https?://|www\.|t\.me|@|[\w.+-]+@[\w.-]+", text, re.I):
        return False
    return all(
        char.isalnum()
        or char.isspace()
        or char in "+-!！?？。，,.、:：;；()（）"
        or unicodedata.category(char) in {"So", "Sk"}
        for char in text
    )


def deadline(message: Message, at: datetime) -> tuple[datetime | None, str]:
    """无截止仅允许当前置顶或一小时增量；含糊截止不接受 AI 猜测。"""
    values = re.findall(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", message.text
    )
    if len(values) > 1:
        return None, "存在多个时间，无法确定唯一截止"
    if values:
        try:
            end = datetime.fromisoformat(values[0].replace("Z", "+00:00")).astimezone(SHANGHAI)
        except ValueError:
            return None, "截止时间无效"
        return (end, "已过截止时间") if end <= at else (end, "")
    if re.search(r"截止|结束于|今晚|明天|deadline|ends?\s", message.text, re.I):
        return None, "截止无法可靠验证，需人工检查"
    if (
        message.source == "pinned_today"
        and message.pinned
        and message.date.astimezone(SHANGHAI).date() == at.date()
    ):
        return None, ""
    if message.source == "incremental_recent" and at - timedelta(hours=1) <= message.date <= at:
        return None, ""
    return None, "无截止且不属于当日置顶或最近一小时增量"


def lottery_plan(message: Message, trust: str, at: datetime) -> LotteryPlan:
    """从原消息确定性解析一个白名单动作；未知条件整体转人工。"""
    if trust not in {"owner", "admin", "bot"}:
        return LotteryPlan(None, "发布者权限不可信或无法查询")
    end, error = deadline(message, at)
    if error:
        return LotteryPlan(None, error, end)
    text = message.text
    if re.search(
        r"https?://|t\.me/|@|关注|转发|私聊|投票|点赞|邀请|加群|加入|订阅|注册|下载|安装|支付|答题|验证码|评论|分享|提及|反应|手机号|邮箱|必须|需要|条件|要求|完成|务必|方可|先|再|请|subscribe|follow|forward|payment|before|after|must|required",
        text,
        re.I,
    ):
        return LotteryPlan(None, "包含额外或非白名单参与条件", end)
    callbacks = [(r, c, b) for r, row in enumerate(message.buttons) for c, b in enumerate(row)]
    if any(b.kind != "callback" for _, _, b in callbacks):
        return LotteryPlan(None, "包含外链或非回调按钮", end)
    fixed = re.findall(r'(回复|发送)(?:文本)?[：: ]*[“"]([^”"]+)[”"]', text)
    if fixed:
        if len(fixed) != 1 or callbacks or not fixed_text_safe(fixed[0][1]):
            return LotteryPlan(None, "文本方式不唯一或不安全", end)
        kind, phrase = fixed[0]
        return (
            LotteryPlan(
                Action("reply" if kind == "回复" else "text", message.id, phrase),
                "明确固定文本",
                end,
            )
            if _conditions_known(text)
            else LotteryPlan(None, "参与条件格式不完整，需人工检查", end)
        )
    matches = [
        (r, c) for r, c, b in callbacks if b.text.strip() in {"参与", "参加", "立即参与", "Join"}
    ]
    if len(matches) != 1 or len(callbacks) != 1:
        return LotteryPlan(None, "参与按钮不唯一", end)
    if not _conditions_known(text):
        return LotteryPlan(None, "参与条件格式不完整，需人工检查", end)
    return LotteryPlan(
        Action("callback", message.id, row=matches[0][0], column=matches[0][1]),
        "唯一明确参与回调",
        end,
    )


def _conditions_known(text: str) -> bool:
    """只允许简单公告行；自由叙述中可能藏有条件时拒绝自动执行。"""
    lines = [line.strip() for line in re.split(r"[\n;；]", text) if line.strip()]
    allowed = r'^(?:抽奖(?:活动)?[：:！!。 ]*|(?:奖品|奖项|开奖时间|截止时间|截止|结束于)[：: ].+|(?:参与方式[：: ]*)?(?:点击[“"]?(?:参与|参加|立即参与)[”"]?(?:按钮)?(?:参与抽奖)?[。！! ]*|(?:回复|发送)(?:文本)?[：: ]*[“"][^”"]+[”"](?:参与(?:抽奖)?)?[。！! ]*)|(?:点击按钮)?参与抽奖[。！! ]*)$'
    return bool(lines) and all(re.fullmatch(allowed, line) for line in lines)
