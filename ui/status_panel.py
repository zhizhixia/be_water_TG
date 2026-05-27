from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import flet as ft

from src.interval import format_duration


class GUIHandler(logging.Handler):
    """Custom logging handler that routes log records to a StatusPanel."""

    def __init__(self, panel: "StatusPanel"):
        super().__init__()
        self._panel = panel

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if record.levelno >= logging.ERROR:
                level = "error"
            elif record.levelno >= logging.WARNING:
                level = "warning"
            else:
                level = "info"
            self._panel.add_log(level, msg)
        except Exception:
            pass


class StatusPanel:
    """状态面板 — 运行日志 + 验证码输入。

    - 上半部分: 只读的终端输出显示区域
    - 下半部分: 验证码输入区域 (需要时显示)
    """

    MAX_LOG_ENTRIES = 500

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.log_entries: list[str] = []

        # ── 计数器和倒计时显示 ──
        self.counter_text = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_200)
        self.countdown_text = ft.Text("", size=13, color=ft.Colors.ORANGE_200)

        # ── 终端输出区域 (只读, 自动滚动) ──
        self.log_output = ft.TextField(
            multiline=True,
            read_only=True,
            min_lines=10,
            max_lines=30,
            text_style=ft.TextStyle(size=12, font_family="Consolas"),
            border_color=ft.Colors.GREY_400,
            expand=True,
        )

        # ── 验证码输入区域 (默认隐藏) ──
        self._code_future: asyncio.Future[str] | None = None
        self.code_input = ft.TextField(
            label="验证码",
            hint_text="请输入 Telegram 发来的验证码",
            visible=False,
            on_submit=self._on_code_submit,
        )
        self._code_waiting_text = ft.Text("", visible=False, size=12, color=ft.Colors.GREY_400)

        # ── 配置 logging handler ──
        self._gui_handler = GUIHandler(self)
        self._gui_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                              datefmt="%H:%M:%S")
        )

    def attach_root_logger(self) -> None:
        """将 GUI handler 挂载到根 logger，捕获所有模块的日志输出。"""
        root = logging.getLogger()
        root.addHandler(self._gui_handler)
        # 同时保留终端输出（不影响原有 handler）
        self.add_log("info", "日志已接入 GUI 面板")

    # ── Public API ──────────────────────────────────────────────

    def build(self) -> ft.Column:
        """构建状态面板布局。"""
        return ft.Column(
            [
                ft.Text("📋 运行日志", size=16, weight=ft.FontWeight.BOLD),
                self.counter_text,
                self.countdown_text,
                ft.Container(
                    content=self.log_output,
                    bgcolor=ft.Colors.BLACK_12,
                    border_radius=4,
                    padding=8,
                    expand=True,
                ),
                self._code_waiting_text,
                self.code_input,
            ],
            expand=True,
        )

    def add_log(self, level: str, message: str) -> None:
        """添加一行日志。"""
        now = datetime.now().strftime("%H:%M:%S")
        entry = f"[{now}] {message}"
        self.log_entries.append(entry)

        while len(self.log_entries) > self.MAX_LOG_ENTRIES:
            self.log_entries.pop(0)

        self.log_output.value = "\n".join(self.log_entries)
        # 如果日志区域有焦点，不要强制更新文本以免干扰用户查看
        try:
            self.page.update()
        except Exception:
            pass

    def clear_log(self) -> None:
        """清空日志。"""
        self.log_entries.clear()
        self.log_output.value = ""
        try:
            self.page.update()
        except Exception:
            pass

    def update_counter(self, total: int, per_group: dict[str, int]) -> None:
        """更新发送计数显示。

        Args:
            total: 总发送条数。
            per_group: 各群组发送条数映射，key 为群组链接。
        """
        lines = [f"已发送 {total} 条"]
        if per_group:
            # 提取群组链接最后一段作为显示名
            parts = []
            for link, count in per_group.items():
                # 从链接中提取最后一段作为群组名
                name = link.rstrip("/").split("/")[-1]
                if name.startswith("@"):
                    name = name[1:]
                parts.append(f"{name}: {count}条")
            lines.append(", ".join(parts))
        self.counter_text.value = "\n".join(lines)
        try:
            self.page.update()
        except Exception:
            pass

    def update_countdown(self, seconds: int) -> None:
        """更新倒计时显示。

        Args:
            seconds: 剩余秒数，0 时清空显示。
        """
        if seconds <= 0:
            self.countdown_text.value = ""
        else:
            formatted = format_duration(seconds)
            self.countdown_text.value = f"下一轮在 {formatted} 后开始"
        try:
            self.page.update()
        except Exception:
            pass

    # ── 验证码输入 ──────────────────────────────────────────────

    async def prompt_code(self) -> str:
        """显示验证码输入框并等待用户输入。

        这是一个异步方法，会阻塞直到用户提交验证码。
        用于 sender.start() 的 code_callback。
        """
        self._code_waiting_text.value = "⚠️ 请在下方输入 Telegram 验证码"
        self._code_waiting_text.visible = True
        self.code_input.visible = True
        self.code_input.value = ""
        self.code_input.border_color = ft.Colors.BLUE_400
        self.code_input.focus()
        self.page.update()

        self._code_future = asyncio.Future()
        try:
            code = await self._code_future
            return code
        except asyncio.CancelledError:
            return ""
        finally:
            self._code_waiting_text.visible = False
            self.code_input.visible = False
            self.code_input.border_color = None
            self._code_future = None
            self.page.update()

    def _on_code_submit(self, e: ft.ControlEvent) -> None:
        """用户按下回车提交验证码。"""
        if self._code_future and not self._code_future.done():
            self._code_future.set_result((self.code_input.value or "").strip())
