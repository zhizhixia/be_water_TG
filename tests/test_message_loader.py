from __future__ import annotations

import pytest

from src.message_loader import load_messages, validate_messages


class TestLoadMessages:
    """load_messages 函数测试。"""

    def test_load_messages_normal(self, tmp_path):
        """正常逗号分隔文件加载。"""
        f = tmp_path / "messages.txt"
        f.write_text("你好,今天天气不错,记得喝水,学习加油", encoding="utf-8")
        result = load_messages(str(f))
        assert result == ["你好", "今天天气不错", "记得喝水", "学习加油"]

    def test_load_messages_chinese(self, tmp_path):
        """中文消息加载。"""
        f = tmp_path / "messages.txt"
        f.write_text("你好世界,测试消息,中文内容", encoding="utf-8")
        result = load_messages(str(f))
        assert len(result) == 3
        assert result[0] == "你好世界"

    def test_load_messages_mixed(self, tmp_path):
        """中英文混合消息。"""
        f = tmp_path / "messages.txt"
        f.write_text("Hello,你好,World,世界", encoding="utf-8")
        result = load_messages(str(f))
        assert len(result) == 4

    def test_load_messages_blank_filter(self, tmp_path):
        """空白消息过滤。"""
        f = tmp_path / "messages.txt"
        f.write_text("你好,,  ,世界,  ", encoding="utf-8")
        result = load_messages(str(f))
        assert result == ["你好", "世界"]

    def test_load_messages_file_not_found(self):
        """文件不存在时抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="消息文件不存在"):
            load_messages("nonexistent_file.txt")

    def test_load_messages_empty_file(self, tmp_path):
        """空文件返回空列表。"""
        f = tmp_path / "messages.txt"
        f.write_text("", encoding="utf-8")
        result = load_messages(str(f))
        assert result == []


class TestValidateMessages:
    """validate_messages 函数测试。"""

    def test_validate_messages_empty_list(self):
        """空列表抛出 ValueError。"""
        with pytest.raises(ValueError, match="消息列表不能为空"):
            validate_messages([])

    def test_validate_messages_valid_list(self):
        """有效列表不抛出异常。"""
        validate_messages(["消息1", "消息2"])  # 不应抛出异常
