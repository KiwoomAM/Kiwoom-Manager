"""SDK 비동기 인터페이스 테스트."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import llm_manager._internal.handler_client.aio
from llm_manager._internal.handler_client.exceptions import HandlerConnectionError, HandlerError


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> httpx.Response:
    """httpx.Response 모킹."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test"),
    )


@pytest.mark.asyncio
async def test_async_call_success():
    """async call → LLMResponse."""
    mock_data = {
        "text": "비동기 응답",
        "model": "gemini-2.5-flash-preview-05-20",
        "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
        "finish_reason": "STOP",
        "request_id": "abc",
    }

    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, mock_data))

        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.call("test")

    assert result.text == "비동기 응답"
    assert result.usage is not None
    assert result.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_async_call_handler_error():
    """Handler 에러 → HandlerError."""
    error_data = {"error": "server error", "request_id": "xyz"}

    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(500, error_data))

        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")

        with pytest.raises(HandlerError) as exc_info:
            await llm_manager._internal.handler_client.aio.call("test")

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_async_call_captures_wire_detail():
    """500 응답의 wire detail slug 가 SDK 예외 detail 속성으로 보존."""
    error_data = {
        "error": "내부 서버 오류",
        "detail": "pool_timeout",
        "error_type": "handler_internal",
        "request_id": "xyz",
    }

    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(500, error_data))

        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")

        with pytest.raises(HandlerError) as exc_info:
            await llm_manager._internal.handler_client.aio.call("test")

    assert exc_info.value.error_type == "handler_internal"
    assert exc_info.value.detail == "pool_timeout"


@pytest.mark.asyncio
async def test_async_call_connection_error():
    """Handler 연결 실패 → HandlerConnectionError."""
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")

        with pytest.raises(HandlerConnectionError):
            await llm_manager._internal.handler_client.aio.call("test")


# ── 통합 call(input=...) 테스트 ──


@pytest.mark.asyncio
async def test_async_call_unified_simple():
    """async 통합 call(input=...) — 단순 텍스트."""
    mock_data = {
        "text": "비동기 통합 응답",
        "model": "gemini-3-flash-preview",
        "request_id": "async-unified-1",
    }
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, mock_data))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.call("안녕하세요")
    assert result.text == "비동기 통합 응답"
    body = instance.request.call_args.kwargs["json"]
    assert body["messages"] == [{"role": "user", "content": "안녕하세요"}]


@pytest.mark.asyncio
async def test_async_call_unified_with_output_and_history():
    """async 통합 call() — output + history 조합."""
    mock_data = {
        "text": '{"result": true}',
        "model": "gemini-3-flash-preview",
        "request_id": "async-unified-2",
    }
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, mock_data))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.call(
            "결론은?",
            output="json",
            history=[{"role": "user", "content": "이전 질문"}],
        )
    body = instance.request.call_args.kwargs["json"]
    assert body["output"] == "json"
    assert len(body["messages"]) == 2
    assert result.to_dict() == {"result": True}


# ── Files API ──


def _file_json(**overrides: object) -> dict:
    defaults: dict = {
        "name": "files/abc123",
        "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc123",
        "mime_type": "application/pdf",
        "state": "ACTIVE",
        "display_name": "test.pdf",
        "size_bytes": 1024,
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_async_delete_file():
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, {"status": "deleted"}))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        await llm_manager._internal.handler_client.aio.delete_file("files/abc123")
    instance.request.assert_called_once()


@pytest.mark.asyncio
async def test_async_delete_file_404_idempotent():
    """404 → 예외 없이 정상 반환."""
    error_data = {"error": "not found", "error_type": "provider"}
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(404, error_data))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        await llm_manager._internal.handler_client.aio.delete_file("files/gone")  # 예외 발생 안 함


@pytest.mark.asyncio
async def test_async_get_file():
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _file_json()))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.get_file("files/abc123")
    assert result is not None
    assert result.name == "files/abc123"
    assert result.display_name == "test.pdf"


@pytest.mark.asyncio
async def test_async_get_file_not_found_returns_none():
    """404 → None 반환."""
    error_data = {"error": "not found", "error_type": "provider"}
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(404, error_data))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.get_file("files/nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_async_list_files():
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(
            return_value=_mock_response(200, [_file_json(name="files/a"), _file_json(name="files/b")])
        )
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.list_files()
    assert len(result) == 2
    assert result[0].name == "files/a"


# ── Cache API ──


def _cache_json(**overrides: object) -> dict:
    defaults: dict = {
        "name": "cachedContents/abc123",
        "model": "models/gemini-2.5-flash",
        "display_name": "test cache",
        "create_time": "2026-01-01T00:00:00Z",
        "expire_time": "2026-01-02T00:00:00Z",
        "update_time": "2026-01-01T12:00:00Z",
        "total_token_count": 5000,
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_async_get_cache():
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _cache_json()))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.get_cache("cachedContents/abc123")
    assert result.name == "cachedContents/abc123"
    assert result.total_token_count == 5000


@pytest.mark.asyncio
async def test_async_update_cache():
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _cache_json()))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_manager._internal.handler_client.aio.update_cache("cachedContents/abc123", ttl=7200)
    assert result.name == "cachedContents/abc123"
    call_args = instance.request.call_args
    assert call_args.kwargs["json"] == {"ttl": 7200}


@pytest.mark.asyncio
async def test_async_create_cache_explicit_params():
    """async create_cache — 명시적 파라미터."""
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _cache_json()))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        await llm_manager._internal.handler_client.aio.create_cache(
            "대량 텍스트",
            model="gemini-2.5-flash",
            system="분석가",
            ttl=300,
        )
    body = instance.request.call_args.kwargs["json"]
    assert body["model"] == "gemini-2.5-flash"
    assert body["system_instruction"] == "분석가"
    assert body["contents"] == [{"role": "user", "content": "대량 텍스트"}]


@pytest.mark.asyncio
async def test_async_create_cache_with_tools():
    """async create_cache — tools 패스스루."""
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _cache_json()))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        await llm_manager._internal.handler_client.aio.create_cache(
            "텍스트",
            model="gemini-2.5-flash",
            tools=[{"google_search": {}}],
        )
    body = instance.request.call_args.kwargs["json"]
    assert body["tools"] == [{"google_search": {}}]


# =============================================================================
# aio.call(messages=...) — B 패턴 (명시 messages)
# =============================================================================


async def test_aio_call_messages_path():
    """aio.call(messages=[Content, ...]) → body['messages'] 그대로 직렬화."""
    from llm_manager._internal.handler_client.models import Content, ContentPart, FunctionResponse

    mock_data = {
        "text": "최종 답변",
        "model": "gemini-3-flash-preview",
        "finish_reason": "STOP",
        "request_id": "msg1",
    }
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, mock_data))
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        await llm_manager._internal.handler_client.aio.call(
            messages=[
                Content(role="user", content="주가 알려줘"),
                Content(
                    role="tool",
                    content=[
                        ContentPart(
                            type="function_response",
                            function_response=FunctionResponse(name="get_price", response={"price": 87500}, id="fc_1"),
                        )
                    ],
                ),
            ],
        )
    body = instance.request.call_args.kwargs["json"]
    assert len(body["messages"]) == 2
    assert body["messages"][0] == {"role": "user", "content": "주가 알려줘"}
    assert body["messages"][1]["role"] == "tool"
    assert body["messages"][1]["content"][0]["function_response"]["id"] == "fc_1"


async def test_aio_call_mutually_exclusive_both():
    """aio.call(contents='x', messages=[...]) → ValueError."""
    from llm_manager._internal.handler_client.models import Content

    llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
    with pytest.raises(ValueError, match="exactly one of: contents, messages"):
        await llm_manager._internal.handler_client.aio.call(
            contents="hi",
            messages=[Content(role="user", content="hi")],
        )


async def test_aio_call_mutually_exclusive_neither():
    """aio.call() (둘 다 미지정) → ValueError."""
    llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
    with pytest.raises(ValueError, match="exactly one of: contents, messages"):
        await llm_manager._internal.handler_client.aio.call()


async def test_aio_call_messages_with_history_raises():
    """aio.call(messages=[...], history=[...]) → ValueError."""
    from llm_manager._internal.handler_client.models import Content

    llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
    with pytest.raises(ValueError, match="messages and history are mutually exclusive"):
        await llm_manager._internal.handler_client.aio.call(
            messages=[Content(role="user", content="hi")],
            history=[{"role": "user", "content": "prev"}],
        )


# ── stream done payload 격리 (race 회귀 방지) ──


def _make_mock_async_stream_cm(sse_lines: list[str]) -> object:
    """httpx.AsyncClient.stream(...) 반환값을 흉내내는 async context manager."""

    class _Resp:
        status_code = 200

        async def aiter_bytes(self):
            return
            yield  # pragma: no cover — async generator 시그니처 충족용

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class _CM:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *args):
            return None

    return _CM()


@pytest.mark.asyncio
async def test_async_stream_done_payload_isolated_across_concurrent_calls():
    """동일 AsyncClient 위 동시 stream() 호출의 done payload 가 task 간 격리되어야 한다.

    Race 시나리오 (수정 전 instance-attribute 구현에서 깨짐):
    - Task A 가 stream("A") 의 done event 를 받아 _last_done_payload = X_A 로 set
    - Task B 가 동시에 stream("B") 의 done event 를 받아 _last_done_payload = X_B 로 set (X_A 덮어씀)
    - Task A 가 종료 후 self._client._last_done_payload 를 읽으면 X_B 가 보임 → 다른 turn 의
      assistant_message 가 자기 history 로 누적되는 silent corruption.

    Race window 결정론적 재현: 두 task 가 각자 stream 종료 후 Event 로 서로의 완료를 기다린 뒤에
    read → 옛 코드라면 양쪽 다 마지막 set 값을 보고, 새 코드 (ContextVar) 는 각자 자기 값을 봄.
    """
    import asyncio
    import json as _json

    from llm_manager._internal.handler_client._client import AsyncClient

    done_payloads = [
        {
            "event": "done",
            "request_id": "r-A",
            "assistant_message": {
                "role": "model",
                "content": [{"type": "text", "text": "from-A", "thought_signature": "TS_A"}],
            },
        },
        {
            "event": "done",
            "request_id": "r-B",
            "assistant_message": {
                "role": "model",
                "content": [{"type": "text", "text": "from-B", "thought_signature": "TS_B"}],
            },
        },
    ]

    def _sse(done_data: dict) -> list[str]:
        return [
            'data: {"text": "x"}',
            "",
            f"data: {_json.dumps(done_data, ensure_ascii=False)}",
            "",
        ]

    call_count = {"n": 0}

    def _stream_side_effect(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return _make_mock_async_stream_cm(_sse(done_payloads[idx]))

    done_events = [asyncio.Event(), asyncio.Event()]

    async def _stream_then_sync_then_read(client: AsyncClient, idx: int) -> dict | None:
        async for _ in client.stream(contents="x", model="gemini-3-flash-preview"):
            await asyncio.sleep(0)
        # 자기 set 완료 신호 + 상대편 set 완료 대기 → 양쪽 set 이 모두 끝난 뒤 read 하므로,
        # 옛 코드라면 양쪽 다 마지막 writer 값을 봄 (race), 새 코드는 각자 자기 context 값을 봄.
        done_events[idx].set()
        await done_events[1 - idx].wait()
        return client._last_done_payload  # property 경유 — ContextVar 의 자기 task 값

    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.side_effect = _stream_side_effect

        client = AsyncClient(base_url="http://test:8100")
        result_a, result_b = await asyncio.gather(
            _stream_then_sync_then_read(client, 0),
            _stream_then_sync_then_read(client, 1),
        )

    ts_a = result_a["assistant_message"]["content"][0]["thought_signature"]
    ts_b = result_b["assistant_message"]["content"][0]["thought_signature"]
    assert ts_a == "TS_A", f"Task A 가 자기 done payload 대신 다른 task 값을 봄: got {ts_a}"
    assert ts_b == "TS_B", f"Task B 가 자기 done payload 대신 다른 task 값을 봄: got {ts_b}"


@pytest.mark.asyncio
async def test_async_client_last_done_payload_property_compat():
    """`client._last_done_payload` 직접 attribute 접근이 ContextVar 마이그레이션 후에도 동작 (호환 layer)."""
    import json as _json

    from llm_manager._internal.handler_client._client import AsyncClient

    done_data = {
        "event": "done",
        "request_id": "r-1",
        "assistant_message": {
            "role": "model",
            "content": [{"type": "text", "text": "hi", "thought_signature": "BASE64"}],
        },
    }
    sse_lines = [
        'data: {"text": "hi"}',
        "",
        f"data: {_json.dumps(done_data, ensure_ascii=False)}",
        "",
    ]

    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _make_mock_async_stream_cm(sse_lines)

        client = AsyncClient(base_url="http://test:8100")
        async for _ in client.stream(contents="hi", model="gemini-3-flash-preview"):
            pass
        # 기존 테스트 / 외부 코드가 사용하던 attribute 접근 패턴 — property 로 그대로 동작
        assert client._last_done_payload is not None
        am = client._last_done_payload["assistant_message"]
        assert isinstance(am, dict)
        assert am["content"][0]["thought_signature"] == "BASE64"


# ── aio.last_assistant_message() public helper ──


@pytest.mark.asyncio
async def test_last_assistant_message_returns_validated_content_after_stream():
    """raw `aio.stream()` 종료 후 `last_assistant_message()` 가 Content 로 복원되어야 한다."""
    import json as _json

    from llm_manager._internal.handler_client import aio

    done_data = {
        "event": "done",
        "request_id": "r-1",
        "assistant_message": {
            "role": "model",
            "content": [{"type": "text", "text": "hello", "thought_signature": "TS"}],
        },
    }
    sse_lines = [
        'data: {"text": "hello"}',
        "",
        f"data: {_json.dumps(done_data, ensure_ascii=False)}",
        "",
    ]

    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _make_mock_async_stream_cm(sse_lines)

        aio.configure(base_url="http://test:8100")
        async for _ in aio.stream(contents="hi", model="gemini-3-flash-preview"):
            pass

        am = aio.last_assistant_message()

    assert am is not None, "stream 종료 직후 헬퍼가 None 반환 — done event 보존 실패"
    assert am.role == "model"
    assert len(am.content) == 1
    part = am.content[0]
    assert part.type == "text"
    assert part.text == "hello"
    assert part.thought_signature == "TS"


@pytest.mark.asyncio
async def test_last_assistant_message_resets_between_streams():
    """새 `stream()` 진입 시 이전 turn 의 assistant_message 가 reset 되어야 (cross-turn leak 방지)."""

    from llm_manager._internal.handler_client import aio
    from llm_manager._internal.handler_client._common import _done_payload_var

    done_data = {
        "event": "done",
        "request_id": "r-prev",
        "assistant_message": {"role": "model", "content": [{"type": "text", "text": "prev"}]},
    }
    # 이전 stream 결과가 ContextVar 에 남아있다고 가정
    _done_payload_var.set(done_data)
    assert aio.last_assistant_message() is not None

    # 새 stream 진입 시 _client.stream() 내부에서 _done_payload_var.set(None) 호출됨
    # done event 가 없는 SSE 로 진입 → reset 후 새 set 없음 → None 이어야 함
    sse_lines = ['data: {"text": "x"}', ""]  # done event 없음
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _make_mock_async_stream_cm(sse_lines)

        aio.configure(base_url="http://test:8100")
        async for _ in aio.stream(contents="x", model="gemini-3-flash-preview"):
            pass

        assert aio.last_assistant_message() is None, (
            "이전 turn 의 assistant_message 가 새 stream 시 reset 되지 않음 — cross-turn leak"
        )


def test_last_assistant_message_returns_none_when_payload_missing():
    """ContextVar 에 payload 가 없거나 assistant_message 키가 없으면 None."""
    from llm_manager._internal.handler_client import aio
    from llm_manager._internal.handler_client._common import _done_payload_var

    _done_payload_var.set(None)
    assert aio.last_assistant_message() is None

    # payload 는 있지만 assistant_message 키 누락 (예: handler 가 빈응답 등으로 미발송)
    _done_payload_var.set({"event": "done", "request_id": "r"})
    assert aio.last_assistant_message() is None

    _done_payload_var.set(None)
