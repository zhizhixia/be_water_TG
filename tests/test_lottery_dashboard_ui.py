"""抽奖仪表盘静态界面行为契约测试。"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_ui_has_lottery_pagination_and_global_configuration_notice() -> None:
    """页面提供默认最新 50、总数、分页，以及配置全局生效提示。"""
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="lotteryAlertTotal"' in html
    assert 'id="lotteryPrev"' in html
    assert 'id="lotteryNext"' in html
    assert "全局生效，不按群组分别配置" in html
    assert "lotteryPage: {offset: 0, limit: 50" in script
    assert "/api/lotteries?offset=" in script


def test_dashboard_ui_opens_only_from_click_and_never_claims_manual_verification() -> None:
    """打开动作由点击触发 window.open，降级展示消息 ID，文案不伪称已人工核验。"""
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.open(data.url, \"_blank\", \"noopener\")" in script
    assert "message_id" in script
    assert "抽奖操作失败：" in script
    assert "已人工核验" not in html
    assert "已人工核验" not in script
