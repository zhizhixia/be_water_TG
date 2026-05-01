from __future__ import annotations

import logging

import pytest

from src.config import Settings, load_settings, save_settings


# ── 辅助函数 ──────────────────────────────────────────────────────────
def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置 load_settings 所需的基础环境变量，并阻止加载真实 .env 文件。"""
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "a" * 32)
    monkeypatch.setenv("PHONE", "+8613800138000")
    monkeypatch.setenv("MIN_INTERVAL", "60")
    monkeypatch.setenv("MAX_INTERVAL", "180")


# ── load_settings 向后兼容测试 ────────────────────────────────────────


class TestLoadSettingsTargetGroupCompat:
    """TARGET_GROUP / TARGET_GROUPS 向后兼容性测试。"""

    def test_only_target_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只有 TARGET_GROUP（旧的单数键）→ target_groups 为单元素列表。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUP", "https://t.me/mygroup")
        monkeypatch.delenv("TARGET_GROUPS", raising=False)

        settings = load_settings()
        assert settings.target_groups == ["https://t.me/mygroup"]
        assert settings.target_group == "https://t.me/mygroup"

    def test_only_target_groups(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只有 TARGET_GROUPS（新的复数键）→ 正确解析为列表。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/a,https://t.me/b")
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        settings = load_settings()
        assert settings.target_groups == [
            "https://t.me/a",
            "https://t.me/b",
        ]
        assert settings.target_group == "https://t.me/a"

    def test_both_set_prefers_target_groups(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """同时配置 TARGET_GROUPS 和 TARGET_GROUP → 优先 TARGET_GROUPS 并记录警告。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/groups_a,https://t.me/groups_b")
        monkeypatch.setenv("TARGET_GROUP", "https://t.me/old_group")

        with caplog.at_level(logging.WARNING):
            settings = load_settings()

        assert settings.target_groups == [
            "https://t.me/groups_a",
            "https://t.me/groups_b",
        ]
        assert settings.target_group == "https://t.me/groups_a"
        assert "同时配置" in caplog.text
        assert "TARGET_GROUPS" in caplog.text

    def test_neither_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUPS 和 TARGET_GROUP 都未配置 → 抛出 ValueError。"""
        set_required_env(monkeypatch)
        monkeypatch.delenv("TARGET_GROUPS", raising=False)
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        with pytest.raises(ValueError, match="TARGET_GROUPS|TARGET_GROUP"):
            load_settings()

    def test_target_group_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUP 为空字符串 → 视为未设置 → ValueError。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUP", "")
        monkeypatch.delenv("TARGET_GROUPS", raising=False)

        with pytest.raises(ValueError, match="TARGET_GROUPS|TARGET_GROUP"):
            load_settings()

    def test_target_groups_chinese_comma(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUPS 使用中文逗号 → 正确解析。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/甲，https://t.me/乙")
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        settings = load_settings()
        assert settings.target_groups == [
            "https://t.me/甲",
            "https://t.me/乙",
        ]

    def test_target_groups_single_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUPS 只配置一个值 → 单元素列表。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/solo")
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        settings = load_settings()
        assert settings.target_groups == ["https://t.me/solo"]
        assert settings.target_group == "https://t.me/solo"

    def test_target_groups_trailing_spaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUPS 元素含前后空格 → 自动去除。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "  https://t.me/a ,  https://t.me/b  ")
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        settings = load_settings()
        assert settings.target_groups == [
            "https://t.me/a",
            "https://t.me/b",
        ]


    def test_target_groups_with_blank_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUPS 含空白条目 → 自动过滤。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv("TARGET_GROUPS", "https://t.me/a,,https://t.me/b,,")
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        settings = load_settings()
        assert settings.target_groups == [
            "https://t.me/a",
            "https://t.me/b",
        ]

    def test_target_groups_three_elements(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TARGET_GROUPS 含三个元素 → 正确解析。"""
        set_required_env(monkeypatch)
        monkeypatch.setenv(
            "TARGET_GROUPS",
            "https://t.me/alpha,https://t.me/beta,https://t.me/gamma",
        )
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        settings = load_settings()
        assert settings.target_groups == [
            "https://t.me/alpha",
            "https://t.me/beta",
            "https://t.me/gamma",
        ]
        assert settings.target_group == "https://t.me/alpha"


# ── save_settings → load_settings 往返测试 ────────────────────────────


class TestSaveSettingsRoundTrip:
    """save_settings 与 load_settings 的往返兼容性测试。"""

    def test_save_then_load_round_trip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """保存 target_groups → 重新加载 → 值一致。"""
        env_path = tmp_path / ".env"
        original = Settings(
            api_id=99999,
            api_hash="roundtrip_hash_abcdef1234567890",
            phone="+8600000000000",
            target_groups=["https://t.me/round_a", "https://t.me/round_b"],
            min_interval=30,
            max_interval=90,
        )

        # 写入临时 .env
        save_settings(original, str(env_path))

        # 模拟 env 文件内容已加载：手动设置环境变量并阻止 load_dotenv
        monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
        monkeypatch.setenv("API_ID", str(original.api_id))
        monkeypatch.setenv("API_HASH", original.api_hash)
        monkeypatch.setenv("PHONE", original.phone)
        monkeypatch.setenv("TARGET_GROUPS", ",".join(original.target_groups))
        monkeypatch.setenv("MIN_INTERVAL", str(original.min_interval))
        monkeypatch.setenv("MAX_INTERVAL", str(original.max_interval))
        monkeypatch.delenv("TARGET_GROUP", raising=False)

        reloaded = load_settings()
        assert reloaded.target_groups == original.target_groups
        assert reloaded.target_group == original.target_groups[0]
        assert reloaded.api_id == original.api_id
        assert reloaded.min_interval == original.min_interval
        assert reloaded.max_interval == original.max_interval

    def test_save_writes_target_groups(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """save_settings 写入 TARGET_GROUPS 为逗号分隔字符串。"""
        env_path = tmp_path / ".env"
        settings = Settings(
            api_id=1,
            api_hash="x" * 32,
            phone="+8600000000001",
            target_groups=["https://t.me/grp1", "https://t.me/grp2"],
        )

        save_settings(settings, str(env_path))

        content = env_path.read_text(encoding="utf-8")
        assert "TARGET_GROUPS=https://t.me/grp1,https://t.me/grp2" in content
        # 确认文件中不含 TARGET_GROUP= 行（注意：TARGET_GROUPS 包含 TARGET_GROUP 子串）
        for line in content.splitlines():
            assert not line.startswith("TARGET_GROUP=")

    def test_save_with_single_group(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """save_settings 写入单元素 target_groups → 无多余逗号。"""
        env_path = tmp_path / ".env"
        settings = Settings(
            api_id=2,
            api_hash="y" * 32,
            phone="+8600000000002",
            target_groups=["https://t.me/single"],
        )

        save_settings(settings, str(env_path))

        content = env_path.read_text(encoding="utf-8")
        assert "TARGET_GROUPS=https://t.me/single" in content
        # 确认没有多余的逗号
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        tg_line = next(l for l in lines if l.startswith("TARGET_GROUPS="))
        assert tg_line.count(",") == 0
