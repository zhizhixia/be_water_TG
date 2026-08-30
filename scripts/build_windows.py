"""Windows 发布构建入口。"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


RUNTIME_RESOURCES = (
    ("templates", "templates"),
    ("static", "static"),
    (".env.example", "."),
    ("messages.txt", "."),
    ("messages_hzbjhspa.txt", "."),
)


def release_manifest(project_root: Path) -> tuple[tuple[Path, str], ...]:
    """返回发布包允许携带的显式运行时资源清单。"""
    manifest = [(project_root / source, destination) for source, destination in RUNTIME_RESOURCES]
    missing = [str(path) for path, _ in manifest if not path.exists()]
    if missing:
        raise FileNotFoundError(f"发布资源缺失: {', '.join(missing)}")
    return tuple(manifest)


def _project_python(project_root: Path) -> Path:
    """返回项目虚拟环境中的 Python 解释器。"""
    python = project_root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError(f"未找到项目环境: {python}")
    return python


def _write_zip(source: Path, archive: Path) -> None:
    """将 onedir 输出压缩为可审计的 ZIP。"""
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                member = ZipInfo(path.relative_to(source.parent).as_posix(), (1980, 1, 1, 0, 0, 0))
                member.compress_type = ZIP_DEFLATED
                zip_file.writestr(member, path.read_bytes())


def _write_checksum(archive: Path, checksum_file: Path) -> None:
    """写入 ZIP 的 SHA-256 校验文件。"""
    digest = hashlib.sha256()
    with archive.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_file.write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="utf-8", newline="\n")


def build_release(project_root: Path) -> Path:
    """使用项目环境构建 onedir 发布包、ZIP 和校验和。"""
    project_root = project_root.resolve()
    release_manifest(project_root)
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"
    output_dir = dist_dir / "BeWaterTG"
    archive = dist_dir / "BeWaterTG-win64.zip"
    checksum_file = dist_dir / "SHA256SUMS.txt"
    shutil.rmtree(output_dir, ignore_errors=True)
    archive.unlink(missing_ok=True)
    checksum_file.unlink(missing_ok=True)
    subprocess.run(
        [
            str(_project_python(project_root)),
            "-m",
            "PyInstaller",
            str(project_root / "packaging" / "BeWaterTG.spec"),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(build_dir),
        ],
        check=True,
        cwd=project_root,
    )
    if not output_dir.is_dir():
        raise RuntimeError(f"PyInstaller 未生成发布目录: {output_dir}")
    _write_zip(output_dir, archive)
    _write_checksum(archive, checksum_file)
    subprocess.run(
        [str(_project_python(project_root)), str(project_root / "scripts" / "verify_release.py"), str(archive)],
        check=True,
        cwd=project_root,
    )
    return archive


def main() -> int:
    """执行本地 Windows 发布构建。"""
    archive = build_release(Path(__file__).resolve().parents[1])
    print(archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
