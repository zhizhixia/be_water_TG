from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from src.ai_client import AIClient
from src.ai_sender import AISender
from src.config import Settings, load_settings, save_settings
from src.group_parser import parse_group_links, validate_group_links
from src.sender import TelegramSender
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
                "api_hash": settings.api_hash,
                "phone": settings.phone,
                "target_groups": settings.target_groups,
                "min_interval": settings.min_interval,
                "max_interval": settings.max_interval,
                "proxy_host": settings.proxy_host,
                "proxy_port": settings.proxy_port,
                "proxy_type": settings.proxy_type,
                "message_files": settings.message_files,
                "ai_enabled": settings.ai_enabled,
                "ai_api_key": settings.ai_api_key,
                "ai_base_url": settings.ai_base_url,
                "ai_model": settings.ai_model,
                "ai_prompt": settings.ai_prompt,
                "ai_context_count": settings.ai_context_count,
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

        settings = Settings(
            api_id=int(data.get("api_id", 0)),
            api_hash=data.get("api_hash", ""),
            phone=data.get("phone", ""),
            target_groups=target_groups,
            min_interval=int(data.get("min_interval", 60)),
            max_interval=int(data.get("max_interval", 180)),
            proxy_host=data.get("proxy_host") or None,
            proxy_port=data.get("proxy_port") or None,
            proxy_type=data.get("proxy_type", "http"),
            message_files=data.get("message_files", {}),
            ai_enabled=bool(data.get("ai_enabled", False)),
            ai_api_key=data.get("ai_api_key", ""),
            ai_base_url=data.get("ai_base_url", "https://api.deepseek.com/v1"),
            ai_model=data.get("ai_model", "deepseek-chat"),
            ai_prompt=data.get("ai_prompt", ""),
            ai_context_count=int(data.get("ai_context_count", 5)),
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
            group_gap_min=1,
            group_gap_max=1,
        )
        save_settings(settings)
        return jsonify({"success": True})
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
        for g in settings.target_groups:
            group_file_map[g] = f"messages_{g.split('/')[-1]}.txt"

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
                except Exception:
                    # queue.Empty 等：发心跳保持连接
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
