"""Handler URL이 운영 정책상 192.7.9.45:8200으로 고정되어 있고
env / configure(base_url=...) 어느 쪽으로도 우회할 수 없음을 검증한다."""

import importlib

import pytest

LOCKED_URL = "http://192.7.9.45:8200"


@pytest.fixture(autouse=True)
def _reset_modules(monkeypatch):
    """매 테스트마다 winus_handler / winus_handler.aio 의 _instance를 초기화."""
    import winus_handler
    import winus_handler.aio as aio

    winus_handler.close()
    yield
    winus_handler.close()
    # async close는 호출하지 않음 — 테스트마다 _instance None이면 충분
    aio._instance = None


def _get_sync_client(env_value: str | None, monkeypatch):
    if env_value is None:
        monkeypatch.delenv("WINUS_HANDLER_URL", raising=False)
    else:
        monkeypatch.setenv("WINUS_HANDLER_URL", env_value)

    import winus_handler
    importlib.reload(winus_handler)
    return winus_handler._get_client()


def _get_async_client(env_value: str | None, monkeypatch):
    if env_value is None:
        monkeypatch.delenv("WINUS_HANDLER_URL", raising=False)
    else:
        monkeypatch.setenv("WINUS_HANDLER_URL", env_value)

    import winus_handler.aio as aio
    importlib.reload(aio)
    return aio._get_client()


# ── 1. env가 없을 때 운영 URL 사용 ─────────────────────────


def test_sync_no_env_uses_locked_url(monkeypatch):
    client = _get_sync_client(None, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


def test_async_no_env_uses_locked_url(monkeypatch):
    client = _get_async_client(None, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


# ── 2. env로 다른 URL을 줘도 무시되어야 함 ──────────────────


@pytest.mark.parametrize(
    "wrong_url",
    [
        "http://wrong-host:1234",
        "http://localhost:8200",
        "http://winus-handler:8200",
        "",
    ],
)
def test_sync_env_is_ignored(monkeypatch, wrong_url):
    client = _get_sync_client(wrong_url, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


@pytest.mark.parametrize(
    "wrong_url",
    [
        "http://wrong-host:1234",
        "http://localhost:8200",
        "http://winus-handler:8200",
        "",
    ],
)
def test_async_env_is_ignored(monkeypatch, wrong_url):
    client = _get_async_client(wrong_url, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


# ── 3. configure(base_url=...) 무시 ────────────────────────


def test_sync_configure_base_url_is_ignored(monkeypatch):
    monkeypatch.delenv("WINUS_HANDLER_URL", raising=False)
    import winus_handler
    importlib.reload(winus_handler)

    winus_handler.configure(base_url="http://malicious-host:9999", timeout=10.0)
    client = winus_handler._get_client()
    assert str(client._http.base_url) == LOCKED_URL
    # timeout은 적용되어야 함
    assert client._http.timeout.connect == 10.0


def test_async_configure_base_url_is_ignored(monkeypatch):
    monkeypatch.delenv("WINUS_HANDLER_URL", raising=False)
    import winus_handler.aio as aio
    importlib.reload(aio)

    aio.configure(base_url="http://malicious-host:9999", timeout=12.0)
    client = aio._get_client()
    assert str(client._http.base_url) == LOCKED_URL
    assert client._http.timeout.connect == 12.0


# ── 4. _resolve_base_url 직접 호출도 고정 ──────────────────


def test_sync_resolve_base_url_is_constant(monkeypatch):
    monkeypatch.setenv("WINUS_HANDLER_URL", "http://something-else:1")
    import winus_handler
    importlib.reload(winus_handler)
    assert winus_handler._resolve_base_url() == LOCKED_URL


def test_async_resolve_base_url_is_constant(monkeypatch):
    monkeypatch.setenv("WINUS_HANDLER_URL", "http://something-else:1")
    import winus_handler.aio as aio
    importlib.reload(aio)
    assert aio._resolve_base_url() == LOCKED_URL
