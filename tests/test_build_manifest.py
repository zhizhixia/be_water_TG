"""Windows 发布构建清单的外部行为。"""

from pathlib import Path

import scripts.build_windows as build_windows
from scripts.build_windows import release_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_spec_is_a_real_file_with_explicit_runtime_resources() -> None:
    spec = PROJECT_ROOT / "packaging" / "BeWaterTG.spec"

    assert spec.is_file()
    contents = spec.read_text(encoding="utf-8")
    assert "main.py" in contents
    for resource in ("templates", "static", ".env.example", "messages.txt", "messages_hzbjhspa.txt"):
        assert resource in contents


def test_build_release_uses_the_spec_and_exact_windows_archive_name(
    tmp_path: Path, monkeypatch
) -> None:
    for resource, _ in build_windows.RUNTIME_RESOURCES:
        path = tmp_path / resource
        if path.suffix:
            path.write_text("fixture\n", encoding="utf-8")
        else:
            path.mkdir()
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "BeWaterTG.spec").write_text("# fixture\n", encoding="utf-8")
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> None:
        commands.append(command)
        if "PyInstaller" in command:
            (tmp_path / "dist" / "BeWaterTG").mkdir(parents=True)

    monkeypatch.setattr(build_windows, "_project_python", lambda _: tmp_path / "python.exe")
    monkeypatch.setattr(build_windows.subprocess, "run", run)
    monkeypatch.setattr(build_windows, "_write_zip", lambda source, archive: archive.touch())
    monkeypatch.setattr(build_windows, "_write_checksum", lambda *_: None)

    assert build_windows.build_release(tmp_path) == tmp_path / "dist" / "BeWaterTG-win64.zip"
    assert str(tmp_path / "packaging" / "BeWaterTG.spec") in commands[0]


def test_release_manifest_lists_only_the_explicit_runtime_resources(tmp_path: Path) -> None:
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    (tmp_path / ".env.example").write_text("API_ID=0\n", encoding="utf-8")
    (tmp_path / "messages.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "messages_hzbjhspa.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / ".env").write_text("secret\n", encoding="utf-8")

    assert release_manifest(tmp_path) == (
        (tmp_path / "templates", "templates"),
        (tmp_path / "static", "static"),
        (tmp_path / ".env.example", "."),
        (tmp_path / "messages.txt", "."),
        (tmp_path / "messages_hzbjhspa.txt", "."),
    )
