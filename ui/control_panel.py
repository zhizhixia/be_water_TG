from __future__ import annotations

from enum import Enum, auto

import flet as ft

Icons = ft.icons.Icons


class AppState(Enum):
    """发送控制状态机。

    IDLE → RUNNING → PAUSING → PAUSED → RUNNING → IDLE

    PAUSING: 等待当前轮次完成（send_loop 完成当前所有群组的发送后进入 PAUSED）。
    """

    IDLE = auto()
    RUNNING = auto()
    PAUSING = auto()
    PAUSED = auto()


class ControlPanel:
    """开始 / 暂停 / 继续 / 停止 按钮面板，内嵌状态机。

    通过 state_changed_callback 向外通知状态变更，外部代码负责
    桥接 AppState ↔ SendState (send_loop.py)。本面板仅管理 UI 状态。
    """

    def __init__(self, page: ft.Page, state_changed_callback=None) -> None:
        self.page = page
        self.current_state = AppState.IDLE
        self._on_state_changed = state_changed_callback  # (new_state: AppState) -> None

        self.start_btn = ft.ElevatedButton(
            "▶ 开始发送",
            icon=Icons.PLAY_ARROW,
            style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE),
            on_click=self._on_start,
            visible=True,
        )
        self.pause_btn = ft.ElevatedButton(
            "⏸ 暂停",
            icon=Icons.PAUSE,
            style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_700, color=ft.Colors.WHITE),
            on_click=self._on_pause,
            visible=False,
        )
        self.resume_btn = ft.ElevatedButton(
            "▶ 继续",
            icon=Icons.PLAY_ARROW,
            style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE),
            on_click=self._on_resume,
            visible=False,
        )
        self.stop_btn = ft.ElevatedButton(
            "⏹ 停止",
            icon=Icons.STOP,
            style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700, color=ft.Colors.WHITE),
            on_click=self._on_stop,
            visible=False,
        )
        self.status_text = ft.Text("就绪", size=14)
        self.retry_status = ft.Text("", size=12, color=ft.Colors.ORANGE_200, visible=False)

    # ── 构建 UI ──────────────────────────────────────────────────

    def build(self) -> ft.Row:
        """构建控制面板 UI。由 app.py 挂载到底部占位区域。"""
        return ft.Row(
            [
                self.start_btn,
                self.pause_btn,
                self.resume_btn,
                self.stop_btn,
                self.status_text,
                self.retry_status,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=12,
        )

    # ── 状态管理 ─────────────────────────────────────────────────

    def set_state(self, new_state: AppState) -> None:
        """设置新状态并刷新按钮可见性与状态文本。

        外部代码（如 send_loop 完成当前轮次后）可调用此方法
        将 PAUSING 推进到 PAUSED。
        """
        old_state = self.current_state
        self.current_state = new_state
        self._update_buttons()
        # PAUSING → PAUSED 不触发回调（避免重复暂停信号）
        if self._on_state_changed and not (
            old_state == AppState.PAUSING and new_state == AppState.PAUSED
        ):
            self._on_state_changed(new_state)
        self.page.update()

    def _update_buttons(self) -> None:
        """根据当前状态控制各按钮的可见性与状态文本。"""
        self.start_btn.visible = self.current_state == AppState.IDLE
        self.pause_btn.visible = self.current_state == AppState.RUNNING
        self.resume_btn.visible = self.current_state == AppState.PAUSED
        self.stop_btn.visible = self.current_state in [
            AppState.RUNNING,
            AppState.PAUSING,
            AppState.PAUSED,
        ]

        status_map = {
            AppState.IDLE: "就绪",
            AppState.RUNNING: "运行中...",
            AppState.PAUSING: "⏳ 暂停中，等待当前轮次完成...",
            AppState.PAUSED: "已暂停",
        }
        self.status_text.value = status_map.get(self.current_state, "")

    # ── 按钮事件处理 ─────────────────────────────────────────────

    def _on_start(self, e: ft.ControlEvent) -> None:
        """点击"开始发送"：IDLE → RUNNING。"""
        self.set_state(AppState.RUNNING)

    def _on_pause(self, e: ft.ControlEvent) -> None:
        """点击"暂停"：RUNNING → PAUSING。

        暂停语义为"完成当前轮次后暂停"，因此先进入 PAUSING 状态。
        外部 callback 应设置 SendState.paused = True，
        send_loop 完成当前轮次后，外部代码调用 set_state(PAUSED)。
        """
        self.set_state(AppState.PAUSING)

    def _on_resume(self, e: ft.ControlEvent) -> None:
        """点击"继续"：PAUSED → RUNNING。"""
        self.set_state(AppState.RUNNING)

    def _on_stop(self, e: ft.ControlEvent) -> None:
        """点击"停止"：弹出确认对话框，确认后任意运行态 → IDLE。"""

        def confirm_stop(e: ft.ControlEvent):
            self.page.close(alert)
            self.set_state(AppState.IDLE)

        def cancel_stop(e: ft.ControlEvent):
            self.page.close(alert)

        alert = ft.AlertDialog(
            title=ft.Text("确认停止"),
            content=ft.Text("确定要停止发送吗？"),
            actions=[
                ft.TextButton("取消", on_click=cancel_stop),
                ft.TextButton(
                    "确定停止",
                    on_click=confirm_stop,
                    style=ft.ButtonStyle(color=ft.Colors.RED),
                ),
            ],
        )
        self.page.open(alert)

    def set_retry_status(self, text: str) -> None:
        """设置重试状态提示文本，空字符串则隐藏。"""
        self.retry_status.value = text
        self.retry_status.visible = bool(text)
        try:
            self.page.update()
        except Exception:
            pass
