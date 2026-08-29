"""抽奖配置的加载、持久化与边界校验测试。"""
from __future__ import annotations

import pytest

from src.config import Settings, load_settings, save_settings


LOTTERY_ENV_NAMES = (
    "LOTTERY_ENABLED",
    "LOTTERY_SCAN_INTERVAL_MINUTES",
    "DAILY_PINNED_LOTTERY_SCAN_ENABLED",
    "DAILY_CHECKIN_ENABLED",
    "LOTTERY_PER_GROUP_DAILY_LIMIT",
    "LOTTERY_ACCOUNT_DAILY_LIMIT",
    "LOTTERY_ACTION_GAP_MIN_SECONDS",
    "LOTTERY_ACTION_GAP_MAX_SECONDS",
)


def test_lottery_configuration_defaults_are_safe() -> None:
    """抽奖配置应使用启用扫描、每日检查和保守额度的默认值。"""
    settings = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
    )

    assert settings.lottery_enabled is True
    assert settings.lottery_scan_interval_minutes == 60
    assert settings.daily_pinned_lottery_scan_enabled is True
    assert settings.daily_checkin_enabled is True
    assert settings.lottery_per_group_daily_limit == 10
    assert settings.lottery_account_daily_limit == 30
    assert settings.lottery_action_gap_min_seconds == 20
    assert settings.lottery_action_gap_max_seconds == 60


def test_load_settings_reads_all_lottery_environment_values(
    monkeypatch: pytest.MonkeyPatch, set_required_env: None
) -> None:
    """环境变量应覆盖全部八项抽奖配置。"""
    monkeypatch.setenv("TARGET_GROUPS", "https://t.me/example")
    monkeypatch.setenv("LOTTERY_ENABLED", "false")
    monkeypatch.setenv("LOTTERY_SCAN_INTERVAL_MINUTES", "120")
    monkeypatch.setenv("DAILY_PINNED_LOTTERY_SCAN_ENABLED", "false")
    monkeypatch.setenv("DAILY_CHECKIN_ENABLED", "false")
    monkeypatch.setenv("LOTTERY_PER_GROUP_DAILY_LIMIT", "7")
    monkeypatch.setenv("LOTTERY_ACCOUNT_DAILY_LIMIT", "40")
    monkeypatch.setenv("LOTTERY_ACTION_GAP_MIN_SECONDS", "25")
    monkeypatch.setenv("LOTTERY_ACTION_GAP_MAX_SECONDS", "90")

    settings = load_settings()

    assert (
        settings.lottery_enabled,
        settings.lottery_scan_interval_minutes,
        settings.daily_pinned_lottery_scan_enabled,
        settings.daily_checkin_enabled,
        settings.lottery_per_group_daily_limit,
        settings.lottery_account_daily_limit,
        settings.lottery_action_gap_min_seconds,
        settings.lottery_action_gap_max_seconds,
    ) == (False, 120, False, False, 7, 40, 25, 90)


def test_save_settings_round_trips_all_lottery_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """保存后重新加载应保留全部八项抽奖配置。"""
    env_path = tmp_path / ".env"
    original = Settings(
        api_id=12345,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/example"],
        lottery_enabled=False,
        lottery_scan_interval_minutes=120,
        daily_pinned_lottery_scan_enabled=False,
        daily_checkin_enabled=False,
        lottery_per_group_daily_limit=7,
        lottery_account_daily_limit=40,
        lottery_action_gap_min_seconds=25,
        lottery_action_gap_max_seconds=90,
    )

    save_settings(original, str(env_path))
    saved_values = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if line and "=" in line
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    for name, value in saved_values.items():
        monkeypatch.setenv(name, value)

    reloaded = load_settings()

    assert (
        reloaded.lottery_enabled,
        reloaded.lottery_scan_interval_minutes,
        reloaded.daily_pinned_lottery_scan_enabled,
        reloaded.daily_checkin_enabled,
        reloaded.lottery_per_group_daily_limit,
        reloaded.lottery_account_daily_limit,
        reloaded.lottery_action_gap_min_seconds,
        reloaded.lottery_action_gap_max_seconds,
    ) == (False, 120, False, False, 7, 40, 25, 90)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"lottery_scan_interval_minutes": 14}, "LOTTERY_SCAN_INTERVAL_MINUTES"),
        ({"lottery_per_group_daily_limit": 0}, "LOTTERY_PER_GROUP_DAILY_LIMIT"),
        (
            {"lottery_per_group_daily_limit": 11, "lottery_account_daily_limit": 10},
            "LOTTERY_ACCOUNT_DAILY_LIMIT",
        ),
        ({"lottery_action_gap_min_seconds": 4}, "LOTTERY_ACTION_GAP_MIN_SECONDS"),
        (
            {"lottery_action_gap_min_seconds": 30, "lottery_action_gap_max_seconds": 29},
            "LOTTERY_ACTION_GAP_MAX_SECONDS",
        ),
        ({"lottery_action_gap_max_seconds": 601}, "LOTTERY_ACTION_GAP_MAX_SECONDS"),
    ],
)
def test_lottery_configuration_rejects_invalid_ranges_and_relationships(
    overrides: dict[str, int], message: str
) -> None:
    """非法区间和相互关联的额度、间隔必须在构造配置时失败。"""
    with pytest.raises(ValueError, match=message):
        Settings(
            api_id=1,
            api_hash="x" * 32,
            phone="+1",
            target_groups=["https://t.me/example"],
            **overrides,
        )
