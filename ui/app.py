from __future__ import annotations

import logging
import flet as ft

from src.config import load_settings
from src.sender import TelegramSender
from ui.config_form import ConfigForm
from ui.control_panel import ControlPanel, AppState
from ui.send_loop import SendState, send_loop
from ui.status_panel import StatusPanel
from ui.message_manager import MessageManager

logger = logging.getLogger(__name__)


async def main(page: ft.Page):
    page.title = "Telegram 灌水工具"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.window.width = 750
    page.window.height = 600
    page.window.min_width = 550
    page.window.min_height = 400
    page.padding = 12

    # ── 共享状态 ──
    state = SendState()
    send_task_active = False  # 防重复启动

    # ── 控制面板 → 发送状态 桥接 ──
    def on_state_changed(new_state: AppState):
        nonlocal send_task_active

        if new_state == AppState.RUNNING:
            state.paused = False
            if not send_task_active:
                send_task_active = True
                state.stopped = False
                page.run_task(start_sending)
        elif new_state == AppState.PAUSING:
            state.paused = True
        elif new_state == AppState.PAUSED:
            state.paused = True
        elif new_state == AppState.IDLE:
            state.stopped = True
            state.paused = False

    # ── UI 组件 ──
    config_form = ConfigForm(page)
    status_panel = StatusPanel(page)
    control_panel = ControlPanel(page, state_changed_callback=on_state_changed)

    # 将日志输出接入 GUI
    status_panel.attach_root_logger()

    # Top: Title bar
    title = ft.Container(
        content=ft.Text("Telegram 灌水工具", size=22, weight=ft.FontWeight.BOLD),
        padding=ft.padding.Padding.only(bottom=10),
    )

    # Bottom: Control panel
    bottom_bar = ft.Container(
        content=control_panel.build(),
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        border_radius=8,
        padding=12,
    )

    # Tabs layout (Flet 0.85: TabBar + TabBarView inside Tabs.content)
    tabs = ft.Tabs(
        length=2,
        selected_index=0,
        animation_duration=300,
        expand=True,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="⚙️ 配置", icon=ft.icons.Icons.SETTINGS),
                        ft.Tab(label="📋 日志", icon=ft.icons.Icons.TERMINAL),
                    ],
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        ft.Container(
                            content=config_form.build(),
                            padding=12,
                            expand=True,
                        ),
                        ft.Container(
                            content=status_panel.build(),
                            padding=12,
                            expand=True,
                        ),
                    ],
                ),
            ],
        ),
    )

    # Main layout
    page.add(
        title,
        tabs,
        bottom_bar,
    )

    # ── 开始发送的核心逻辑 ──
    async def start_sending():
        nonlocal send_task_active

        status_panel.add_log("info", "正在初始化...")

        # 1. 加载配置
        try:
            settings = load_settings()
        except Exception as ex:
            status_panel.add_log("error", f"加载配置失败: {ex}")
            control_panel.set_state(AppState.IDLE)
            send_task_active = False
            return

        # 2. 获取消息文件映射
        group_file_map = config_form.get_group_file_map()
        if not group_file_map:
            status_panel.add_log("warning", "请先在左侧输入群组链接和消息文件路径")
            control_panel.set_state(AppState.IDLE)
            send_task_active = False
            return

        # 3. 创建 MessageManager
        try:
            message_manager = MessageManager(group_file_map)
        except Exception as ex:
            status_panel.add_log("error", f"加载消息文件失败: {ex}")
            control_panel.set_state(AppState.IDLE)
            send_task_active = False
            return

        # 4. 创建并启动 Telegram 发送器
        sender = TelegramSender(settings)
        try:
            await sender.start(code_callback=status_panel.prompt_code)
        except Exception as ex:
            status_panel.add_log("error", f"连接 Telegram 失败: {ex}")
            control_panel.set_state(AppState.IDLE)
            send_task_active = False
            return

        status_panel.add_log("success", f"已连接 Telegram，开始向 {len(settings.target_groups)} 个群组发送")

        # 5. 重置发送状态
        state.stopped = False
        state.paused = False
        state.total_count = 0
        state.per_group_counts.clear()
        page.update()

        # 5.5 设置暂停回调（send_loop 完成当前轮次后触发）
        state.on_paused_callback = lambda: control_panel.set_state(AppState.PAUSED)

        # 6. 运行发送循环 (阻塞直到停止)
        try:
            await send_loop(page, sender, settings, state, message_manager, status_panel=status_panel)
        except Exception as ex:
            status_panel.add_log("error", f"发送循环异常: {ex}")
        finally:
            try:
                await sender.disconnect()
            except Exception:
                pass
            status_panel.add_log("info", "发送已停止")
            control_panel.set_state(AppState.IDLE)
            send_task_active = False

    # ── 窗口关闭 ──
    async def on_window_close(e):
        state.stopped = True
        await page.window_destroy_async()

    page.on_close = on_window_close

    page.update()
