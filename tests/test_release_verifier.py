"""发布 ZIP 审计的外部行为。"""

from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from scripts.verify_release import ReleaseAuditError, audit_release_zip


def _write_zip(path: Path, members: list[str]) -> None:
    with ZipFile(path, "w") as archive:
        for member in members:
            info = ZipInfo(member)
            info.filename = member
            archive.writestr(info, "ok")
    for member in members:
        if "\\" in member:
            path.write_bytes(
                path.read_bytes().replace(
                    member.replace("\\", "/").encode(), member.encode()
                )
            )


REQUIRED_MEMBERS = [
    "BeWaterTG/BeWaterTG.exe",
    "BeWaterTG/_internal/templates/index.html",
    "BeWaterTG/_internal/static/css/style.css",
    "BeWaterTG/_internal/static/js/app.js",
    "BeWaterTG/.env.example",
    "BeWaterTG/messages.txt",
    "BeWaterTG/messages_hzbjhspa.txt",
]


def test_audit_release_zip_accepts_required_files_and_env_example(tmp_path: Path) -> None:
    archive = tmp_path / "BeWaterTG-win64.zip"
    _write_zip(
        archive,
        REQUIRED_MEMBERS,
    )

    assert audit_release_zip(archive) == ()


def test_audit_release_zip_requires_the_exact_release_archive_name(tmp_path: Path) -> None:
    archive = tmp_path / "BeWaterTG-windows.zip"
    _write_zip(archive, REQUIRED_MEMBERS)

    with pytest.raises(ReleaseAuditError):
        audit_release_zip(archive)


@pytest.mark.parametrize(
    "member",
    [
        "BeWaterTG/session/auth.dat",
        "BeWaterTG/SESSION/auth.dat",
        r"BeWaterTG\\session\\auth.dat",
        "BeWaterTG/db/credentials.txt",
        "BeWaterTG/DB/credentials.txt",
        r"BeWaterTG\\db\\credentials.txt",
        "BeWaterTG/.env",
        "BeWaterTG/nested/.ENV",
        "BeWaterTG/data/ACCOUNT.SESSION",
        "BeWaterTG/data/account.session-journal",
        "BeWaterTG/cache/state.sqlite-wal",
        "BeWaterTG/STATE/be_water.db",
        "BeWaterTG/logs/runtime.log",
        "BeWaterTG/.hermes/cache/item",
        "BeWaterTG/.git/config",
        r"BeWaterTG\\nested\\secret.env",
        "BeWaterTG/nested/.env.production",
    ],
)
def test_audit_release_zip_rejects_sensitive_files_and_directories_in_any_path_form(
    tmp_path: Path, member: str
) -> None:
    archive = tmp_path / "BeWaterTG-win64.zip"
    _write_zip(archive, [*REQUIRED_MEMBERS, member])

    with pytest.raises(ReleaseAuditError):
        audit_release_zip(archive)


@pytest.mark.parametrize(
    "members",
    [
        [member for member in REQUIRED_MEMBERS if member != "BeWaterTG/_internal/static/css/style.css"],
        [member.replace("BeWaterTG/BeWaterTG.exe", "nested/BeWaterTG.exe") for member in REQUIRED_MEMBERS],
    ],
)
def test_audit_release_zip_requires_exact_paths(tmp_path: Path, members: list[str]) -> None:
    archive = tmp_path / "BeWaterTG.zip"
    _write_zip(archive, members)

    with pytest.raises(ReleaseAuditError):
        audit_release_zip(archive)
