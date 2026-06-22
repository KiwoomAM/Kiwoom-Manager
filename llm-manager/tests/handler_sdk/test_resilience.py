"""네트워크 복원력 테스트 — RemoteProtocolError 재시도 / wrap / keepalive.

설계: docs/24.handler-client-resilience.md
- ② keepalive_expiry=2.0 (server 5s 보다 짧게) → httpx.Client 생성자 인자 검증
- ③ 비스트림 RemoteProtocolError → wrap + 1회 재시도 (files 제외) / stream → wrap-only
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import llm_manager._internal.handler_client as sdk
import llm_manager._internal.handler_client.aio as aio_sdk
from llm_manager._internal.handler_client._client import AsyncClient
from llm_manager._internal.handler_client._common import KEEPALIVE_EXPIRY
from llm_manager._internal.handler_client._sync_client import SyncClient
from llm_manager._internal.handler_client.exceptions import HandlerConnectionError

_SYNC_HTTPX = "llm_manager._internal.handler_client._sync_client.httpx.Client"
_ASYNC_HTTPX = "llm_manager._internal.handler_client._client.httpx.AsyncClient"


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test"),
    )


_OK = {"text": "응답", "model": "gemini-3-flash-preview", "request_id": "r-1"}


# ─────────────────────────────────────────────────────────────
# ② keepalive_expiry — 생성자 인자 검증 (httpx.Client/AsyncClient 는 Mock)
# ─────────────────────────────────────────────────────────────


def test_sync_client_sets_keepalive_expiry():
    with patch(_SYNC_HTTPX) as mock_cls:
        SyncClient(base_url="http://test:8100")
    limits = mock_cls.call_args.kwargs["limits"]
    assert limits.keepalive_expiry == KEEPALIVE_EXPIRY == 2.0


def test_async_client_sets_keepalive_expiry():
    with patch(_ASYNC_HTTPX) as mock_cls:
        AsyncClient(base_url="http://test:8100")
    limits = mock_cls.call_args.kwargs["limits"]
    assert limits.keepalive_expiry == KEEPALIVE_EXPIRY == 2.0


# ─────────────────────────────────────────────────────────────
# ③ 비스트림 _request — RemoteProtocolError wrap + 1회 재시도
# ─────────────────────────────────────────────────────────────


def test_sync_request_retries_once_on_remote_protocol_error():
    """첫 시도 RemoteProtocolError → 두 번째 성공 → 정상 반환 (호출 2회)."""
    with patch(_SYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request.side_effect = [
            httpx.RemoteProtocolError("Server disconnected without sending a response"),
            _mock_response(200, _OK),
        ]
        sdk.configure(base_url="http://test:8100")
        result = sdk.call("test")
    assert result.text == "응답"
    assert instance.request.call_count == 2


def test_sync_request_wraps_after_retry_fails():
    """연속 2회 RemoteProtocolError → HandlerConnectionError (호출 2회)."""
    with patch(_SYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request.side_effect = httpx.RemoteProtocolError("boom")
        sdk.configure(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            sdk.call("test")
    assert instance.request.call_count == 2


def test_sync_request_no_retry_for_file_upload():
    """files kwarg 포함 시 RemoteProtocolError → 재시도 없이 즉시 wrap (호출 1회)."""
    with patch(_SYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request.side_effect = httpx.RemoteProtocolError("boom")
        client = SyncClient(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            client._request("POST", "/v1/files", files={"file": ("n", b"x", "text/plain")})
    assert instance.request.call_count == 1


def test_sync_request_connect_error_still_wrapped():
    """기존 ConnectError 동작 회귀 없음 → HandlerConnectionError (재시도 없음)."""
    with patch(_SYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request.side_effect = httpx.ConnectError("refused")
        sdk.configure(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            sdk.call("test")
    assert instance.request.call_count == 1


# ─────────────────────────────────────────────────────────────
# ③ 스트림 — RemoteProtocolError wrap-only (재시도 없음)
# ─────────────────────────────────────────────────────────────


def test_sync_stream_wraps_remote_protocol_error_no_retry():
    with patch(_SYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.stream.side_effect = httpx.RemoteProtocolError("boom")
        sdk.configure(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            list(sdk.stream("test"))
    assert instance.stream.call_count == 1


# ─────────────────────────────────────────────────────────────
# async 동일 검증
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_request_retries_once_on_remote_protocol_error():
    with patch(_ASYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request = AsyncMock(
            side_effect=[
                httpx.RemoteProtocolError("Server disconnected without sending a response"),
                _mock_response(200, _OK),
            ]
        )
        aio_sdk.configure(base_url="http://test:8100")
        result = await aio_sdk.call("test")
    assert result.text == "응답"
    assert instance.request.call_count == 2


@pytest.mark.asyncio
async def test_async_request_wraps_after_retry_fails():
    with patch(_ASYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request = AsyncMock(side_effect=httpx.RemoteProtocolError("boom"))
        aio_sdk.configure(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            await aio_sdk.call("test")
    assert instance.request.call_count == 2


@pytest.mark.asyncio
async def test_async_request_no_retry_for_file_upload():
    with patch(_ASYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.request = AsyncMock(side_effect=httpx.RemoteProtocolError("boom"))
        client = AsyncClient(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            await client._request("POST", "/v1/files", files={"file": ("n", b"x", "text/plain")})
    assert instance.request.call_count == 1


@pytest.mark.asyncio
async def test_async_stream_wraps_remote_protocol_error_no_retry():
    with patch(_ASYNC_HTTPX) as mock_cls:
        instance = mock_cls.return_value
        instance.stream = lambda *a, **k: (_ for _ in ()).throw(httpx.RemoteProtocolError("boom"))
        aio_sdk.configure(base_url="http://test:8100")
        with pytest.raises(HandlerConnectionError):
            async for _ in aio_sdk.stream("test"):
                pass
