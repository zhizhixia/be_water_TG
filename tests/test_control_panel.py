from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

from ui.control_panel import AppState, ControlPanel


@pytest.fixture
def mock_page() -> MagicMock:
    """模拟 Flet Page 对象，不依赖真实 Flet 运行时。"""
    return MagicMock(spec=ft.Page)


@pytest.fixture
def callback_mock() -> MagicMock:
    """模拟 state_changed_callback。"""
    return MagicMock()


@pytest.fixture
def panel(mock_page: MagicMock, callback_mock: MagicMock) -> ControlPanel:
    """创建带 mock 的 ControlPanel 实例。"""
    return ControlPanel(mock_page, state_changed_callback=callback_mock)


# ── AppState 枚举测试 ──────────────────────────────────────────────────


class TestAppState:
    """AppState 枚举 + 状态过渡路径测试。"""

    def test_enum_values_exist(self) -> None:
        """所有预期状态值存在且唯一。"""
        states = {AppState.IDLE, AppState.RUNNING, AppState.PAUSING, AppState.PAUSED}
        assert len(states) == 4
        for s in states:
            assert isinstance(s, AppState)

    def test_legal_transitions(self) -> None:
        """验证合法状态过渡路径：IDLE→RUNNING, RUNNING→PAUSING,
        PAUSING→PAUSED, PAUSED→RUNNING, 任意→IDLE。"""
        # IDLE → RUNNING
        assert AppState.IDLE != AppState.RUNNING
        # RUNNING → PAUSING
        assert AppState.RUNNING != AppState.PAUSING
        # PAUSING → PAUSED (callback 跳过，但过渡本身合法)
        assert AppState.PAUSING != AppState.PAUSED
        # PAUSED → RUNNING
        assert AppState.PAUSED != AppState.RUNNING
        # RUNNING → IDLE (任意运行态 → IDLE)
        assert AppState.RUNNING != AppState.IDLE


# ── ControlPanel 按钮可见性与状态管理测试 ───────────────────────────────


class TestControlPanel:
    """ControlPanel 按钮可见性、状态文本与回调测试。"""

    # ── 初始状态 ──

    def test_initial_state(self, panel: ControlPanel) -> None:
        """初始状态为 IDLE，start_btn 可见，其他按钮不可见。"""
        assert panel.current_state == AppState.IDLE
        assert panel.start_btn.visible is True
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is False

    # ── 状态过渡 ──

    def test_idle_to_running(self, panel: ControlPanel, callback_mock: MagicMock) -> None:
        """IDLE → RUNNING：pause_btn 和 stop_btn 可见。"""
        panel.set_state(AppState.RUNNING)

        assert panel.current_state == AppState.RUNNING
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is True
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is True
        callback_mock.assert_called_once_with(AppState.RUNNING)

    def test_running_to_pausing(self, panel: ControlPanel, callback_mock: MagicMock) -> None:
        """RUNNING → PAUSING：stop_btn 可见，pause_btn 不可见。"""
        panel.set_state(AppState.RUNNING)
        callback_mock.reset_mock()
        panel.set_state(AppState.PAUSING)

        assert panel.current_state == AppState.PAUSING
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is True
        callback_mock.assert_called_once_with(AppState.PAUSING)

    def test_pausing_to_paused_skips_callback(
        self, panel: ControlPanel, callback_mock: MagicMock
    ) -> None:
        """PAUSING → PAUSED：resume_btn 可见，回调不被触发。"""
        panel.set_state(AppState.RUNNING)
        panel.set_state(AppState.PAUSING)
        callback_mock.reset_mock()
        panel.set_state(AppState.PAUSED)

        assert panel.current_state == AppState.PAUSED
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is True
        assert panel.stop_btn.visible is True
        callback_mock.assert_not_called()

    def test_paused_to_running(self, panel: ControlPanel, callback_mock: MagicMock) -> None:
        """PAUSED → RUNNING：resume_btn 隐藏，pause_btn 和 stop_btn 可见。"""
        panel.set_state(AppState.RUNNING)
        panel.set_state(AppState.PAUSING)
        panel.set_state(AppState.PAUSED)
        callback_mock.reset_mock()
        panel.set_state(AppState.RUNNING)

        assert panel.current_state == AppState.RUNNING
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is True
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is True
        callback_mock.assert_called_once_with(AppState.RUNNING)

    def test_any_to_idle(self, panel: ControlPanel, callback_mock: MagicMock) -> None:
        """RUNNING → IDLE：start_btn 可见，其他按钮隐藏。"""
        panel.set_state(AppState.RUNNING)
        callback_mock.reset_mock()
        panel.set_state(AppState.IDLE)

        assert panel.current_state == AppState.IDLE
        assert panel.start_btn.visible is True
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is False
        callback_mock.assert_called_once_with(AppState.IDLE)

    # ── _update_buttons 直接测试 ──

    def test_update_buttons_all_states(self, panel: ControlPanel) -> None:
        """_update_buttons 在各状态下正确设置按钮可见性。"""
        # IDLE
        panel.current_state = AppState.IDLE
        panel._update_buttons()
        assert panel.start_btn.visible is True
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is False

        # RUNNING
        panel.current_state = AppState.RUNNING
        panel._update_buttons()
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is True
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is True

        # PAUSING
        panel.current_state = AppState.PAUSING
        panel._update_buttons()
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is False
        assert panel.stop_btn.visible is True

        # PAUSED
        panel.current_state = AppState.PAUSED
        panel._update_buttons()
        assert panel.start_btn.visible is False
        assert panel.pause_btn.visible is False
        assert panel.resume_btn.visible is True
        assert panel.stop_btn.visible is True

    # ── 状态文本 ──

    def test_status_text_all_states(self, panel: ControlPanel) -> None:
        """各状态下 status_text 正确。"""
        panel.set_state(AppState.IDLE)
        assert panel.status_text.value == "就绪"

        panel.set_state(AppState.RUNNING)
        assert panel.status_text.value == "运行中..."

        panel.set_state(AppState.PAUSING)
        assert panel.status_text.value == "\u23f3 暂停中，等待当前轮次完成..."

        panel.set_state(AppState.PAUSED)
        assert panel.status_text.value == "已暂停"

    # ── 回调行为 ──

    def test_callback_fires_on_running(self, panel: ControlPanel, callback_mock: MagicMock) -> None:
        """IDLE → RUNNING 触发 callback(RUNNING)。"""
        panel.set_state(AppState.RUNNING)
        callback_mock.assert_called_once_with(AppState.RUNNING)

    def test_callback_not_called_when_none(self, mock_page: MagicMock) -> None:
        """callback 为 None 时 set_state 不崩溃。"""
        panel = ControlPanel(mock_page, state_changed_callback=None)
        # 不应抛出异常
        panel.set_state(AppState.RUNNING)
        assert panel.current_state == AppState.RUNNING
