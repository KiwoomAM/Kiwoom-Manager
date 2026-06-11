"""Handler URL이 운영 정책상 192.7.9.45:8700으로 고정되어 있고
env / configure(base_url=...) 어느 쪽으로도 우회할 수 없음을 검증한다."""

import importlib

import pytest

LOCKED_URL = "http://192.7.9.45:8700"


@pytest.fixture(autouse=True)
def _reset_modules():
    """매 테스트마다 rdb_manager / rdb_manager.aio 의 _instance를 초기화."""
    import rdb_manager
    import rdb_manager.aio as aio

    rdb_manager.close()
    yield
    rdb_manager.close()
    aio._instance = None


def _get_sync_client(env_value, monkeypatch):
    # RDB_HANDLER_URL: 잠금이 env를 무시함을 검증하기 위한 이름.
    if env_value is None:
        monkeypatch.delenv("RDB_HANDLER_URL", raising=False)
    else:
        monkeypatch.setenv("RDB_HANDLER_URL", env_value)

    import rdb_manager
    importlib.reload(rdb_manager)
    return rdb_manager._get_client()


def test_sync_no_env_uses_locked_url(monkeypatch):
    client = _get_sync_client(None, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


@pytest.mark.parametrize(
    "wrong_url",
    ["http://wrong-host:1234", "http://localhost:8700", "http://rdb-handler:8700", ""],
)
def test_sync_env_is_ignored(monkeypatch, wrong_url):
    client = _get_sync_client(wrong_url, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


def test_sync_configure_base_url_is_ignored(monkeypatch):
    monkeypatch.delenv("RDB_HANDLER_URL", raising=False)
    import rdb_manager
    importlib.reload(rdb_manager)

    rdb_manager.configure(base_url="http://malicious-host:9999", timeout=10.0)
    client = rdb_manager._get_client()
    assert str(client._http.base_url) == LOCKED_URL
    assert client._http.timeout.connect == 10.0


def test_sync_resolve_base_url_is_constant(monkeypatch):
    monkeypatch.setenv("RDB_HANDLER_URL", "http://something-else:1")
    import rdb_manager
    importlib.reload(rdb_manager)
    assert rdb_manager._resolve_base_url() == LOCKED_URL
