"""发布 ZIP 审计与本机回环烟雾测试。"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile


class ReleaseAuditError(ValueError):
    """发布归档包含敏感内容或缺少必要文件。"""

REQUIRED_MEMBERS = (
    "BeWaterTG/BeWaterTG.exe",
    "BeWaterTG/templates/index.html",
    "BeWaterTG/static/css/style.css",
    "BeWaterTG/.env.example",
    "BeWaterTG/messages.txt",
    "BeWaterTG/messages_hzbjhspa.txt",
)
RELEASE_ARCHIVE_NAME = "BeWaterTG-win64.zip"


def _normalise(member: str) -> PurePosixPath:
    """将 ZIP 条目统一为大小写无关的 POSIX 路径。"""
    if "\\" in member:
        raise ReleaseAuditError(f"发布包条目不能使用反斜杠: {member}")
    path = PurePosixPath(member)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseAuditError(f"发布包条目路径无效: {member}")
    return PurePosixPath(*(part.lower() for part in path.parts))


def _is_forbidden(path: PurePosixPath) -> bool:
    """判断归档路径是否属于禁止发布的敏感内容。"""
    parts = path.parts
    name = path.name
    if any(part in {"session", "db", "state", "logs", ".hermes", ".git"} for part in parts):
        return True
    if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
        return True
    if ".session" in name:
        return True
    return name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"))


def audit_release_zip(archive: Path) -> tuple[str, ...]:
    """审计 ZIP，拒绝敏感条目并确认发布所需文件齐全。"""
    if archive.name != RELEASE_ARCHIVE_NAME:
        raise ReleaseAuditError(f"发布 ZIP 名必须为: {RELEASE_ARCHIVE_NAME}")
    with ZipFile(archive) as zip_file:
        members = tuple(info.orig_filename for info in zip_file.infolist() if not info.is_dir())
    paths = tuple(_normalise(member) for member in members)
    forbidden = [path.as_posix() for path in paths if _is_forbidden(path)]
    if forbidden:
        raise ReleaseAuditError(f"发布包包含禁止文件: {', '.join(forbidden)}")
    actual_members = set(members)
    missing = [member for member in REQUIRED_MEMBERS if member not in actual_members]
    if missing:
        raise ReleaseAuditError(f"发布包缺少必要文件: {', '.join(missing)}")
    return ()


def wait_for_local_http(url: str, timeout_seconds: float = 15) -> None:
    """等待仅绑定到 127.0.0.1 的 HTTP 就绪响应。"""
    _validate_local_http(url)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"服务未就绪: {url}")


def _validate_local_http(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("烟雾测试仅允许 127.0.0.1 HTTP 地址")


def smoke_test(
    command: Sequence[str],
    url: str = "http://127.0.0.1:5000/",
    start_process: Callable[..., subprocess.Popen] = subprocess.Popen,
    ready_probe: Callable[[str], None] = wait_for_local_http,
) -> None:
    """启动可注入的本机进程，并以 127.0.0.1 探针确认就绪。"""
    _validate_local_http(url)
    process = start_process(list(command))
    try:
        ready_probe(url)
    finally:
        try:
            process.terminate()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    """从命令行审计指定发布 ZIP。"""
    if len(sys.argv) != 2:
        raise SystemExit("用法: python scripts/verify_release.py <release.zip>")
    audit_release_zip(Path(sys.argv[1]))
    print("发布 ZIP 审计通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
