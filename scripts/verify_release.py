"""发布 ZIP 审计与本机回环烟雾测试。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile


class ReleaseAuditError(ValueError):
    """发布归档包含敏感内容或缺少必要文件。"""


class SmokeTestError(RuntimeError):
    """本机回环烟雾测试未通过严格验收。"""


APP_NAME = "BeWaterTG"
APP_VERSION = "1.0.1"
RELEASE_ARCHIVE_NAME = "BeWaterTG-win64.zip"

# PyInstaller 6 onedir 默认 contents_directory="_internal"：模板与静态资源
# 位于 _internal 下，EXE 同级另放置操作员可见的 .env.example 与消息样例。
REQUIRED_MEMBERS = (
    "BeWaterTG/BeWaterTG.exe",
    "BeWaterTG/_internal/templates/index.html",
    "BeWaterTG/_internal/static/css/style.css",
    "BeWaterTG/_internal/static/js/app.js",
    "BeWaterTG/.env.example",
    "BeWaterTG/messages.txt",
    "BeWaterTG/messages_hzbjhspa.txt",
)

# /api/health 严格期望：三字段齐全、无多余敏感字段，隔离环境未配置。
EXPECTED_HEALTH = {"app": APP_NAME, "version": APP_VERSION, "configured": False}

# 运行数据只允许落在隔离 LOCALAPPDATA/BeWaterTG 下。
RUNTIME_DATA_SUBDIRS = ("config", "state", "session", "logs", "backups")
_FORBIDDEN_PROGRAM_DIR_NAMES = {"logs", "state", "session", "backups", "db", "config"}
_FORBIDDEN_PROGRAM_DIR_SUFFIXES = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".session",
)


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
    return name.endswith(
        (
            ".db",
            ".db-wal",
            ".db-shm",
            ".sqlite",
            ".sqlite-wal",
            ".sqlite-shm",
            ".sqlite3",
            ".sqlite3-wal",
            ".sqlite3-shm",
        )
    )


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


def _validate_local_http(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("烟雾测试仅允许 127.0.0.1 HTTP 地址")


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


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    """请求回环地址并返回 (状态码, 响应体)。"""
    _validate_local_http(url)
    with urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def _http_json(url: str, timeout: float = 5.0) -> tuple[int, object]:
    """请求回环地址并解析 JSON 响应。"""
    status, body = _http_get(url, timeout)
    return status, json.loads(body.decode("utf-8"))


def verify_health(base_url: str) -> None:
    """严格验证 /api/health 三字段且无多余敏感字段。"""
    status, payload = _http_json(base_url + "/api/health")
    if status != 200:
        raise SmokeTestError(f"/api/health 状态码异常: {status}")
    if not isinstance(payload, dict) or set(payload) != set(EXPECTED_HEALTH):
        raise SmokeTestError(f"/api/health 字段集合不符（禁止多余敏感字段）: {payload}")
    if payload != EXPECTED_HEALTH:
        raise SmokeTestError(f"/api/health 字段值不符: {payload}")


ASSET_PATHS = ("/", "/static/css/style.css", "/static/js/app.js")


def verify_assets(base_url: str) -> None:
    """验证首页与静态资源均返回 200 且内容非空。"""
    for path in ASSET_PATHS:
        status, body = _http_get(base_url + path)
        if status != 200:
            raise SmokeTestError(f"{path} 状态码异常: {status}")
        if not body.strip():
            raise SmokeTestError(f"{path} 返回空内容")


def assert_runtime_data_isolated(
    data_dir: Path,
    program_dir: Path,
    baseline_names: set[str],
) -> None:
    """断言运行数据只落在隔离数据目录，程序目录未新增日志/状态/数据库。"""
    for sub in RUNTIME_DATA_SUBDIRS:
        if not (data_dir / sub).is_dir():
            raise SmokeTestError(f"隔离运行数据目录缺失: {data_dir / sub}")
    for entry in sorted(program_dir.iterdir()):
        if entry.name in baseline_names:
            continue
        name = entry.name.lower()
        if entry.is_dir() and name in _FORBIDDEN_PROGRAM_DIR_NAMES:
            raise SmokeTestError(f"程序目录新增运行数据目录: {entry}")
        if name.endswith(_FORBIDDEN_PROGRAM_DIR_SUFFIXES):
            raise SmokeTestError(f"程序目录新增运行数据文件: {entry}")


def assert_port_free(host: str, port: int) -> None:
    """断言端口当前可绑定，烟雾测试启动前使用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError as exc:
        raise SmokeTestError(f"烟雾测试端口被占用 {host}:{port}: {exc}") from exc


def assert_port_released(host: str, port: int, timeout: float = 10.0) -> None:
    """断言停止后端口已释放，可再次绑定。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise SmokeTestError(f"端口未释放 {host}:{port}: {exc}") from exc
            time.sleep(0.2)


def _stop_process(process: subprocess.Popen, timeout: float = 5.0) -> None:
    """终止进程并等待退出；超时则强制结束。"""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _verify_single_instance(
    exe_path: Path,
    launch_dir: Path,
    env: dict[str, str],
    base_url: str,
    *,
    start_process: Callable[..., subprocess.Popen] = subprocess.Popen,
    check_health: Callable[[str], None] = verify_health,
    timeout: float = 20.0,
) -> None:
    """第二实例应快速退出，且不破坏第一实例的健康响应。"""
    second = start_process(
        [str(exe_path)],
        cwd=str(launch_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        second.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        second.kill()
        try:
            second.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise SmokeTestError("第二实例未在超时内退出") from None
    if second.returncode != 0:
        raise SmokeTestError(f"第二实例退出码异常: {second.returncode}")
    check_health(base_url)


def wait_for_health(base_url: str, timeout_seconds: float = 30.0) -> None:
    """轮询 /api/health 直到严格字段验收通过。"""
    _validate_local_http(base_url)
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            verify_health(base_url)
            return
        except (OSError, SmokeTestError, ValueError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise TimeoutError(f"/api/health 未就绪: {base_url} ({last_error})") from last_error


def smoke_test(
    exe_path: str | Path,
    *,
    base_url: str = "http://127.0.0.1:5000",
    local_appdata: Path | None = None,
    launch_dir: Path | None = None,
    start_process: Callable[..., subprocess.Popen] = subprocess.Popen,
    stop_process: Callable[[subprocess.Popen], None] = _stop_process,
    wait_ready: Callable[[str], None] = wait_for_health,
    check_health: Callable[[str], None] = verify_health,
    check_assets: Callable[[str], None] = verify_assets,
    check_isolation: Callable[[Path, Path, set[str]], None] = assert_runtime_data_isolated,
    check_port_free: Callable[[str, int], None] = assert_port_free,
    check_port: Callable[[str, int], None] = assert_port_released,
    single_instance: Callable[..., None] = _verify_single_instance,
) -> None:
    """对发布 EXE 执行严格本机回环验收（隔离 LOCALAPPDATA + 中文/空格启动目录）。"""
    _validate_local_http(base_url)
    exe_path = Path(exe_path).resolve()
    if not exe_path.is_file():
        raise SmokeTestError(f"未找到发布可执行文件: {exe_path}")

    program_dir = exe_path.parent
    port = urlparse(base_url).port or 80
    check_port_free("127.0.0.1", port)

    smoke_root = Path(tempfile.mkdtemp(prefix="bewatertg-smoke-"))
    data_dir = (local_appdata or smoke_root / "LocalAppData").resolve()
    launch = (launch_dir or smoke_root / "启动目录" / "中文 路径").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    launch.mkdir(parents=True, exist_ok=True)

    baseline = {entry.name for entry in program_dir.iterdir()}
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(data_dir)

    first_log = smoke_root / "first-instance.log"
    process = start_process(
        [str(exe_path)],
        cwd=str(launch),
        env=env,
        stdout=first_log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    try:
        wait_ready(base_url)
        check_health(base_url)
        check_assets(base_url)
        single_instance(exe_path, launch, env, base_url, start_process=start_process)
        check_isolation(data_dir / "BeWaterTG", program_dir, baseline)
    finally:
        stop_process(process)
    check_port("127.0.0.1", port)


def main() -> int:
    """从命令行审计指定发布 ZIP。"""
    if len(sys.argv) != 2:
        raise SystemExit("用法: python scripts/verify_release.py <release.zip>")
    audit_release_zip(Path(sys.argv[1]))
    print("发布 ZIP 审计通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
