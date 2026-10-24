"""发布归档边界：程序和安全样例可进入，运行数据与越界路径必须拒绝。"""
from pathlib import Path
import runpy

import pytest

verify_members = runpy.run_path(str(Path(__file__).resolve().parents[1] / "build.py"))["verify_members"]


def test_release_accepts_program_and_safe_examples() -> None:
    """中文指南和公开样例不应被误判为真实运行数据。"""
    verify_members([
        "BeWaterTG/BeWaterTG.exe",
        "BeWaterTG/README.md",
        "BeWaterTG/_internal/settings.example.json",
        "BeWaterTG/_internal/static/app.js",
    ])


@pytest.mark.parametrize("name", [
    "../private.txt",
    "/private.txt",
    "C:/private.txt",
    "BeWaterTG/private.txt:payload",
    "BeWaterTG/../../private.txt",
    "BeWaterTG\\..\\private.txt",
    "BeWaterTG/.env",
    "BeWaterTG/.env.production",
    "BeWaterTG/settings.json",
    "BeWaterTG/STATE/runtime.bin",
    "BeWaterTG/_internal/cache.db",
    "BeWaterTG/_internal/cache.db-wal",
    "BeWaterTG/_internal/cache.sqlite3",
    "BeWaterTG/_internal/sender.session",
    "BeWaterTG/_internal/sender.session-wal",
    "BeWaterTG/_internal/sender.session-shm",
    "BeWaterTG/_internal/run.log",
])
def test_release_rejects_runtime_and_unsafe_members(name: str) -> None:
    """文件内容尚未读取前就拒绝敏感成员，Windows分隔符同样生效。"""
    with pytest.raises(RuntimeError):
        verify_members([name])
