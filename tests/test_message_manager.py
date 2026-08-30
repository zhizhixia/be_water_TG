from __future__ import annotations

from pathlib import Path

import pytest

import ui.message_manager as message_manager_module
from src.message_paths import LoadedMessageText
from src.runtime_paths import RuntimePaths
from ui.message_manager import MessageManager


def make_paths(tmp_path: Path) -> RuntimePaths:
    """创建 MessageManager 受控读取测试路径。"""
    resources = tmp_path / "resources"
    resources.mkdir()
    return RuntimePaths.from_environment(tmp_path / "local", resources)


def test_controlled_manager_parses_handle_read_text_without_legacy_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受控构造直接解析已读文本，不调用遗留路径读取函数。"""
    paths = make_paths(tmp_path)
    (paths.messages_dir / "messages.txt").write_text("甲,乙", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("受控流程不得按路径重开")

    monkeypatch.setattr(message_manager_module, "load_messages", forbidden)

    manager = MessageManager(
        {"https://t.me/group": "messages.txt"},
        runtime_paths=paths,
    )

    assert manager.get_message("https://t.me/group") in {"甲", "乙"}


def test_controlled_manager_caches_same_reference_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多个群组共享同一引用时只安全读取并构造一次 selector。"""
    paths = make_paths(tmp_path)
    calls: list[str] = []

    def fake_read(reference: str, runtime_paths: RuntimePaths) -> LoadedMessageText:
        calls.append(reference)
        assert runtime_paths is paths
        return LoadedMessageText("user", "shared.txt", "唯一消息")

    monkeypatch.setattr(message_manager_module, "read_message_text", fake_read)

    manager = MessageManager(
        {
            "https://t.me/one": "shared.txt",
            "https://t.me/two": "shared.txt",
        },
        runtime_paths=paths,
    )

    assert calls == ["shared.txt"]
    assert manager.get_message("https://t.me/one") == "唯一消息"
    assert manager.get_message("https://t.me/two") == "唯一消息"


def test_controlled_manager_rejects_empty_parsed_messages(tmp_path: Path) -> None:
    """受控文本解析后无有效消息时固定失败。"""
    paths = make_paths(tmp_path)
    (paths.messages_dir / "empty.txt").write_text(" ,， ", encoding="utf-8")

    with pytest.raises(ValueError, match="消息列表不能为空"):
        MessageManager({"https://t.me/group": "empty.txt"}, runtime_paths=paths)
