"""配置加载的实例隔离与进程环境优先级测试。"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.config import load_settings
from src.runtime_paths import RuntimePaths


REQUIRED_ENV_NAMES = (
    "API_ID",
    "API_HASH",
    "PHONE",
    "TARGET_GROUP",
    "TARGET_GROUPS",
)


def _make_runtime_paths(tmp_path: Path, name: str, env_content: str) -> RuntimePaths:
    """创建带有独立 dotenv 文件的运行路径。"""
    paths = RuntimePaths(tmp_path / f"{name}-resources", tmp_path / name)
    paths.ensure_directories()
    paths.env_path.write_text(env_content, encoding="utf-8")
    return paths


def _clear_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """移除会覆盖测试 dotenv 文件的显式进程环境变量。"""
    for name in REQUIRED_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_load_settings_keeps_runtime_dotenv_values_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """交错加载两个运行路径时，各实例只读取自己的 dotenv 且不污染进程环境。"""
    _clear_required_environment(monkeypatch)
    first_paths = _make_runtime_paths(
        tmp_path,
        "first",
        "API_ID=111\nAPI_HASH=first-hash\nPHONE=first-phone\nTARGET_GROUPS=https://t.me/first\n",
    )
    second_paths = _make_runtime_paths(
        tmp_path,
        "second",
        "API_ID=222\nAPI_HASH=second-hash\nPHONE=second-phone\nTARGET_GROUPS=https://t.me/second\n",
    )

    first = load_settings(first_paths)
    second = load_settings(second_paths)
    first_again = load_settings(first_paths)

    assert (first.api_id, first.target_groups) == (111, ["https://t.me/first"])
    assert (second.api_id, second.target_groups) == (222, ["https://t.me/second"])
    assert (first_again.api_id, first_again.target_groups) == (111, ["https://t.me/first"])
    assert all(name not in os.environ for name in REQUIRED_ENV_NAMES)


def test_load_settings_isolates_runtime_paths_across_threads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """并发重复加载不同运行路径时，每次结果都应属于对应实例。"""
    _clear_required_environment(monkeypatch)
    first_paths = _make_runtime_paths(
        tmp_path,
        "thread-first",
        "API_ID=111\nAPI_HASH=first-hash\nPHONE=first-phone\nTARGET_GROUPS=https://t.me/first\n",
    )
    second_paths = _make_runtime_paths(
        tmp_path,
        "thread-second",
        "API_ID=222\nAPI_HASH=second-hash\nPHONE=second-phone\nTARGET_GROUPS=https://t.me/second\n",
    )

    jobs = [(first_paths, 111), (second_paths, 222)] * 50
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda job: (load_settings(job[0]).api_id, job[1]), jobs))

    assert all(actual == expected for actual, expected in results)
    assert all(name not in os.environ for name in REQUIRED_ENV_NAMES)


def test_load_settings_reads_optional_values_from_each_runtime_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """必填项之外的配置也必须来自当前实例的局部 dotenv 映射。"""
    _clear_required_environment(monkeypatch)
    for name in ("MIN_INTERVAL", "MAX_INTERVAL", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    first_paths = _make_runtime_paths(
        tmp_path,
        "optional-first",
        "API_ID=111\nAPI_HASH=first-hash\nPHONE=first-phone\n"
        "TARGET_GROUPS=https://t.me/first\nMIN_INTERVAL=10\nMAX_INTERVAL=20\nAI_MODEL=first-model\n",
    )
    second_paths = _make_runtime_paths(
        tmp_path,
        "optional-second",
        "API_ID=222\nAPI_HASH=second-hash\nPHONE=second-phone\n"
        "TARGET_GROUPS=https://t.me/second\nMIN_INTERVAL=30\nMAX_INTERVAL=40\nAI_MODEL=second-model\n",
    )

    first = load_settings(first_paths)
    second = load_settings(second_paths)

    assert (first.min_interval, first.max_interval, first.ai_model) == (10, 20, "first-model")
    assert (second.min_interval, second.max_interval, second.ai_model) == (30, 40, "second-model")


def test_explicit_environment_values_override_runtime_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """显式进程环境变量应继续优先于当前实例的 dotenv 值。"""
    _clear_required_environment(monkeypatch)
    paths = _make_runtime_paths(
        tmp_path,
        "environment-override",
        "API_ID=111\nAPI_HASH=file-hash\nPHONE=file-phone\n"
        "TARGET_GROUPS=https://t.me/file\nAI_MODEL=file-model\n",
    )
    monkeypatch.setenv("API_ID", "999")
    monkeypatch.setenv("API_HASH", "environment-hash")
    monkeypatch.setenv("PHONE", "environment-phone")
    monkeypatch.setenv("TARGET_GROUPS", "https://t.me/environment")
    monkeypatch.setenv("AI_MODEL", "environment-model")

    settings = load_settings(paths)

    assert settings.api_id == 999
    assert settings.api_hash == "environment-hash"
    assert settings.phone == "environment-phone"
    assert settings.target_groups == ["https://t.me/environment"]
    assert settings.ai_model == "environment-model"


def test_empty_explicit_environment_value_overrides_runtime_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """环境变量即使为空也应覆盖文件值，并触发既有缺失校验。"""
    _clear_required_environment(monkeypatch)
    paths = _make_runtime_paths(
        tmp_path,
        "empty-environment-override",
        "API_ID=111\nAPI_HASH=file-hash\nPHONE=file-phone\nTARGET_GROUPS=https://t.me/file\n",
    )
    monkeypatch.setenv("API_HASH", "")

    with pytest.raises(ValueError, match="API_HASH is required"):
        load_settings(paths)


def test_load_settings_never_loads_dotenv_into_environment_or_interpolates_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """加载时不得调用全局 dotenv 注入，文件占位符也不得从进程环境展开。"""
    _clear_required_environment(monkeypatch)
    monkeypatch.setenv("SHARED_SECRET", "expanded-from-process")
    paths = _make_runtime_paths(
        tmp_path,
        "no-global-dotenv",
        "API_ID=111\nAPI_HASH=${SHARED_SECRET}\nPHONE=file-phone\nTARGET_GROUPS=https://t.me/file\n",
    )

    def forbidden_load_dotenv(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("load_dotenv 不得被调用")

    monkeypatch.setattr("src.config.load_dotenv", forbidden_load_dotenv)

    settings = load_settings(paths)

    assert settings.api_hash == "${SHARED_SECRET}"


def test_state_database_path_always_comes_from_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """dotenv 与显式环境都不能覆盖 RuntimePaths 固定的状态库路径。"""
    _clear_required_environment(monkeypatch)
    paths = _make_runtime_paths(
        tmp_path,
        "fixed-state-path",
        "API_ID=111\nAPI_HASH=file-hash\nPHONE=file-phone\n"
        "TARGET_GROUPS=https://t.me/file\nSTATE_DB_PATH=file-state.db\n",
    )
    monkeypatch.setenv("STATE_DB_PATH", "environment-state.db")

    settings = load_settings(paths)

    assert settings.state_db_path == str(paths.state_db_path)
