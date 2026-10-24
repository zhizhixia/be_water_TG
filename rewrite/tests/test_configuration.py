"""配置、实际网页 API 和消息库的纵向回归。"""

from __future__ import annotations

import pytest

from bewater.app import create_app
from bewater.config import ConfigStore
from bewater.content import Messages
from bewater.models import DomainError


def test_partial_roundtrip_keeps_disabled_tasks_and_secrets(setup, paths):
    """读回配置后仅保存策略，不能把关闭开关复活或擦掉密钥。"""
    controller, _, values = setup
    config = controller.config
    values["groups"][0]["checkin"] = False
    values["groups"][0]["lottery"] = False
    _, rev = config.load()
    config.update({"groups": values["groups"], "ai_api_key": "test-private"}, rev)
    public = config.public()
    assert public["values"]["api_hash"] == ""
    assert public["values"]["api_hash_set"] is True
    app = create_app(paths)
    client = app.test_client()
    initial = client.get("/api/bootstrap").get_json()
    response = client.post(
        "/api/config",
        json={
            "revision": initial["config"]["revision"],
            "patch": {"daily_limit": 25, "api_hash": "", "ai_enabled": False},
        },
        headers={"X-CSRF-Token": initial["csrf"]},
    )
    assert response.status_code == 200
    saved, _ = config.load()
    assert saved["groups"][0]["checkin"] is False
    assert saved["groups"][0]["lottery"] is False
    assert saved["api_hash"] == "a" * 32
    assert saved["ai_api_key"] == "test-private"


@pytest.mark.parametrize(
    "patch",
    [
        {"ai_enabled": "false"},
        {"daily_limit": True},
        {"question_reply_pct": 101},
        {"reply_delay_min": 100, "reply_delay_max": 20},
        {"proxy_host": "localhost"},
        {"ai_temperature": float("nan")},
    ],
)
def test_invalid_configuration_is_not_persisted(paths, patch):
    """类型或关联错误不得被隐式修正保存。"""
    store = ConfigStore(paths)
    _, revision = store.load()
    with pytest.raises(DomainError):
        store.update(patch, revision)
    assert not paths.config.exists()


def test_version_conflict_and_multiline_prompt(paths):
    """提示词可以完整往返，陈旧页面不能覆盖新值。"""
    store = ConfigStore(paths)
    _, old = store.load()
    text = '第一行，包含逗号\n第二行=值 "引号"'
    response = store.update({"ai_prompt": text}, old)
    assert store.load()[0]["ai_prompt"] == text
    with pytest.raises(DomainError):
        store.update({"daily_limit": 20}, old)
    assert response["revision"] != old


def test_local_web_security_and_empty_ui(paths):
    """实际 Flask 页面与静态资源可达，外站不能驱动配置。"""
    app = create_app(paths)
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").get_json()["configured"] is False
    initial = client.get("/api/bootstrap").get_json()
    assert client.post("/api/control/start", json={}).status_code == 403
    assert client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.post(
            "/api/config",
            json={},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": initial["csrf"]},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/control/start", json={}, headers={"X-CSRF-Token": initial["csrf"]}
        ).status_code
        == 422
    )


def test_legacy_import_is_readonly_and_has_per_group_controls(paths):
    """旧凭据与显式关闭保留，但不能伪造新授权确认。"""
    original = (
        "API_ID=12345\nAPI_HASH="
        + "a" * 32
        + "\nPHONE=+10000000000\nTARGET_GROUPS=https://t.me/first_group,https://t.me/second_group\nDAILY_CHECKIN_ENABLED=false\nLOTTERY_ENABLED=false\n"
    )
    paths.env_path.write_text(original, encoding="utf-8")
    values, _ = ConfigStore(paths).load()
    assert len(values["groups"]) == 2
    assert all(
        not g["checkin"] and not g["lottery"] and not g["authorized"] for g in values["groups"]
    )
    assert paths.env_path.read_text(encoding="utf-8") == original
    assert not paths.config.exists()


def test_txt_preserves_commas_and_stale_edits_fail(paths):
    """新消息每行一条，正文逗号不能被拆成多条。"""
    messages = Messages(paths)
    result = messages.save("new.txt", "你好，最近项目怎么样？\nhello, world", "missing")
    assert result["count"] == 2
    assert result["text"] == "你好，最近项目怎么样？\nhello, world"
    with pytest.raises(DomainError):
        messages.save("new.txt", "覆盖", "missing")
    with pytest.raises(DomainError):
        messages.save("../outside.txt", "不允许", "missing")


def test_old_csv_message_preview_and_explicit_conversion(paths):
    """旧消息按原逗号语义预览，只在保存时转换。"""
    source = paths.messages_dir / "old.txt"
    source.write_text("第一条，第二条,第三条", encoding="utf-8")
    messages = Messages(paths)
    old = messages.load("old.txt")
    assert old["legacy"] and old["count"] == 3
    assert not source.read_text(encoding="utf-8").startswith("#")
    converted = messages.save("old.txt", old["text"], old["revision"])
    assert converted["count"] == 3 and not converted["legacy"]
