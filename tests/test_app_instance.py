from __future__ import annotations

from src.app_instance import APP_NAME, APP_VERSION, AppInstance, is_local_bewater_service


class SharedMutex:
    held = False

    def acquire(self) -> bool:
        if type(self).held:
            return False
        type(self).held = True
        return True

    def release(self) -> None:
        type(self).held = False


def test_second_instance_detects_running_local_service() -> None:
    """第二实例不取得应用锁，并仅通过健康检查确认已有本地服务。"""
    first = AppInstance(lock_factory=SharedMutex, health_probe=lambda: True)
    second = AppInstance(lock_factory=SharedMutex, health_probe=lambda: True)

    assert first.acquire()
    assert not second.acquire()
    assert second.existing_service_is_ours()
    first.release()


def test_health_probe_rejects_unexpected_response(monkeypatch) -> None:
    """健康探针只接受完整的固定安全响应。"""
    class Response:
        status = 200

        def read(self) -> bytes:
            return b'{"app":"other","version":"1.0.1","configured":true}'

    class Connection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert host == "127.0.0.1"
            assert port == 5000

        def request(self, method: str, path: str) -> None:
            assert (method, path) == ("GET", "/api/health")

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr("src.app_instance.HTTPConnection", Connection)

    assert not is_local_bewater_service()
    assert APP_NAME == "BeWaterTG"
    assert APP_VERSION == "1.0.1"
