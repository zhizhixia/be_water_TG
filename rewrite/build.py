"""构建完整目录 Windows 发布包，并执行真实隔离回环冒烟。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / ".hermes/rewrite/release"


def verify_members(names: list[str]) -> None:
    """禁止真实配置、会话、数据库、日志或开发文件进入发布包。"""
    for name in names:
        parts = Path(name).parts
        suffix = Path(name).suffix.lower()
        if any(
            p in {".env", "settings.json", ".git", ".hermes", "tests", "logs", "state", "session"}
            for p in parts
        ):
            raise RuntimeError("发布包含禁止文件：" + name)
        if suffix in {".db", ".sqlite", ".session", ".log", ".journal"} or name.endswith(
            "session-journal"
        ):
            raise RuntimeError("发布包含运行数据：" + name)


def get(port: int, route: str) -> tuple[bytes, str]:
    """只请求明确本机回环 URL。"""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{route}", timeout=2) as response:
        return response.read(), response.headers.get("Content-Type", "")


def smoke(executable: Path) -> dict[str, object]:
    """真实启动 EXE，验证健康、资源、单实例和进程退出后的端口释放。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="bewater-release-", dir=OUTPUT) as temporary:
        data = Path(temporary) / "data"
        args = [str(executable), "--port", str(port), "--isolated-data-dir", str(data)]
        log = open(Path(temporary) / "process-output.txt", "w", encoding="utf-8")
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        try:
            health = None
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("EXE 在就绪前退出")
                try:
                    health = json.loads(get(port, "/api/health")[0])
                    break
                except (OSError, ValueError):
                    time.sleep(0.25)
            if health != {"app": "BeWaterTG", "version": "2.0.0", "configured": False}:
                raise RuntimeError("EXE 健康结果不符合预期")
            for route, marker in (
                ("/", b"<!doctype html>"),
                ("/static/app.js", b"use strict"),
                ("/static/app.css", b"color-scheme"),
            ):
                if marker not in get(port, route)[0]:
                    raise RuntimeError("发布资源缺失：" + route)
            second = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15
            )
            if second.returncode != 0 or process.poll() is not None:
                raise RuntimeError("第二实例没有安全退出")
            if (data / "session/sender_session.session").exists():
                raise RuntimeError("打开控制台竟创建了 Telegram session")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
        for _ in range(40):
            try:
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.25)
        else:
            raise RuntimeError("EXE 退出后端口仍被占用")
        return {
            "health": health,
            "static_resources": True,
            "second_instance_exits": True,
            "no_telegram_session": True,
            "port_released_after_termination": True,
        }


def main() -> None:
    """从显式源码与资源清单构建，不打包项目工作树。"""
    if os.name != "nt":
        raise SystemExit("此构建器仅用于 Windows")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noupx",
        "--onedir",
        "--name",
        "BeWaterTG",
        "--paths",
        str(ROOT),
        "--distpath",
        str(OUTPUT / "dist"),
        "--workpath",
        str(OUTPUT / "work"),
        "--specpath",
        str(OUTPUT / "spec"),
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "flet",
        "--exclude-module",
        "tkinter",
        "--collect-data",
        "tzdata",
        "--hidden-import",
        "tzdata",
    ]
    for folder in ("templates", "static"):
        command += ["--add-data", f"{ROOT / folder}{os.pathsep}{folder}"]
    for name in ("messages.txt", "settings.example.json"):
        command += ["--add-data", f"{ROOT / name}{os.pathsep}."]
    command.append(str(ROOT / "main.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    bundle = OUTPUT / "dist/BeWaterTG"
    paths = [p for p in bundle.rglob("*") if p.is_file()]
    names = [p.relative_to(bundle.parent).as_posix() for p in paths]
    verify_members(names)
    result = smoke(bundle / "BeWaterTG.exe")
    archive = OUTPUT / "BeWaterTG-win64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for path, name in zip(paths, names):
            target.write(path, name)
    with zipfile.ZipFile(archive) as source:
        verify_members(source.namelist())
        if source.testzip() is not None:
            raise RuntimeError("ZIP CRC 检查失败")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (OUTPUT / "SHA256SUMS.txt").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    report = {
        "artifact": str(archive),
        "sha256": digest,
        "files": len(paths),
        "zip_crc_valid": True,
        "smoke": result,
    }
    (OUTPUT / "build-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
