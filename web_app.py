from __future__ import annotations

import json
import logging
import os
import queue
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from src.ai_client import AIClient
from src.ai_sender import AISender
from src.config import (
    Settings,
    SettingsValidationError,
    load_settings,
    save_settings,
    validate_settings_for_save,
)
from src.group_parser import parse_group_links, validate_group_links
from src.sender import TelegramSender
from src.lottery_store import LotteryStore
from src.state_store import StateStore
from ui.message_manager import MessageManager
from web_manager import LogQueueHandler, SendLoopManager

logger = logging.getLogger(__name__)

app = Flask(__name__)

# 全局发送管理器
manager = SendLoopManager()


# ===== 页面路由 =====

@app.route("/")
def index():
    return render_template("index.html")


# ===== 配置 API =====

@app.route("/api/config", methods=["GET"])
def api_config_get():
    """从 .env 加载配置并返回 JSON"""
    try:
        settings = load_settings()
        return jsonify({
            "success": True,
            "config": {
                "api_id": settings.api_id,
                "api_hash": "",
                "api_hash_set": bool(settings.api_hash),
                "phone": settings.phone,
                "target_groups": settings.target_groups,
                "min_interval": settings.min_interval,
                "max_interval": settings.max_interval,
                "proxy_host": settings.proxy_host,
                "proxy_port": settings.proxy_port,
                "proxy_type": settings.proxy_type,
                "message_files": settings.message_files,
                "ai_enabled": settings.ai_enabled,
                "ai_api_key": "",
                "ai_api_key_set": bool(settings.ai_api_key),
                "ai_base_url": settings.ai_base_url,
                "ai_model": settings.ai_model,
                "ai_prompt": settings.ai_prompt,
                "ai_context_count": settings.ai_context_count,
                "ai_temperature": settings.ai_temperature,
                "ai_max_tokens": settings.ai_max_tokens,
                "ai_timeout": settings.ai_timeout,
                "schedule_enabled": settings.schedule_enabled,
                "schedule_morning_start": settings.schedule_morning_start,
                "schedule_morning_end": settings.schedule_morning_end,
                "schedule_afternoon_start": settings.schedule_afternoon_start,
                "schedule_afternoon_end": settings.schedule_afternoon_end,
                "anti_detect": settings.anti_detect,
                "typing_delay_min": settings.typing_delay_min,
                "typing_delay_max": settings.typing_delay_max,
                "thinking_delay_min": settings.thinking_delay_min,
                "thinking_delay_max": settings.thinking_delay_max,
                "skip_round_pct": settings.skip_round_pct,
                "group_gap_min": settings.group_gap_min,
                "group_gap_max": settings.group_gap_max,
                "daily_limit": settings.daily_limit,
                "idle_threshold_minutes": settings.idle_threshold_minutes,
                "question_reply_pct": settings.question_reply_pct,
                "discussion_reply_pct": settings.discussion_reply_pct,
                "reply_delay_min": settings.reply_delay_min,
                "reply_delay_max": settings.reply_delay_max,
            },
        })
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/api/config", methods=["POST"])
def api_config_save():
    """保存配置到 .env"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "无效的请求数据"}), 400

        # 构建 Settings 对象
        target_groups = data.get("target_groups", [])
        if isinstance(target_groups, str):
            target_groups = parse_group_links(target_groups)
        elif isinstance(target_groups, list):
            target_groups = parse_group_links(",".join(str(item) for item in target_groups))
        else:
            raise SettingsValidationError("target_groups", "Target groups must be a list or string")
        try:
            validate_group_links(target_groups)
        except ValueError as ex:
            raise SettingsValidationError("target_groups", str(ex)) from ex

        try:
            current = load_settings()
        except Exception:
            current = None
        raw_message_files = data.get("message_files", {})
        if not isinstance(raw_message_files, dict):
            raise SettingsValidationError(
                "message_files", "Message files must be a group-to-path mapping"
            )
        settings = Settings(
            api_id=int(data.get("api_id", 0)),
            api_hash=data.get("api_hash") or (current.api_hash if current else ""),
            phone=data.get("phone", ""),
            target_groups=target_groups,
            min_interval=int(data.get("min_interval", 60)),
            max_interval=int(data.get("max_interval", 180)),
            proxy_host=data.get("proxy_host") or None,
            proxy_port=data.get("proxy_port") or None,
            proxy_type=data.get("proxy_type", "http"),
            message_files=raw_message_files,
            ai_enabled=bool(data.get("ai_enabled", False)),
            ai_api_key=(
                data.get("ai_api_key")
                or (current.ai_api_key if current and data.get("ai_enabled", False) else "")
            ),
            ai_base_url=data.get("ai_base_url", "https://api.deepseek.com/v1"),
            ai_model=data.get("ai_model", "deepseek-chat"),
            ai_prompt=data.get("ai_prompt", ""),
            ai_context_count=int(data.get("ai_context_count", 5)),
            ai_temperature=float(data.get("ai_temperature", 0.7)),
            ai_max_tokens=int(data.get("ai_max_tokens", 500)),
            ai_timeout=int(data.get("ai_timeout", 30)),
            schedule_enabled=bool(data.get("schedule_enabled", False)),
            schedule_morning_start=data.get("schedule_morning_start", "08:00"),
            schedule_morning_end=data.get("schedule_morning_end", "11:00"),
            schedule_afternoon_start=data.get("schedule_afternoon_start", "14:00"),
            schedule_afternoon_end=data.get("schedule_afternoon_end", "18:00"),
            anti_detect=bool(data.get("anti_detect", False)),
            typing_delay_min=int(data.get("typing_delay_min", 3)),
            typing_delay_max=int(data.get("typing_delay_max", 8)),
            thinking_delay_min=int(data.get("thinking_delay_min", 5)),
            thinking_delay_max=int(data.get("thinking_delay_max", 25)),
            skip_round_pct=int(data.get("skip_round_pct", 10)),
            group_gap_min=int(data.get("group_gap_min", 1)),
            group_gap_max=int(data.get("group_gap_max", 1)),
            daily_limit=int(data.get("daily_limit", 30)),
            idle_threshold_minutes=int(data.get("idle_threshold_minutes", 10)),
            question_reply_pct=int(data.get("question_reply_pct", 70)),
            discussion_reply_pct=int(data.get("discussion_reply_pct", 15)),
            reply_delay_min=int(data.get("reply_delay_min", 20)),
            reply_delay_max=int(data.get("reply_delay_max", 90)),
            state_db_path=(current.state_db_path if current else "state/be_water.db"),
        )
        validate_settings_for_save(settings)
        save_settings(settings)
        return jsonify({"success": True})
    except SettingsValidationError as ex:
        return jsonify({"success": False, "error": str(ex), "field": ex.field}), 422
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400


# ===== 控制 API =====

@app.route("/api/start", methods=["POST"])
def api_start():
    """启动发送循环。先校验群组链接，再经 manager.start 进入 STARTING。"""
    try:
        settings = load_settings()
    except ValueError as ex:
        # load_settings 在 target_groups 为空时抛 ValueError，视为校验失败 → 422
        return jsonify({"success": False, "detail": str(ex)}), 422
    except Exception as ex:
        return jsonify({"success": False, "error": f"加载配置失败: {ex}"}), 400

    # 前置校验：群组链接无效则直接返回 422，避免启动后才失败
    try:
        validate_group_links(settings.target_groups)
    except ValueError as ex:
        return jsonify({"success": False, "detail": str(ex)}), 422

    # 构建 MessageManager
    group_file_map = {}
    if settings.message_files:
        group_file_map = settings.message_files
    else:
        shared_fallback = Path("messages.txt")
        for g in settings.target_groups:
            group_file_map[g] = (
                str(shared_fallback)
                if shared_fallback.exists()
                else f"messages_{g.split('/')[-1]}.txt"
            )

    try:
        message_manager = MessageManager(group_file_map)
    except Exception as ex:
        return jsonify({"success": False, "error": f"加载消息文件失败: {ex}"}), 400

    sender = TelegramSender(settings)

    ai_sender = None
    if settings.ai_enabled and settings.ai_api_key:
        ai_client = AIClient(
            api_key=settings.ai_api_key,
            base_url=settings.ai_base_url,
            model=settings.ai_model,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout=settings.ai_timeout,
        )
        ai_sender = AISender(sender, ai_client)

    result = manager.start(sender, settings, message_manager, ai_sender)
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    result = manager.pause()
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    result = manager.resume()
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    result = manager.stop()
    if not result.ok:
        return jsonify({"success": False, "error": result.reason}), 409
    return jsonify({"success": True})


@app.route("/api/dashboard")
def api_dashboard():
    """Return a durable runtime snapshot for the overview page."""
    snapshot = manager.dashboard_snapshot()
    if not snapshot["groups"]:
        try:
            settings = load_settings()
            store = StateStore(settings.state_db_path)
            groups = [
                StateStore.serialize_state(item)
                for item in store.list_group_states(settings.target_groups)
            ]
            snapshot["groups"] = groups
            snapshot["alerts"] = [item for item in groups if item["paused"]]
            snapshot["total"] = sum(item["sent_count"] for item in groups)
            snapshot["per_group"] = {
                item["group"]: item["sent_count"] for item in groups
            }
        except Exception as ex:
            return jsonify({"success": False, "error": str(ex)}), 400
    try:
        settings = load_settings()
        alerts, total = LotteryStore(settings.state_db_path).list_alerts(limit=50, offset=0)
        snapshot["lottery_alerts"] = [_lottery_alert_payload(item) for item in alerts]
        snapshot["lottery_alert_total"] = total
    except Exception:
        snapshot["lottery_alerts"] = []
        snapshot["lottery_alert_total"] = 0
    return jsonify({"success": True, "dashboard": snapshot})


def _lottery_alert_payload(item):
    """将仓储实体转换为不包含 Telegram 回调和凭据的浏览器摘要。"""
    return {
        "id": item.id,
        "peer_id": item.peer_id,
        "message_id": item.message_id,
        "status": item.participation_status.value,
        "summary": item.summary,
    }


def _lottery_open_url(peer_id: int, message_id: int) -> tuple[str, bool]:
    """返回 Telegram 消息链接；未知公开用户名时降级为私有群链接。"""
    return f"https://t.me/c/{peer_id}/{message_id}", True


@app.route("/api/lotteries", methods=["GET"])
def api_lottery_list():
    """分页读取待处理抽奖提醒，不改变提醒状态或冻结状态。"""
    try:
        offset = max(0, int(request.args.get("offset", "0")))
        limit = min(50, max(1, int(request.args.get("limit", "50"))))
    except ValueError:
        return jsonify({"success": False, "error": "分页参数必须是整数"}), 400
    try:
        settings = load_settings()
        alerts, total = LotteryStore(settings.state_db_path).list_alerts(limit=limit, offset=offset)
        items = [_lottery_alert_payload(item) for item in alerts]
        return jsonify({"success": True, "alerts": items, "items": items, "total": total, "offset": offset, "limit": limit})
    except Exception as ex:
        return jsonify({"success": False, "alerts": [], "items": [], "total": 0, "offset": offset, "limit": limit, "error": str(ex)}), 400


@app.route("/api/lotteries/<int:lottery_id>/open", methods=["POST"])
def api_lottery_open(lottery_id: int):
    """原子关闭抽奖提醒；未知结果时同时解除该群抽奖冻结。"""
    try:
        item = LotteryStore(load_settings().state_db_path).open_alert(lottery_id)
        if item is None:
            return jsonify({"success": False, "error": "未找到抽奖提醒"}), 404
        url, fallback = _lottery_open_url(item.peer_id, item.message_id)
        return jsonify({"success": True, "url": url, "fallback": fallback, "message_id": item.message_id, "lottery": _lottery_alert_payload(item)})
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/api/lotteries/cleanup", methods=["POST"])
def api_lottery_cleanup():
    """仅在空闲或已停止状态、收到中文二次确认后清理历史。"""
    if manager.state.value not in {"idle", "stopped"}:
        return jsonify({"success": False, "error": "请先停止自动任务后再清理抽奖历史"}), 409
    data = request.get_json(silent=True) or {}
    cutoff = str(data.get("before", "")).strip()
    if "confirm" in data and data.get("confirm") != "确认清理":
        return jsonify({"success": False, "error": "请输入“确认清理”进行二次确认"}), 400
    try:
        from datetime import date
        date.fromisoformat(cutoff)
        if manager.state.value not in {"idle", "stopped"}:
            return jsonify({"success": False, "error": "自动任务状态已变化，请重新确认"}), 409
        deleted = LotteryStore(load_settings().state_db_path).cleanup_before(cutoff)
        return jsonify({"success": True, "deleted": deleted})
    except ValueError:
        return jsonify({"success": False, "error": "清理日期必须是 ISO 日期"}), 400
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/api/audit")
def api_audit():
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100
    events = manager.audit_events(limit)
    if not events:
        try:
            settings = load_settings()
            events = [
                {
                    "seq": event.seq,
                    "occurred_at": event.occurred_at,
                    "group": event.group,
                    "event_type": event.event_type,
                    "reason": event.reason,
                    "metadata": event.metadata,
                }
                for event in StateStore(settings.state_db_path).list_audit(limit)
            ]
        except Exception:
            events = []
    return jsonify({"success": True, "events": events})


@app.route("/api/groups/resume", methods=["POST"])
def api_group_resume():
    data = request.get_json(silent=True) or {}
    group = str(data.get("group", "")).strip()
    if not group:
        return jsonify({"success": False, "error": "group is required"}), 400
    try:
        state = manager.resume_group(group)
    except RuntimeError:
        try:
            settings = load_settings()
            state = StateStore.serialize_state(
                StateStore(settings.state_db_path).resume_group(group)
            )
        except Exception as ex:
            return jsonify({"success": False, "error": str(ex)}), 409
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 409
    return jsonify({"success": True, "group": state})


# ===== SSE 端点 =====

@app.route("/api/events")
def api_events():
    """SSE 端点：推送实时日志、状态、计数器。

    支持 Last-Event-ID 请求头与 ?last_event_id= 查询参数，
    浏览器重连或刷新后可从断点续推历史事件。
    """
    # 头部优先级低于查询参数：查询参数显式时优先
    last_id_header = request.headers.get("Last-Event-ID", "")
    last_id_query = request.args.get("last_event_id", "")
    raw = last_id_query or last_id_header or "0"
    try:
        last_seq = int(raw)
    except (TypeError, ValueError):
        last_seq = 0

    def generate():
        event_bus = manager.event_bus
        q = event_bus.subscribe(last_seq=last_seq)
        # 推送当前状态作为首条（用 id: 0 占位，避免与真实历史 seq 冲突）
        state = event_bus.get_current_state()
        yield f"id: 0\ndata: {json.dumps({'type': 'status', 'data': {'state': state}})}\n\n"
        try:
            while True:
                try:
                    seq, data = q.get(timeout=30)
                except queue.Empty:
                    # 队列空超时：发心跳保持连接
                    yield ": heartbeat\n\n"
                    continue
                yield f"id: {seq}\ndata: {json.dumps(data)}\n\n"
        finally:
            # 客户端断开 / generator 被 GC 时触发，保证不泄漏订阅者
            event_bus.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ===== 验证码 API =====

@app.route("/api/code", methods=["POST"])
def api_code():
    """提交 Telegram 验证码。submit_code 在无 future 或已超时时返回 False → 409。"""
    data = request.get_json()
    if not data or not data.get("code"):
        return jsonify({"success": False, "error": "验证码不能为空"}), 400
    if not manager.submit_code(str(data["code"]).strip()):
        return jsonify({"success": False, "error": "当前无验证码请求或已超时"}), 409
    return jsonify({"success": True})


# ===== 应用初始化 =====

def create_app() -> Flask:
    """工厂函数，由 main.py 导入使用"""
    # 注册 LogQueueHandler 到根 logger
    root_logger = logging.getLogger()
    root_logger.addHandler(LogQueueHandler(manager.event_bus))
    logger.info("Web UI 已启动 - LogQueueHandler 已注册")
    return app
