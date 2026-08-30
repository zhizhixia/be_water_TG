from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import src.message_paths as message_paths
from src.message_paths import MessagePathError, read_message_text
from src.runtime_paths import RuntimePaths


def make_paths(tmp_path: Path) -> RuntimePaths:
    """构造彼此隔离的可信用户根和资源根。"""
    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    return RuntimePaths.from_environment(
        local_appdata=tmp_path / "local",
        resource_dir=resource_dir,
    )


def test_relative_reference_prefers_user_file_and_ignores_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """普通引用只在可信根查找，用户文件优先且不受工作目录诱饵影响。"""
    paths = make_paths(tmp_path)
    (paths.messages_dir / "messages.txt").write_text("用户内容", encoding="utf-8")
    (paths.resource_dir / "messages.txt").write_text("内置内容", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "messages.txt").write_text("诱饵内容", encoding="utf-8")
    monkeypatch.chdir(cwd)

    loaded = read_message_text("messages.txt", paths)

    assert loaded.kind == "user"
    assert loaded.display_name == "messages.txt"
    assert loaded.text == "用户内容"
    assert str(paths.messages_dir) not in repr(loaded)


def test_builtin_reference_reads_only_resource_root(tmp_path: Path) -> None:
    """builtin 引用明确绕过用户同名文件。"""
    paths = make_paths(tmp_path)
    (paths.messages_dir / "nested").mkdir()
    (paths.resource_dir / "nested").mkdir()
    (paths.messages_dir / "nested" / "sample.txt").write_text("用户", encoding="utf-8")
    (paths.resource_dir / "nested" / "sample.txt").write_text("内置", encoding="utf-8")

    loaded = read_message_text("builtin:nested\\sample.txt", paths)

    assert (loaded.kind, loaded.display_name, loaded.text) == (
        "builtin",
        "nested/sample.txt",
        "内置",
    )


@pytest.mark.parametrize(
    "reference",
    [
        "",
        ".",
        "..",
        "./messages.txt",
        "nested/../messages.txt",
        r"nested\..\messages.txt",
        "/messages.txt",
        r"\messages.txt",
        r"\\server\share\messages.txt",
        r"C:\messages.txt",
        "C:/messages.txt",
        "messages.csv",
        "messages.txt\x00hidden",
        "nested//messages.txt",
        "builtin:",
    ],
)
def test_invalid_references_are_rejected_without_root_disclosure(
    tmp_path: Path, reference: str
) -> None:
    """拒绝绝对、点段、控制符、非 txt 与不规范引用。"""
    paths = make_paths(tmp_path)

    with pytest.raises(MessagePathError) as caught:
        read_message_text(reference, paths)

    assert str(paths.data_dir) not in str(caught.value)
    assert str(paths.resource_dir) not in str(caught.value)


def test_oversize_and_invalid_utf8_use_fixed_safe_errors(tmp_path: Path) -> None:
    """超限与坏编码失败且异常不包含路径或文件内容。"""
    paths = make_paths(tmp_path)
    oversized = paths.messages_dir / "oversized.txt"
    oversized.write_bytes(b"secret" * 20)
    invalid = paths.messages_dir / "invalid.txt"
    invalid.write_bytes(b"top-secret-\xff")

    with pytest.raises(MessagePathError, match="大小限制") as size_error:
        read_message_text("oversized.txt", paths, max_bytes=32)
    with pytest.raises(MessagePathError, match="UTF-8") as encoding_error:
        read_message_text("invalid.txt", paths)

    combined = f"{size_error.value} {encoding_error.value}"
    assert "secret" not in combined
    assert str(paths.messages_dir) not in combined


def _make_junction(link: Path, target: Path) -> None:
    """使用 Windows 原生命令创建无需管理员权限的目录联接。"""
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=False,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail("当前 Windows 环境无法创建测试目录联接")


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows 重解析点")
def test_rejects_ancestor_and_final_reparse_points(tmp_path: Path) -> None:
    """真实目录联接无论位于祖先还是最终 txt 节点均被拒绝。"""
    paths = make_paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escape.txt").write_text("外部秘密", encoding="utf-8")
    ancestor = paths.messages_dir / "linked"
    final = paths.messages_dir / "linked.txt"
    _make_junction(ancestor, outside)
    _make_junction(final, outside)

    for reference in ("linked/escape.txt", "linked.txt"):
        with pytest.raises(MessagePathError, match="不安全") as caught:
            read_message_text(reference, paths)
        assert "外部秘密" not in str(caught.value)


def test_rejects_directory_even_with_txt_suffix(tmp_path: Path) -> None:
    """最终节点必须是普通磁盘文件而不能是目录。"""
    paths = make_paths(tmp_path)
    (paths.messages_dir / "directory.txt").mkdir()

    with pytest.raises(MessagePathError, match="不安全"):
        read_message_text("directory.txt", paths)


def test_rejects_hard_link_to_file_outside_trusted_root(tmp_path: Path) -> None:
    """拒绝通过根内硬链接为根外文件建立任意读取别名。"""
    paths = make_paths(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("外部秘密", encoding="utf-8")
    os.link(outside, paths.messages_dir / "alias.txt")

    with pytest.raises(MessagePathError, match="不安全") as caught:
        read_message_text("alias.txt", paths)

    assert "外部秘密" not in str(caught.value)


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows 共享模式")
def test_open_handle_blocks_replace_and_reads_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读取期间最终句柄不共享删除，替换失败且内容来自同一已打开文件。"""
    paths = make_paths(tmp_path)
    target = paths.messages_dir / "race.txt"
    replacement = tmp_path / "replacement.txt"
    target.write_text("原始内容", encoding="utf-8")
    replacement.write_text("外部内容", encoding="utf-8")
    original_read = message_paths._ReadFile
    replace_errors: list[OSError] = []
    attempted = False

    def read_with_replace_attempt(*args):
        nonlocal attempted
        if not attempted:
            attempted = True
            try:
                os.replace(replacement, target)
            except OSError as error:
                replace_errors.append(error)
        return original_read(*args)

    monkeypatch.setattr(message_paths, "_ReadFile", read_with_replace_attempt)

    loaded = read_message_text("race.txt", paths)

    assert loaded.text == "原始内容"
    assert replace_errors
    assert target.read_text(encoding="utf-8") == "原始内容"


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows 文件句柄")
def test_retries_final_file_close_failure_and_releases_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终文件句柄首次关闭失败时重试，读取结果仍可用且文件可删除。"""
    paths = make_paths(tmp_path)
    nested = paths.messages_dir / "nested"
    nested.mkdir()
    target = nested / "retry.txt"
    target.write_text("可删除内容", encoding="utf-8")
    original_open = message_paths._win_open
    original_close = message_paths._CloseHandle
    opened: list[int] = []
    close_calls: list[int] = []
    really_closed: set[int] = set()

    def recording_open(path: Path, *, directory: bool) -> int:
        handle = original_open(path, directory=directory)
        opened.append(handle)
        return handle

    def fail_first_file_close(handle: int) -> bool:
        close_calls.append(handle)
        if handle == opened[-1] and close_calls.count(handle) == 1:
            return False
        closed = bool(original_close(handle))
        if closed:
            really_closed.add(handle)
        return closed

    monkeypatch.setattr(message_paths, "_win_open", recording_open)
    monkeypatch.setattr(message_paths, "_CloseHandle", fail_first_file_close)

    try:
        loaded = read_message_text("nested/retry.txt", paths)

        assert loaded.text == "可删除内容"
        assert close_calls == [opened[2], opened[1], opened[0], opened[2]]
        assert really_closed == set(opened)
        target.unlink()
        assert not target.exists()
    finally:
        for handle in reversed(opened):
            if handle not in really_closed:
                original_close(handle)


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows 文件句柄")
def test_retries_every_failed_handle_even_after_another_retry_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多个句柄首次失败时全部重试，单个永久失败不阻断其余重试。"""
    paths = make_paths(tmp_path)
    nested = paths.messages_dir / "nested"
    nested.mkdir()
    target = nested / "all-handles.txt"
    target.write_text("多句柄内容", encoding="utf-8")
    original_open = message_paths._win_open
    original_close = message_paths._CloseHandle
    opened: list[int] = []
    close_calls: list[int] = []
    really_closed: set[int] = set()

    def recording_open(path: Path, *, directory: bool) -> int:
        handle = original_open(path, directory=directory)
        opened.append(handle)
        return handle

    def fail_first_close_and_keep_file_open(handle: int) -> bool:
        close_calls.append(handle)
        if close_calls.count(handle) == 1 or handle == opened[-1]:
            return False
        closed = bool(original_close(handle))
        if closed:
            really_closed.add(handle)
        return closed

    monkeypatch.setattr(message_paths, "_win_open", recording_open)
    monkeypatch.setattr(message_paths, "_CloseHandle", fail_first_close_and_keep_file_open)

    try:
        with pytest.raises(MessagePathError, match="^消息文件句柄清理失败$"):
            read_message_text("nested/all-handles.txt", paths)

        assert close_calls == [*reversed(opened), *reversed(opened)]
        assert really_closed == {opened[0], opened[1]}
    finally:
        for handle in reversed(opened):
            if handle not in really_closed:
                original_close(handle)


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows 文件句柄")
def test_permanent_file_close_failure_raises_fixed_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终文件句柄永久关闭失败时不返回内容，仅抛固定安全错误。"""
    paths = make_paths(tmp_path)
    target = paths.messages_dir / "permanent.txt"
    secret = "永久失败秘密"
    target.write_text(secret, encoding="utf-8")
    original_open = message_paths._win_open
    original_close = message_paths._CloseHandle
    opened: list[int] = []
    close_calls: list[int] = []
    really_closed: set[int] = set()

    def recording_open(path: Path, *, directory: bool) -> int:
        handle = original_open(path, directory=directory)
        opened.append(handle)
        return handle

    def never_close_file(handle: int) -> bool:
        close_calls.append(handle)
        if handle == opened[-1]:
            return False
        closed = bool(original_close(handle))
        if closed:
            really_closed.add(handle)
        return closed

    monkeypatch.setattr(message_paths, "_win_open", recording_open)
    monkeypatch.setattr(message_paths, "_CloseHandle", never_close_file)

    try:
        with pytest.raises(MessagePathError) as caught:
            read_message_text("permanent.txt", paths)

        assert str(caught.value) == "消息文件句柄清理失败"
        assert secret not in str(caught.value)
        assert str(paths.messages_dir) not in str(caught.value)
        assert close_calls.count(opened[-1]) == 2
        assert really_closed == {opened[0]}
    finally:
        for handle in reversed(opened):
            if handle not in really_closed:
                original_close(handle)


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows 文件句柄")
def test_read_failure_still_retries_and_closes_every_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读取失败时仍重试并关闭全部句柄，且保留固定安全读取错误。"""
    paths = make_paths(tmp_path)
    nested = paths.messages_dir / "nested"
    nested.mkdir()
    target = nested / "read-failure.txt"
    secret = "读取失败秘密"
    target.write_text(secret, encoding="utf-8")
    original_open = message_paths._win_open
    original_close = message_paths._CloseHandle
    opened: list[int] = []
    close_calls: list[int] = []
    really_closed: set[int] = set()

    def recording_open(path: Path, *, directory: bool) -> int:
        handle = original_open(path, directory=directory)
        opened.append(handle)
        return handle

    def fail_each_first_close(handle: int) -> bool:
        close_calls.append(handle)
        if close_calls.count(handle) == 1:
            return False
        closed = bool(original_close(handle))
        if closed:
            really_closed.add(handle)
        return closed

    monkeypatch.setattr(message_paths, "_win_open", recording_open)
    monkeypatch.setattr(message_paths, "_ReadFile", lambda *args: False)
    monkeypatch.setattr(message_paths, "_CloseHandle", fail_each_first_close)

    try:
        with pytest.raises(MessagePathError) as caught:
            read_message_text("nested/read-failure.txt", paths)

        assert str(caught.value) == "消息文件读取失败"
        assert secret not in str(caught.value)
        assert str(paths.messages_dir) not in str(caught.value)
        assert close_calls == [*reversed(opened), *reversed(opened)]
        assert really_closed == set(opened)
        target.unlink()
        assert not target.exists()
    finally:
        for handle in reversed(opened):
            if handle not in really_closed:
                original_close(handle)


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows 文件句柄")
def test_cleanup_failure_does_not_swallow_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不可恢复清理失败不得把 KeyboardInterrupt 替换为普通业务异常。"""
    paths = make_paths(tmp_path)
    target = paths.messages_dir / "interrupt.txt"
    target.write_text("中断内容", encoding="utf-8")
    original_open = message_paths._win_open
    original_close = message_paths._CloseHandle
    opened: list[int] = []
    really_closed: set[int] = set()

    def recording_open(path: Path, *, directory: bool) -> int:
        handle = original_open(path, directory=directory)
        opened.append(handle)
        return handle

    def never_close_file(handle: int) -> bool:
        if handle == opened[-1]:
            return False
        closed = bool(original_close(handle))
        if closed:
            really_closed.add(handle)
        return closed

    def interrupt_read(*args) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(message_paths, "_win_open", recording_open)
    monkeypatch.setattr(message_paths, "_ReadFile", interrupt_read)
    monkeypatch.setattr(message_paths, "_CloseHandle", never_close_file)

    try:
        with pytest.raises(KeyboardInterrupt):
            read_message_text("interrupt.txt", paths)
    finally:
        for handle in reversed(opened):
            if handle not in really_closed:
                original_close(handle)
