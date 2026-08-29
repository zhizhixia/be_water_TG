from __future__ import annotations

import ctypes

from src.app_instance import APP_NAME, APP_VERSION, AppInstance, _WindowsMutex, is_local_bewater_service


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


def test_windows_mutex_second_same_name_acquire_returns_false_and_closes_handle() -> None:
    class Function:
        def __init__(self, callback: object) -> None:
            self._callback = callback

        def __call__(self, *args: object) -> object:
            return self._callback(*args)

    class Kernel32:
        def __init__(self) -> None:
            self.CreateMutexW = Function(self.create_mutex)
            self.CloseHandle = Function(self.close_handle)
            self.last_error = 0
            self.closed: list[int] = []

        def create_mutex(self, _security: object, _owner: bool, name: str) -> int:
            assert name == "Local\\BeWaterTG-v1"
            self.last_error = 183
            return 123

        def close_handle(self, handle: int) -> bool:
            self.closed.append(handle)
            return True

    kernel32 = Kernel32()
    mutex = _WindowsMutex(
        kernel32=kernel32,
        set_last_error=lambda value: setattr(kernel32, "last_error", value),
        get_last_error=lambda: kernel32.last_error,
    )

    assert not mutex.acquire()
    assert kernel32.closed == [123]


def test_windows_mutex_uses_last_error_aware_kernel32_loader(monkeypatch) -> None:
    """默认加载器必须将 Win32 last-error 映射到 ctypes 线程槽。"""
    calls: list[tuple[str, bool]] = []

    class Function:
        def __call__(self, *_args: object) -> int:
            return 0

    class Kernel32:
        CreateMutexW = Function()
        CloseHandle = Function()

    def load(name: str, *, use_last_error: bool) -> Kernel32:
        calls.append((name, use_last_error))
        return Kernel32()

    monkeypatch.setattr(ctypes, "WinDLL", load)
    _WindowsMutex()

    assert calls == [("kernel32", True)]


def test_windows_mutex_configures_pointer_sized_kernel32_abi() -> None:
    class Function:
        def __call__(self, *_args: object) -> int:
            return 0

    class Kernel32:
        CreateMutexW = Function()
        CloseHandle = Function()

    kernel32 = Kernel32()
    _WindowsMutex(kernel32=kernel32)

    assert kernel32.CreateMutexW.restype is ctypes.c_void_p
    assert kernel32.CreateMutexW.argtypes == [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    assert kernel32.CloseHandle.restype is ctypes.c_bool
    assert kernel32.CloseHandle.argtypes == [ctypes.c_void_p]


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
