"""任务生命周期、认证挑战、事件回放和系统级互斥。"""

from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import logging
import os
from pathlib import Path
import secrets
import threading

from bewater.config import ConfigStore, validate
from bewater.models import DomainError, Paths, now
from bewater.store import Store


class FileLock:
    """由操作系统持有字节锁，进程退出自动释放，不猜测 PID 是否存活。"""

    def __init__(self, path: Path) -> None:
        """保存锁路径，不提前改变运行状态。"""
        self.path = path
        self.stream = None

    def acquire(self) -> bool:
        """非阻塞独占锁。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(self.path, "a+b")
        try:
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self) -> None:
        """关闭句柄自动释放，不删除其他进程可能已打开的锁文件。"""
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class Events:
    """有限事件环和进程代际，服务重启后客户端必须重新获取快照。"""

    def __init__(self) -> None:
        """创建线程安全事件存储。"""
        self.generation = secrets.token_hex(8)
        self.seq = 0
        self.items = deque(maxlen=300)
        self.lock = threading.RLock()

    def emit(self, kind: str, message: str, peer: int | None = None) -> None:
        """只广播业务固定摘要，不收集原始异常、消息或凭据。"""
        logging.getLogger("bewater").info("运行事件[%s]：%s", kind, message)
        with self.lock:
            self.seq += 1
            self.items.append(
                {
                    "id": self.seq,
                    "type": kind,
                    "message": message,
                    "peer": peer,
                    "at": now().isoformat(),
                }
            )

    def since(self, cursor: int) -> list[dict[str, object]]:
        """复制事件，慢客户端不会持有后台资源。"""
        with self.lock:
            return [dict(item) for item in self.items if item["id"] > cursor]


class Controller:
    """网页线程只改变控制状态，asyncio 后台是唯一业务执行者。"""

    def __init__(
        self, paths: Paths, config: ConfigStore, store: Store, telegram_factory=None
    ) -> None:
        """注入外部边界，测试可替换为匿名适配器。"""
        self.paths, self.config, self.store = paths, config, store
        self.telegram_factory = telegram_factory
        self.events = Events()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.engine = None
        self.state = "stopped"
        self.error = ""
        self.active_revision = ""
        self.challenge: dict[str, str] | None = None
        self._answer: asyncio.Future | None = None

    def status(self) -> dict[str, object]:
        """返回不含认证输入的生命周期快照。"""
        with self.lock:
            return {
                "state": self.state,
                "error": self.error,
                "challenge": self.challenge,
                "active_revision": self.active_revision,
                "generation": self.events.generation,
            }

    def start(self) -> None:
        """校验后启动一个后台线程；重复开始直接拒绝。"""
        with self.lock:
            if self.thread is not None:
                raise DomainError("后台尚未停止，请勿重复开始", status=409)
            values, revision = self.config.load()
            validate(values, start=True)
            self.stop_event.clear()
            self.state = "starting"
            self.error = ""
            self.active_revision = revision
            thread = threading.Thread(
                target=self._worker, args=(values,), name="bewater-worker", daemon=True
            )
            self.thread = thread
            try:
                thread.start()
            except BaseException:
                self.thread = None
                self.state = "stopped"
                raise
        self.events.emit("state", "正在连接账号")

    def control(self, action: str) -> None:
        """暂停不清空账本；停止同时取消认证等待。"""
        with self.lock:
            if action == "pause" and self.state == "running":
                self.state = "paused"
            elif action == "resume" and self.state == "paused":
                self.state = "running"
            elif action == "stop" and self.thread is not None:
                self.state = "stopping"
                self.stop_event.set()
                if self.loop and self._answer:
                    self.loop.call_soon_threadsafe(self._answer.cancel)
            else:
                raise DomainError("当前状态不允许该操作", status=409)
        self.events.emit(
            "state", {"pause": "已暂停新动作", "resume": "已恢复", "stop": "正在停止"}[action]
        )

    async def ask(self, kind: str) -> str:
        """认证挑战绑定随机 ID，超时或停止后旧输入无效。"""
        with self.lock:
            self.challenge = {"id": secrets.token_hex(12), "kind": kind}
            self.state = "waiting_auth"
            self._answer = asyncio.get_running_loop().create_future()
            answer = self._answer
        self.events.emit(
            "auth", "需要两步验证密码" if kind == "password" else "需要 Telegram 验证码"
        )
        try:
            return await asyncio.wait_for(answer, timeout=180)
        finally:
            with self.lock:
                self.challenge = None
                self._answer = None

    def submit(self, challenge_id: str, answer: str) -> None:
        """只给当前仍有效的挑战提交，永不记录验证码或密码。"""
        with self.lock:
            if (
                not self.challenge
                or challenge_id != self.challenge["id"]
                or not self.loop
                or not self._answer
            ):
                raise DomainError("认证请求已过期", status=409)
            future = self._answer

            def apply() -> None:
                """在所属事件循环完成 Future。"""
                if not future.done():
                    future.set_result(answer)

            self.loop.call_soon_threadsafe(apply)

    def _worker(self, values: dict[str, object]) -> None:
        """在连接之前取得账号与状态库锁，所有退出路径回收资源。"""
        digest = hashlib.sha256(
            (str(values["phone"]) + str(self.paths.database.resolve())).encode()
        ).hexdigest()
        lock = FileLock(self.paths.data / "state" / (digest + ".lock"))
        try:
            if not lock.acquire():
                raise DomainError("该账号与数据库已有后台实例运行")
            self.store.recover()
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._async_worker(values))
        except BaseException as exc:
            with self.lock:
                self.error = (
                    str(exc)
                    if isinstance(exc, DomainError)
                    else (
                        "认证或网络失败，请检查连接后重试" if not self.stop_event.is_set() else ""
                    )
                )
            if self.error:
                self.events.emit("error", self.error)
        finally:
            if self.loop:
                self.loop.close()
            lock.release()
            with self.lock:
                self.state = "stopped"
                self.thread = None
                self.loop = None
                self.engine = None
                self.challenge = None
            self.events.emit("state", "后台已停止")

    async def _async_worker(self, values: dict[str, object]) -> None:
        """只有此处装配生产适配器，单纯打开网页不会执行。"""
        from bewater.engine import Engine
        from bewater.telegram import Telegram

        telegram = (self.telegram_factory or Telegram)(values, self.paths)
        try:
            await telegram.login(self.ask)
            if self.stop_event.is_set():
                return
            self.engine = Engine(self, telegram, values)
            with self.lock:
                self.state = "running"
            self.events.emit("state", "运行中")
            await self.engine.run()
        finally:
            await telegram.close()

    def cleanup(self, before: str, preview: bool) -> dict[str, int]:
        """与开始共用锁，避免清理期间并发启动。"""
        with self.lock:
            if self.thread is not None:
                raise DomainError("请先停止全部任务再清理", status=409)
            return self.store.cleanup(before, preview=preview)
