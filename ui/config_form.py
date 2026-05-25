from __future__ import annotations

import asyncio

import flet as ft

from src.config import Settings, load_settings, save_settings
from src.group_parser import parse_group_links

Icons = ft.icons.Icons


class ConfigForm:
    """配置表单 — 编辑 Settings 的 8 个字段、按群组选择消息文件、加载/保存 .env。"""

    def __init__(self, page: ft.Page) -> None:
        self.page = page

        # ── 输入控件 ──
        self.api_id = ft.TextField(
            label="API ID",
            hint_text="从 my.telegram.org 获取",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._validate_api_id,
        )
        self.api_hash = ft.TextField(
            label="API Hash",
            hint_text="从 my.telegram.org 获取",
            password=True,
            can_reveal_password=True,
        )
        self.phone = ft.TextField(
            label="手机号",
            hint_text="+8613800138000",
        )
        self.target_groups = ft.TextField(
            label="目标群组",
            hint_text="逗号分隔，如: https://t.me/group1, https://t.me/group2",
            multiline=True,
            min_lines=2,
            max_lines=4,
            on_change=self._on_target_groups_change,
        )
        self.min_interval = ft.TextField(
            label="最小间隔 (秒)",
            value="20",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._validate_interval,
        )
        self.max_interval = ft.TextField(
            label="最大间隔 (秒)",
            value="30",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._validate_interval,
        )
        self.proxy_host = ft.TextField(
            label="代理地址 (可选)",
            hint_text="127.0.0.1",
            on_change=self._validate_proxy,
        )
        self.proxy_port = ft.TextField(
            label="代理端口 (可选)",
            hint_text="7890",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._validate_proxy,
        )

        # ── AI 配置 ──
        self.ai_enabled = ft.Switch(
            label="AI 智能聊天模式",
            value=False,
            on_change=self._on_ai_enabled_change,
        )
        self.ai_api_key = ft.TextField(
            label="AI API Key",
            hint_text="sk-your-deepseek-api-key",
            password=True,
            can_reveal_password=True,
            visible=False,
        )
        self.ai_base_url = ft.TextField(
            label="AI Base URL",
            hint_text="https://api.deepseek.com/v1",
            value="https://api.deepseek.com/v1",
            visible=False,
        )
        self.ai_model = ft.TextField(
            label="AI 模型",
            hint_text="deepseek-chat",
            value="deepseek-chat",
            visible=False,
        )
        self.ai_prompt = ft.TextField(
            label="AI 系统提示词 (System Prompt)",
            hint_text="描述 AI 的聊天风格和人设",
            value="你是一个普通群聊参与者，请根据对话上下文自然地回复消息。回复要简短、口语化，像真人聊天一样。不要使用 AI 语气，不要提供帮助或自我介绍。",
            multiline=True,
            min_lines=3,
            max_lines=5,
            visible=False,
        )

        # ── 每个群组的消息文件路径 (群组 → TextField) ──
        self._message_file_fields: dict[str, ft.TextField] = {}
        self._message_file_fields_saved: dict[str, str] = {}  # 从 .env 加载的持久化值
        self._group_file_column = ft.Column([], spacing=4)

        # ── 按钮 ──
        self.load_btn = ft.ElevatedButton(
            "📂 加载配置",
            on_click=self.load_config,
            icon=Icons.FOLDER_OPEN,
        )
        self.save_btn = ft.ElevatedButton(
            "💾 保存配置",
            on_click=self.save_config,
            icon=Icons.SAVE,
        )

        # ── 状态 ──
        self.status = ft.Text("", size=12, color=ft.Colors.GREEN)

    # ── 构建 UI ──────────────────────────────────────────────────

    def build(self) -> ft.Column:
        """构建配置表单 UI。由 app.py 挂载到左侧面板。"""
        # 首次构建动态消息文件行
        self._rebuild_group_file_rows()
        controls: list[ft.Control] = [
            ft.Text("⚙️ 配置", size=20, weight=ft.FontWeight.BOLD),
            self.api_id,
            self.api_hash,
            self.phone,
            self.target_groups,
            ft.Row([self.min_interval, self.max_interval]),
            ft.Row([self.proxy_host, self.proxy_port]),
            ft.Divider(height=8),
            ft.Text("🤖 AI 智能聊天", size=16, weight=ft.FontWeight.BOLD),
            self.ai_enabled,
            self.ai_api_key,
            self.ai_base_url,
            self.ai_model,
            self.ai_prompt,
            ft.Divider(height=8),
            ft.Text("📁 消息文件 (输入群组链接后出现路径输入框)", size=16, weight=ft.FontWeight.BOLD),
            self._group_file_column,
            ft.Divider(height=8),
            ft.Row([self.load_btn, self.save_btn]),
            self.status,
        ]
        return ft.Column(controls, scroll=ft.ScrollMode.AUTO, expand=True)

    def _rebuild_group_file_rows(self) -> None:
        """根据 target_groups 输入重建消息文件路径输入行。保留已有值。"""
        groups = parse_group_links(self.target_groups.value or "")
        self._group_file_column.controls.clear()
        if not groups:
            self._group_file_column.controls.append(
                ft.Text(
                    "输入目标群组链接后，此处将自动显示消息文件路径输入框",
                    size=12,
                    color=ft.Colors.GREY_400,
                    italic=True,
                )
            )
            return
        new_fields: dict[str, ft.TextField] = {}
        for group in groups:
            display_name = group.split("/")[-1][:20]
            # 优先用已保存的值，其次用当前输入框的值
            saved = self._message_file_fields_saved.get(group, "")
            existing = self._message_file_fields.get(group, None)
            cur_value = existing.value if existing else ""
            default_value = saved or cur_value

            # 提取纯 username 用于默认文件名提示
            username = group.split("/")[-1]
            txt = ft.TextField(
                label=f"消息文件 ({display_name})",
                hint_text=f"如: messages_{username}.txt",
                value=default_value,
                expand=True,
            )
            self._group_file_column.controls.append(txt)
            new_fields[group] = txt
        self._message_file_fields = new_fields

    def get_group_file_map(self) -> dict[str, str]:
        """返回群组链接 → 消息文件路径的映射，供 MessageManager 使用。"""
        return {g: field.value.strip() for g, field in self._message_file_fields.items() if field.value and field.value.strip()}

    # ── 实时校验 ────────────────────────────────────────────────

    def _parse_int(self, value: str) -> int | None:
        try:
            return int(value) if value.strip() else None
        except ValueError:
            return None

    def _validate_api_id(self, e: ft.ControlEvent) -> None:
        if not e.control.value:
            self.api_id.error_text = None
            return
        v = self._parse_int(e.control.value)
        if v is None:
            self.api_id.error_text = "API ID 必须是数字"
        elif v <= 0:
            self.api_id.error_text = "API ID 必须大于 0"
        else:
            self.api_id.error_text = None
        self.page.update()

    def _validate_interval(self, e: ft.ControlEvent) -> None:
        min_v = self._parse_int(self.min_interval.value)
        max_v = self._parse_int(self.max_interval.value)
        # 仅在值都存在且可解析时才比较
        if min_v is not None and max_v is not None:
            if min_v >= max_v:
                self.min_interval.error_text = "最小间隔必须小于最大间隔"
                self.max_interval.error_text = "最大间隔必须大于最小间隔"
            else:
                self.min_interval.error_text = None
                self.max_interval.error_text = None
        elif e.control == self.min_interval and min_v is not None and min_v <= 0:
            self.min_interval.error_text = "最小间隔必须大于 0"
        elif e.control == self.max_interval and max_v is not None and max_v <= 0:
            self.max_interval.error_text = "最大间隔必须大于 0"
        else:
            self.min_interval.error_text = None
            self.max_interval.error_text = None
        self.page.update()

    def _validate_proxy(self, e: ft.ControlEvent) -> None:
        host = self.proxy_host.value.strip()
        port = self.proxy_port.value.strip()
        if (host and not port) or (port and not host):
            self.proxy_host.error_text = "代理地址和端口必须同时配置"
            self.proxy_port.error_text = "代理地址和端口必须同时配置"
        else:
            self.proxy_host.error_text = None
            self.proxy_port.error_text = None
        self.page.update()

    # ── 群组变更 ─────────────────────────────────────────────────

    def _on_target_groups_change(self, e: ft.ControlEvent) -> None:
        """群组文本变更时重建消息文件输入行。"""
        self._rebuild_group_file_rows()
        self.page.update()

    def _on_ai_enabled_change(self, e: ft.ControlEvent | None) -> None:
        """AI 模式开关变更时切换 AI 配置字段的可见性。"""
        visible = self.ai_enabled.value
        self.ai_api_key.visible = visible
        self.ai_base_url.visible = visible
        self.ai_model.visible = visible
        self.ai_prompt.visible = visible
        self.page.update()

    # ── 加载 / 保存 ─────────────────────────────────────────────

    async def load_config(self, e: ft.ControlEvent) -> None:
        """从 .env 加载配置并填充表单。"""
        try:
            settings = load_settings()
            self.api_id.value = str(settings.api_id)
            self.api_hash.value = settings.api_hash
            self.phone.value = settings.phone
            self.target_groups.value = ", ".join(settings.target_groups)
            self.min_interval.value = str(settings.min_interval)
            self.max_interval.value = str(settings.max_interval)
            self.proxy_host.value = settings.proxy_host or ""
            self.proxy_port.value = str(settings.proxy_port) if settings.proxy_port else ""

            # 恢复消息文件路径
            if settings.message_files:
                for group, path in settings.message_files.items():
                    self._message_file_fields_saved[group] = path
            self._rebuild_group_file_rows()

            # 加载 AI 配置
            self.ai_enabled.value = settings.ai_enabled
            self.ai_api_key.value = settings.ai_api_key
            self.ai_base_url.value = settings.ai_base_url
            self.ai_model.value = settings.ai_model
            self.ai_prompt.value = settings.ai_prompt or self.ai_prompt.value  # 保留默认值
            # 触发可见性
            self._on_ai_enabled_change(None)

            self.status.value = "✅ 配置已加载"
            self.status.color = ft.Colors.GREEN
            self.page.update()
            try:
                await asyncio.sleep(3)
                self.status.value = ""
                self.page.update()
            except Exception:
                pass
            return
        except Exception as ex:
            self.status.value = f"❌ 加载失败: {ex}"
            self.status.color = ft.Colors.RED
        self.page.update()

    async def save_config(self, e: ft.ControlEvent) -> None:
        """校验表单并保存到 .env。"""
        # ── 收集校验错误 ──
        errors: list[str] = []
        # API ID
        api_id = self._parse_int(self.api_id.value)
        if api_id is None:
            errors.append("API ID 必须是数字")
        elif api_id <= 0:
            errors.append("API ID 必须大于 0")
        # API Hash
        if not self.api_hash.value.strip():
            errors.append("API Hash 不能为空")
        # 手机号
        if not self.phone.value.strip():
            errors.append("手机号不能为空")
        # 群组
        groups = parse_group_links(self.target_groups.value or "")
        if not groups:
            errors.append("至少需要一个目标群组")
        # 间隔
        min_i = self._parse_int(self.min_interval.value or "0")
        max_i = self._parse_int(self.max_interval.value or "0")
        if min_i is None or max_i is None:
            errors.append("间隔必须是数字")
        elif min_i >= max_i:
            errors.append("最小间隔必须小于最大间隔")
        # 代理
        host = self.proxy_host.value.strip() or None
        port_str = self.proxy_port.value.strip()
        port = int(port_str) if port_str else None
        if (host and not port) or (port and not host):
            errors.append("代理地址和端口必须同时配置")

        if errors:
            self.status.value = "❌ " + "; ".join(errors)
            self.status.color = ft.Colors.RED
            self.page.update()
            return

        try:
            s = Settings(
                api_id=api_id,  # type: ignore[arg-type]
                api_hash=self.api_hash.value.strip(),
                phone=self.phone.value.strip(),
                target_groups=groups,
                min_interval=min_i,  # type: ignore[arg-type]
                max_interval=max_i,  # type: ignore[arg-type]
                proxy_host=host,
                proxy_port=port,
                message_files=self.get_group_file_map(),
                ai_enabled=self.ai_enabled.value,
                ai_api_key=self.ai_api_key.value.strip(),
                ai_base_url=self.ai_base_url.value.strip() or "https://api.deepseek.com/v1",
                ai_model=self.ai_model.value.strip() or "deepseek-chat",
                ai_prompt=self.ai_prompt.value.strip(),
            )
            save_settings(s)
            self.status.value = "✅ 配置已保存到 .env"
            self.status.color = ft.Colors.GREEN
            self.page.update()
            try:
                await asyncio.sleep(3)
                self.status.value = ""
                self.page.update()
            except Exception:
                pass
            return
        except Exception as ex:
            self.status.value = f"❌ 保存失败: {ex}"
            self.status.color = ft.Colors.RED
        self.page.update()
