"""中文本地控制台 API：配置、控制、持久化快照、消息库与提醒。"""

from __future__ import annotations
from datetime import date
import hashlib
import json
import secrets
import sqlite3
import time
from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context
from telethon import types, utils
from bewater.config import ConfigStore, validate
from bewater.content import Messages
from bewater.models import DomainError, Paths, VERSION, now
from bewater.runtime import Controller
from bewater.store import Store


class Web:
    """薄网页控制层；业务由控制器与存储负责。"""

    def __init__(self, paths, telegram_factory, migration_status):
        """显式持有依赖，不用四百行工厂闭包隐藏状态。"""
        self.paths = paths
        self.config = ConfigStore(paths)
        self.migration_status = migration_status
        self.store = Store(paths.database) if migration_status == "ready" else None
        self.controller = Controller(paths, self.config, self.store, telegram_factory)
        self.libraries = Messages(paths)

    def protect(self):
        """回环绑定之外，校验 Host/Origin 与每会话写请求标识。"""
        host = request.host.split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost"}:
            raise DomainError("本地服务不接受该 Host", status=403)
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            raise DomainError("拒绝跨站访问", status=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = session.get("csrf")
            if not token or not secrets.compare_digest(
                token, request.headers.get("X-CSRF-Token", "")
            ):
                raise DomainError("页面会话已失效，请刷新后重试", status=403)
            if not request.is_json:
                raise DomainError("请求必须是 JSON", status=415)
        if (
            self.migration_status != "ready"
            and request.path.startswith("/api/")
            and (
                request.path
                not in {"/api/health", "/api/bootstrap", "/api/snapshot", "/api/events"}
            )
        ):
            raise DomainError("旧数据迁移尚未就绪，请先处理迁移阻断", status=409)

    def domain_error(self, exc):
        """只返回可安全显示的字段级错误。"""
        return (jsonify(error=str(exc), field=exc.field), exc.status)

    def unexpected_error(self, exc):
        """异常不泄露配置、路径、凭据或原始网络响应。"""
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return (jsonify(error="请求格式不正确或资源不存在"), exc.code)
        if isinstance(exc, sqlite3.DatabaseError):
            return (jsonify(error="数据库暂时不可用，已拒绝本次操作"), 503)
        return (jsonify(error="操作失败，请检查运行状态后重试"), 500)

    def headers(self, response):
        """本地页面不允许被外站嵌入，敏感响应不缓存。"""
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def index(self):
        """加载完整控制台，无账号副作用。"""
        return render_template("index.html")

    def health(self):
        """公开健康检查只返回非敏感固定状态。"""
        configured = False
        try:
            values, _ = self.config.load()
            validate(values, start=True)
            configured = True
        except DomainError:
            configured = False
        return jsonify(app="BeWaterTG", version=VERSION, configured=configured)

    def bootstrap(self):
        """只在同源页面取得会话标识和脱敏初始配置。"""
        session.setdefault("csrf", secrets.token_hex(32))
        return jsonify(
            csrf=session["csrf"],
            config=self.config.public(),
            snapshot=self.snapshot(),
            migration=self.migration_status,
            upgrade=self.upgrade_report(),
            version=VERSION,
        )

    def upgrade_report(self) -> dict[str, int]:
        """只展示升级计数，不输出旧配置或任何原始路径。"""
        path = self.paths.backups_dir / "v2-upgrade-report.json"
        if not path.exists():
            return {}
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            return {
                key: value
                for key, value in report.items()
                if key in {"copied_messages", "message_conflicts", "message_manual"}
                and type(value) is int
            }
        except (OSError, ValueError, AttributeError):
            return {"message_manual": 1}

    def snapshot(self) -> dict[str, object]:
        """状态按稳定群身份和上海自然日取值，不用会话累计冒充今日总量。"""
        result = {
            **self.controller.status(),
            "groups": [],
            "today": now().date().isoformat(),
            "counts": {"chat": 0, "checkin": 0, "lottery": 0, "unknown": 0},
        }
        if self.store is None:
            return result
        values, revision = self.config.load()
        result["pending_config"] = bool(
            self.controller.thread and revision != self.controller.active_revision
        )
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT kind,status,COUNT(*) n FROM bw_action WHERE day=? GROUP BY kind,status",
                (result["today"],),
            ).fetchall()
            for row in rows:
                if row["kind"] == "lottery" and row["status"] in {
                    "SENT",
                    "CALLING",
                    "UNKNOWN",
                    "FAILED",
                }:
                    result["counts"]["lottery"] += row["n"]
                elif row["status"] == "SENT":
                    result["counts"][row["kind"]] += row["n"]
                if row["status"] == "UNKNOWN":
                    result["counts"]["unknown"] += row["n"]
            for group in values["groups"]:
                saved = db.execute(
                    "SELECT g.* FROM bw_group g JOIN bw_alias a ON a.peer=g.peer WHERE a.ref=?",
                    (group["ref"],),
                ).fetchone()
                item = {
                    **group,
                    "title": group["ref"],
                    "peer": None,
                    "state": {},
                    "text_used": 0,
                    "lottery_used": 0,
                    "frozen": False,
                    "scan": {},
                }
                if saved:
                    peer = int(saved["peer"])
                    item.update(
                        title=saved["title"],
                        peer=peer,
                        state=json.loads(saved["state"]),
                        text_used=self.store.quota(result["today"], peer),
                        lottery_used=self.store.quota(result["today"], peer, "lottery"),
                        frozen=self.store.frozen(peer),
                        scan={
                            **self.store.scan(peer),
                            "next_scan": json.loads(saved["state"]).get("scan_next"),
                            "found": db.execute(
                                "SELECT COUNT(*) FROM bw_lottery WHERE peer=?", (peer,)
                            ).fetchone()[0],
                        },
                    )
                result["groups"].append(item)
        return result

    def get_snapshot(self):
        """浏览器刷新和 SSE 重连均从完整快照恢复。"""
        return jsonify(self.snapshot())

    def configuration(self):
        """完整读写与版本保护，敏感范围变化必须先停止。"""
        if request.method == "GET":
            return jsonify(self.config.public())
        data = json_body()
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise DomainError("配置 patch 必须是对象")
        with self.controller.lock:
            self.guard_configuration(patch)
            result = self.config.update(patch, str(data.get("revision", "")))
        return jsonify(result)

    def guard_configuration(self, patch: dict[str, object]) -> None:
        """与开始操作共用锁，检查和配置提交之间不允许并发启动。"""
        current, _ = self.config.load()
        if not self.controller.thread:
            return
        protected = {"api_id", "api_hash", "phone", "proxy_host", "proxy_port", "proxy_type"}
        if any(
            key in patch
            and patch[key] != current[key]
            and not (key == "api_hash" and patch[key] == "")
            for key in protected
        ):
            raise DomainError("修改账号或代理前请先停止任务", status=409)
        if "clear_secrets" in patch:
            raise DomainError("清除密钥前请先停止任务", status=409)
        groups = patch.get("groups", current["groups"])
        if not isinstance(groups, list) or not all(isinstance(g, dict) for g in groups):
            raise DomainError("群组配置必须是对象列表", "groups")
        if [g.get("ref") for g in groups] != [g["ref"] for g in current["groups"]]:
            raise DomainError("增删授权群前请先停止任务", status=409)

    def control(self, action):
        """控制真实控制器而非前端本地状态。"""
        if action == "start":
            self.controller.start()
        elif action in {"pause", "resume", "stop"}:
            self.controller.control(action)
        else:
            raise DomainError("未知控制操作", status=404)
        return jsonify(self.controller.status())

    def authenticate(self):
        """认证输入仅传给当前挑战，不入日志或数据库。"""
        data = json_body()
        answer = data.get("answer")
        if not isinstance(answer, str) or not 1 <= len(answer) <= 200:
            raise DomainError("请输入有效认证内容")
        self.controller.submit(str(data.get("id", "")), answer)
        return jsonify(ok=True)

    def messages(self, name):
        """消息库查看、编辑和旧格式预览共用受控路径。"""
        if request.method == "GET":
            return jsonify(self.libraries.load(name))
        data = json_body()
        return jsonify(self.libraries.save(name, data.get("text"), str(data.get("revision", ""))))

    def records(self, kind):
        """提醒、历史和审计均支持服务端分页。"""
        table = {"lotteries": "bw_lottery", "audit": "bw_audit", "actions": "bw_action"}.get(kind)
        if not table:
            raise DomainError("未知列表", status=404)
        offset, limit = pagination()
        return jsonify(
            self.store.listings(table, offset, limit, request.args.get("pending") == "true")
        )

    def open_lottery(self, record_id):
        """只生成安全消息链接，不改变提醒、冻结或账本状态。"""
        record = self.store.get_lottery(record_id)
        with self.store.connect() as db:
            group = db.execute("SELECT * FROM bw_group WHERE peer=?", (record["peer"],)).fetchone()
        if not group:
            raise DomainError("该群身份未知，不能安全构造链接", status=409)
        raw, peer_type = utils.resolve_id(record["peer"])
        if group["username"]:
            url = f"https://t.me/{group['username']}/{record['message_id']}"
        elif peer_type is types.PeerChannel:
            url = f"https://t.me/c/{raw}/{record['message_id']}"
        else:
            url = group["ref"]
        return jsonify(
            url=url, message_id=record["message_id"], frozen=self.store.frozen(record["peer"])
        )

    def mark_lottery_viewed(self, record_id):
        """人工确认已查看只关闭提醒，不解冻、不改账本或额度。"""
        if json_body().get("confirm") is not True:
            raise DomainError("需要明确确认已查看")
        self.store.get_lottery(record_id)
        self.store.mark_viewed(record_id)
        return jsonify(ok=True)

    def pause_group(self, peer):
        """人工暂停该群全部自动外发，不影响其他群，也不重置额度。"""
        self.store.set_state(peer, pause="manual", pause_reason="人工暂停该群全部自动外发")
        self.store.audit(peer, "manual_pause", "人工暂停该群全部自动外发")
        self.controller.events.emit("warning", "已人工暂停该群全部自动外发", peer)
        return jsonify(ok=True)

    def resume_group(self, peer):
        """安全暂停与抽奖冻结分开确认，不能清除已用额度。"""
        data = json_body()
        if data.get("confirm") != "确认恢复":
            raise DomainError("请输入“确认恢复”")
        if data.get("kind") == "lottery":
            self.store.unfreeze(peer)
        elif data.get("kind") == "safety":
            self.store.set_state(peer, pause="", pause_reason="")
            self.store.audit(peer, "safety_resumed", "人工确认恢复")
        else:
            raise DomainError("恢复类别无效")
        return jsonify(ok=True)

    def cleanup(self):
        """预览与执行都拒绝运行态，最终操作二次确认。"""
        data = json_body()
        try:
            before = date.fromisoformat(str(data.get("before", ""))).isoformat()
        except ValueError:
            raise DomainError("清理日期必须是 YYYY-MM-DD")
        preview = data.get("preview") is True
        if not preview and data.get("confirm") != "确认清理":
            raise DomainError("请输入“确认清理”")
        return jsonify(self.controller.cleanup(before, preview))

    def events(self):
        """代际与序号可恢复的有限事件流，不把认证内容放入事件。"""
        raw = request.headers.get("Last-Event-ID") or request.args.get("cursor", "")
        generation, _, value = raw.partition(":")
        cursor = (
            int(value) if generation == self.controller.events.generation and value.isdigit() else 0
        )

        @stream_with_context
        def generate():
            """客户端断开直接结束生成器，不遗留订阅队列。"""
            nonlocal cursor
            yield (
                "event: snapshot\ndata: " + json.dumps(self.snapshot(), ensure_ascii=False) + "\n\n"
            )
            while True:
                for item in self.controller.events.since(cursor):
                    cursor = item["id"]
                    yield f"id: {self.controller.events.generation}:{cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                yield ": heartbeat\n\n"
                time.sleep(1)

        return Response(
            generate(), mimetype="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )


def create_app(paths: Paths, *, telegram_factory=None, migration_status: str = "ready") -> Flask:
    """装配路由与安全钩子；构造不连接 Telegram。"""
    paths.prepare()
    app = Flask(
        __name__,
        template_folder=str(paths.resources / "templates"),
        static_folder=str(paths.resources / "static"),
    )
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_NAME="bewater_"
        + hashlib.sha256(str(paths.data.resolve()).encode()).hexdigest()[:12],
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    web = Web(paths, telegram_factory, migration_status)
    app.extensions.update(
        controller=web.controller, config_store=web.config, store=web.store, paths=paths
    )
    app.before_request(web.protect)
    app.after_request(web.headers)
    app.register_error_handler(DomainError, web.domain_error)
    app.register_error_handler(Exception, web.unexpected_error)
    app.add_url_rule("/", "index", web.index, methods=["GET"])
    app.add_url_rule("/api/health", "health", web.health, methods=["GET"])
    app.add_url_rule("/api/bootstrap", "bootstrap", web.bootstrap, methods=["GET"])
    app.add_url_rule("/api/snapshot", "get_snapshot", web.get_snapshot, methods=["GET"])
    app.add_url_rule("/api/config", "configuration", web.configuration, methods=["GET", "POST"])
    app.add_url_rule("/api/control/<action>", "control", web.control, methods=["POST"])
    app.add_url_rule("/api/auth", "authenticate", web.authenticate, methods=["POST"])
    app.add_url_rule("/api/messages/<name>", "messages", web.messages, methods=["GET", "POST"])
    app.add_url_rule("/api/records/<kind>", "records", web.records, methods=["GET"])
    app.add_url_rule(
        "/api/lotteries/<int:record_id>/open", "open_lottery", web.open_lottery, methods=["POST"]
    )
    app.add_url_rule(
        "/api/lotteries/<int:record_id>/viewed",
        "mark_lottery_viewed",
        web.mark_lottery_viewed,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/groups/<int(signed=True):peer>/resume",
        "resume_group",
        web.resume_group,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/groups/<int(signed=True):peer>/pause",
        "pause_group",
        web.pause_group,
        methods=["POST"],
    )
    app.add_url_rule("/api/cleanup", "cleanup", web.cleanup, methods=["POST"])
    app.add_url_rule("/api/events", "events", web.events, methods=["GET"])
    return app


def json_body() -> dict[str, object]:
    """所有写接口都拒绝数组和空解析结果。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DomainError("JSON 请求必须是对象")
    return data


def pagination() -> tuple[int, int]:
    """拒绝非法分页，不隐式吞掉格式错误。"""
    try:
        offset, limit = (int(request.args.get("offset", "0")), int(request.args.get("limit", "50")))
    except ValueError:
        raise DomainError("分页参数必须是整数")
    if offset < 0 or not 1 <= limit <= 100:
        raise DomainError("分页范围无效")
    return (offset, limit)
