"""升级准备：保留旧配置与会话，并安全导入消息库到用户目录。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import dotenv_values

from bewater.config import legacy_message_name
from bewater.content import Messages
from bewater.message_paths import MessagePathError, read_message_text
from bewater.models import Paths
from bewater.runtime_migration import MigrationResult, migrate_legacy_runtime_data


def prepare_upgrade(legacy: Path, paths: Paths, isolated: bool) -> MigrationResult:
    """隔离验证不接触旧真实数据；正式启动才演练已授权的升级路径。"""
    if isolated:
        return MigrationResult((), (), "ready")
    result = migrate_legacy_runtime_data(legacy, paths)
    if result.status != "ready":
        return result
    report = {"copied_messages": 0, "message_conflicts": 0, "message_manual": 0}
    values = dotenv_values(paths.env_path, interpolate=False) if paths.env_path.exists() else {}
    refs = {"messages.txt"}
    for item in (values.get("MESSAGE_FILES") or "").split(","):
        if "|" in item:
            refs.add(item.split("|", 1)[1].strip())
    editor = Messages(paths)
    for reference in refs:
        _import_message(legacy, paths, editor, reference, report)
    (paths.backups_dir / "v2-upgrade-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _import_message(
    legacy: Path, paths: Paths, editor: Messages, reference: str, report: dict[str, int]
) -> None:
    """读原路径必须经同一受控读取器；不覆盖已存在用户消息。"""
    name = legacy_message_name(reference)
    target = paths.messages_dir / name
    if target.exists():
        return
    resource_ref = reference if reference.startswith("builtin:") else "builtin:" + reference
    try:
        loaded = read_message_text(resource_ref, Paths(legacy, paths.data))
    except MessagePathError:
        report["message_manual"] += 1
        return
    with editor._protected_root():
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            report["message_conflicts"] += 1
            return
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(loaded.text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    report["copied_messages"] += 1
