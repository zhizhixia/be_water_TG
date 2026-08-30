"""发布构建与审计脚本的严格验收逻辑（不真跑 PyInstaller）。"""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

import scripts.build_windows as build_windows
import scripts.verify_release as verify
from scripts.verify_release import SmokeTestError, audit_release_zip


class FakeResponse:
    """最小可用的 urllib 响应替身。"""

    def __init__(self, status: int = 200, body: bytes = b"ok"):
        self.status = status
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _json_response(payload: object, status: int = 200) -> FakeResponse:
    return FakeResponse(status, json.dumps(payload).encode("utf-8"))


def _free_port() -> int:
    """返回一个刚被释放的本地回环空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ===== /api/health 严格字段验收 =====


@pytest.mark.parametrize(
    "payload",
    [
        {"app": "BeWaterTG", "version": "1.0.1", "configured": False},
    ],
)
def test_verify_health_accepts_exact_fields(monkeypatch, payload: dict) -> None:
    requested: list[str] = []
    monkeypatch.setattr(
        verify,
        "urlopen",
        lambda url, timeout=None: requested.append(url) or _json_response(payload),
    )

    verify.verify_health("http://127.0.0.1:5000")

    assert requested == ["http://127.0.0.1:5000/api/health"]


@pytest.mark.parametrize(
    "payload",
    [
        {"app": "BeWaterTG", "version": "1.0.0", "configured": False},
        {"app": "BeWaterTG", "version": "1.0.1", "configured": True},
        {"app": "Other", "version": "1.0.1", "configured": False},
        {"app": "BeWaterTG", "version": "1.0.1"},
        {"app": "BeWaterTG", "version": "1.0.1", "configured": False, "api_key": "x"},
        {"app": "BeWaterTG", "version": "1.0.1", "configured": False, "phone": "+861"},
        {"app": "BeWaterTG", "version": "1.0.1", "configured": False, "session": "s"},
    ],
)
def test_verify_health_rejects_wrong_or_extra_fields(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(verify, "urlopen", lambda url, timeout=None: _json_response(payload))

    with pytest.raises(SmokeTestError):
        verify.verify_health("http://127.0.0.1:5000")


def test_verify_health_rejects_non_200(monkeypatch) -> None:
    monkeypatch.setattr(
        verify,
        "urlopen",
        lambda url, timeout=None: _json_response(
            {"app": "BeWaterTG", "version": "1.0.1", "configured": False}, status=500
        ),
    )

    with pytest.raises(SmokeTestError):
        verify.verify_health("http://127.0.0.1:5000")


# ===== 静态资源验收 =====


def test_verify_assets_accepts_200_non_empty(monkeypatch) -> None:
    requested: list[str] = []
    monkeypatch.setattr(
        verify,
        "urlopen",
        lambda url, timeout=None: requested.append(url) or FakeResponse(200, b"<html></html>"),
    )

    verify.verify_assets("http://127.0.0.1:5000")

    assert requested == [
        "http://127.0.0.1:5000/",
        "http://127.0.0.1:5000/static/css/style.css",
        "http://127.0.0.1:5000/static/js/app.js",
    ]


def test_verify_assets_rejects_empty_body(monkeypatch) -> None:
    monkeypatch.setattr(verify, "urlopen", lambda url, timeout=None: FakeResponse(200, b"   "))

    with pytest.raises(SmokeTestError):
        verify.verify_assets("http://127.0.0.1:5000")


def test_verify_assets_rejects_non_200(monkeypatch) -> None:
    monkeypatch.setattr(verify, "urlopen", lambda url, timeout=None: FakeResponse(404, b"x"))

    with pytest.raises(SmokeTestError):
        verify.verify_assets("http://127.0.0.1:5000")


# ===== 运行数据落点断言 =====


def _make_isolated_dirs(tmp_path: Path) -> tuple[Path, Path, set[str]]:
    data_dir = tmp_path / "BeWaterTG"
    for sub in verify.RUNTIME_DATA_SUBDIRS:
        (data_dir / sub).mkdir(parents=True)
    program_dir = tmp_path / "program"
    program_dir.mkdir()
    (program_dir / "BeWaterTG.exe").write_bytes(b"MZ")
    (program_dir / "_internal").mkdir()
    return data_dir, program_dir, {"BeWaterTG.exe", "_internal"}


def test_assert_runtime_data_isolated_accepts_expected_layout(tmp_path: Path) -> None:
    data_dir, program_dir, baseline = _make_isolated_dirs(tmp_path)

    verify.assert_runtime_data_isolated(data_dir, program_dir, baseline)


def test_assert_runtime_data_isolated_rejects_missing_subdir(tmp_path: Path) -> None:
    data_dir = tmp_path / "BeWaterTG"
    data_dir.mkdir()
    program_dir = tmp_path / "program"
    program_dir.mkdir()
    (program_dir / "BeWaterTG.exe").write_bytes(b"MZ")

    with pytest.raises(SmokeTestError):
        verify.assert_runtime_data_isolated(data_dir, program_dir, {"BeWaterTG.exe"})


@pytest.mark.parametrize(
    "entry",
    [
        "be_water.log",
        "be_water.db",
        "be_water.db-journal",
        "state.sqlite-journal",
        "state.sqlite3-journal",
        "sender_session.session",
    ],
)
def test_assert_runtime_data_isolated_rejects_program_dir_data_file(
    tmp_path: Path, entry: str
) -> None:
    data_dir, program_dir, baseline = _make_isolated_dirs(tmp_path)
    (program_dir / entry).write_bytes(b"x")

    with pytest.raises(SmokeTestError):
        verify.assert_runtime_data_isolated(data_dir, program_dir, baseline)


@pytest.mark.parametrize("entry", ["logs", "state", "session", "backups"])
def test_assert_runtime_data_isolated_rejects_program_dir_data_subdir(
    tmp_path: Path, entry: str
) -> None:
    data_dir, program_dir, baseline = _make_isolated_dirs(tmp_path)
    (program_dir / entry).mkdir()

    with pytest.raises(SmokeTestError):
        verify.assert_runtime_data_isolated(data_dir, program_dir, baseline)


# ===== 端口释放 =====


def test_assert_port_free_raises_when_occupied() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])

        with pytest.raises(SmokeTestError):
            verify.assert_port_free("127.0.0.1", port)


def test_assert_port_free_passes_when_available() -> None:
    verify.assert_port_free("127.0.0.1", _free_port())


def test_assert_port_released_passes_when_available() -> None:
    verify.assert_port_released("127.0.0.1", _free_port(), timeout=2)


# ===== 单实例 =====


class _SecondProcess:
    """模拟第二实例进程对象。"""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode: int | None = None
        self._final = returncode
        self.killed = False

    def wait(self, timeout: float = 0.0) -> int:
        self.returncode = self._final
        return self._final

    def kill(self) -> None:
        self.killed = True


def test_single_instance_accepts_quick_clean_exit(monkeypatch) -> None:
    health_checks: list[str] = []
    started: list[list[str]] = []

    def start_process(command, **kwargs):
        started.append(command)
        return _SecondProcess(0)

    verify._verify_single_instance(
        Path("BeWaterTG.exe"),
        Path("launch"),
        {"LOCALAPPDATA": "x"},
        "http://127.0.0.1:5000",
        start_process=start_process,
        check_health=lambda url: health_checks.append(url),
    )

    assert started == [["BeWaterTG.exe"]]
    assert health_checks == ["http://127.0.0.1:5000"]


def test_single_instance_rejects_nonzero_exit() -> None:
    def start_process(command, **kwargs):
        return _SecondProcess(1)

    with pytest.raises(SmokeTestError):
        verify._verify_single_instance(
            Path("BeWaterTG.exe"),
            Path("launch"),
            {},
            "http://127.0.0.1:5000",
            start_process=start_process,
            check_health=lambda url: None,
        )


def test_single_instance_rejects_timeout_and_kills() -> None:
    class Hung:
        returncode: int | None = None
        killed = False

        def wait(self, timeout: float = 0.0) -> None:
            raise subprocess.TimeoutExpired("BeWaterTG.exe", timeout)

        def kill(self) -> None:
            self.killed = True

    second = Hung()

    def start_process(command, **kwargs):
        return second

    with pytest.raises(SmokeTestError):
        verify._verify_single_instance(
            Path("BeWaterTG.exe"),
            Path("launch"),
            {},
            "http://127.0.0.1:5000",
            start_process=start_process,
            check_health=lambda url: None,
        )

    assert second.killed


# ===== smoke_test 编排 =====


def _fake_exe(tmp_path: Path) -> Path:
    exe = tmp_path / "program" / "BeWaterTG.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    return exe


def test_smoke_test_orchestrates_strict_sequence(tmp_path: Path) -> None:
    exe = _fake_exe(tmp_path)
    steps: list[str] = []
    started: list[tuple[list[str], str, str]] = []

    def start_process(command, **kwargs):
        started.append((command, kwargs["cwd"], kwargs["env"]["LOCALAPPDATA"]))

        class Proc:
            def poll(self) -> int | None:
                return 0

            def terminate(self) -> None:
                steps.append("terminate")

            def wait(self, timeout: float = 0.0) -> int:
                return 0

            def kill(self) -> None:
                steps.append("kill")

        return Proc()

    def mark(name: str):
        def _f(*args: object, **kwargs: object) -> None:
            steps.append(name)

        return _f

    verify.smoke_test(
        exe,
        base_url="http://127.0.0.1:5000",
        local_appdata=tmp_path / "localdata",
        launch_dir=tmp_path / "启动 目录",
        start_process=start_process,
        stop_process=lambda p: steps.append("stop"),
        wait_ready=mark("wait_ready"),
        check_health=mark("check_health"),
        check_assets=mark("check_assets"),
        check_isolation=mark("isolation"),
        check_port_free=mark("port_free"),
        check_port=mark("port_released"),
        single_instance=lambda *a, **k: steps.append("single"),
    )

    assert steps == [
        "port_free",
        "wait_ready",
        "check_health",
        "check_assets",
        "single",
        "isolation",
        "stop",
        "port_released",
    ]
    command, cwd, local = started[0]
    assert command == [str(exe)]
    assert cwd == str(tmp_path / "启动 目录")
    assert local == str(tmp_path / "localdata")


def test_smoke_test_stops_process_when_step_fails(tmp_path: Path) -> None:
    exe = _fake_exe(tmp_path)
    stops: list[str] = []

    def start_process(command, **kwargs):
        class Proc:
            def poll(self) -> int | None:
                return None

            def terminate(self) -> None:
                stops.append("terminate")

            def wait(self, timeout: float = 0.0) -> int:
                return 0

            def kill(self) -> None:
                stops.append("kill")

        return Proc()

    with pytest.raises(SmokeTestError):
        verify.smoke_test(
            exe,
            local_appdata=tmp_path / "localdata",
            launch_dir=tmp_path / "启动 目录",
            start_process=start_process,
            stop_process=lambda p: stops.append("stop"),
            wait_ready=lambda url: (_ for _ in ()).throw(SmokeTestError("boom")),
            check_port_free=lambda h, p: None,
            check_port=lambda h, p: stops.append("port_released"),
        )

    assert "stop" in stops
    assert "port_released" not in stops


# ===== build_windows 行为 =====


def test_project_python_prefers_sys_executable(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "python.exe"
    fake.write_bytes(b"x")
    monkeypatch.setattr(build_windows.sys, "executable", str(fake))

    assert build_windows._project_python(tmp_path) == fake


def test_project_python_falls_back_to_venv(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(build_windows.sys, "executable", "")
    venv_py = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"x")

    assert build_windows._project_python(tmp_path) == venv_py


def test_copy_operator_resources_copies_only_allowed_files(tmp_path: Path) -> None:
    for name in build_windows.OPERATOR_RESOURCES:
        (tmp_path / name).write_text("sample\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_ID=secret\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    build_windows._copy_operator_resources(tmp_path, out)

    for name in build_windows.OPERATOR_RESOURCES:
        assert (out / name).is_file()
    assert not (out / ".env").exists()


def test_build_release_invokes_smoke_on_built_exe(tmp_path: Path, monkeypatch) -> None:
    for resource, _ in build_windows.RUNTIME_RESOURCES:
        path = tmp_path / resource
        if path.suffix:
            path.write_text("x", encoding="utf-8")
        else:
            path.mkdir()
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "BeWaterTG.spec").write_text("# fixture\n", encoding="utf-8")
    smoke_calls: list[Path] = []

    def run(command, **kwargs):
        if "PyInstaller" in command:
            (tmp_path / "dist" / "BeWaterTG").mkdir(parents=True)

    monkeypatch.setattr(build_windows, "_project_python", lambda _: tmp_path / "python.exe")
    monkeypatch.setattr(build_windows.subprocess, "run", run)
    monkeypatch.setattr(build_windows, "_copy_operator_resources", lambda *_: None)
    monkeypatch.setattr(build_windows, "_write_zip", lambda source, archive: archive.touch())
    monkeypatch.setattr(build_windows, "_write_checksum", lambda *_: None)

    result = build_windows.build_release(tmp_path, smoke_test=lambda exe: smoke_calls.append(exe))

    assert result == tmp_path / "dist" / "BeWaterTG-win64.zip"
    assert smoke_calls == [tmp_path / "dist" / "BeWaterTG" / "BeWaterTG.exe"]


# ===== ZIP 审计大小写 / 反斜杠绕过 =====

_REQUIRED = [
    "BeWaterTG/BeWaterTG.exe",
    "BeWaterTG/_internal/templates/index.html",
    "BeWaterTG/_internal/static/css/style.css",
    "BeWaterTG/_internal/static/js/app.js",
    "BeWaterTG/.env.example",
    "BeWaterTG/messages.txt",
    "BeWaterTG/messages_hzbjhspa.txt",
]


def _write_zip(path: Path, members: list[str]) -> None:
    with ZipFile(path, "w") as archive:
        for member in members:
            info = ZipInfo(member)
            info.filename = member
            archive.writestr(info, "ok")


@pytest.mark.parametrize(
    "member",
    [
        "BeWaterTG/.env",
        "BeWaterTG/.ENV",
        r"BeWaterTG\\.env",
        "BeWaterTG/_internal/.env.production",
        "BeWaterTG/_internal/state/be_water.db",
        "BeWaterTG/nested/SESSION/auth.dat",
        "BeWaterTG/nested/account.SESSION",
    ],
)
def test_audit_rejects_sensitive_members_in_any_case_or_separator(
    tmp_path: Path, member: str
) -> None:
    archive = tmp_path / "BeWaterTG-win64.zip"
    _write_zip(archive, [*_REQUIRED, member])

    with pytest.raises(verify.ReleaseAuditError):
        audit_release_zip(archive)


def test_audit_allows_env_example_member(tmp_path: Path) -> None:
    archive = tmp_path / "BeWaterTG-win64.zip"
    _write_zip(archive, [*_REQUIRED, "BeWaterTG/_internal/.env.example"])

    assert audit_release_zip(archive) == ()
