"""AsyncSession 멀티턴 테스트 -- 이중 히스토리 패턴."""

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import llm_manager._internal.handler_client.aio


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test"),
    )


@pytest.mark.asyncio
async def test_async_session_multiturn():
    """async session -- 두 번 호출 시 curated 이력 누적."""
    responses = [
        _mock_response(
            200,
            {
                "text": "비동기 첫 번째",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "as1",
            },
        ),
        _mock_response(
            200,
            {
                "text": "비동기 두 번째",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "as2",
            },
        ),
    ]
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(side_effect=responses)
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session(system="한국어")
        r1 = await s.call("안녕")
        assert r1.text == "비동기 첫 번째"
        r2 = await s.call("이어서")
        assert r2.text == "비동기 두 번째"
    second_body = instance.request.call_args_list[1].kwargs["json"]
    assert len(second_body["messages"]) == 3


@pytest.mark.asyncio
async def test_async_session_invalid_excluded():
    """빈 응답 -> curated에서 제외, comprehensive에는 포함."""
    responses = [
        _mock_response(
            200,
            {
                "text": "",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "as-invalid",
            },
        ),
        _mock_response(
            200,
            {
                "text": "정상 응답",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "as-valid",
            },
        ),
    ]
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(side_effect=responses)
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        await s.call("첫 번째")
        await s.call("두 번째")
    second_body = instance.request.call_args_list[1].kwargs["json"]
    assert len(second_body["messages"]) == 1
    assert len(s.get_history()) == 2
    assert len(s.get_full_history()) == 4


@pytest.mark.asyncio
async def test_async_session_safety_finish_reason_excluded():
    """finish_reason=SAFETY -> curated에서 제외."""
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(
            return_value=_mock_response(
                200,
                {
                    "text": "부분 응답",
                    "model": "gemini-3-flash-preview",
                    "finish_reason": "SAFETY",
                    "request_id": "as-safety",
                },
            )
        )
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        await s.call("test")
    assert len(s.get_history()) == 0
    assert len(s.get_full_history()) == 2


@pytest.mark.asyncio
async def test_async_session_clear():
    """session.clear() -- 양쪽 이력 모두 초기화."""
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(
            return_value=_mock_response(
                200,
                {
                    "text": "응답",
                    "model": "gemini-3-flash-preview",
                    "finish_reason": "STOP",
                    "request_id": "as-clear",
                },
            )
        )
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        await s.call("안녕")
        assert len(s.get_history()) == 2
        s.clear()
        assert s.get_history() == []
        assert s.get_full_history() == []


# =============================================================================
# AsyncSession.stream — done event 의 assistant_message (ts) 보존
# =============================================================================


class _AsyncMockStreamResponse:
    """AsyncClient.stream() 의 async context manager + aiter_lines 흉내."""

    status_code = 200

    def __init__(self, sse_lines: list[str]) -> None:
        self._lines = sse_lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aiter_bytes(self):
        if False:
            yield b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _make_async_stream_mock(sse_lines: list[str]) -> MagicMock:
    """instance.stream(...) 호출 시 _AsyncMockStreamResponse 반환하는 MagicMock."""
    m = MagicMock()
    m.return_value = _AsyncMockStreamResponse(sse_lines)
    return m


@pytest.mark.asyncio
async def test_async_session_stream_records_assistant_message_with_ts():
    """AsyncSession.stream() 후 _curated_history 의 model_msg 가 ts 포함된 parts list 로 echo."""
    done = {
        "event": "done",
        "request_id": "r-1",
        "assistant_message": {
            "role": "model",
            "content": [
                {
                    "type": "function_call",
                    "function_call": {"name": "ping", "args": {}, "id": "fc_p", "thought_signature": "TS_P"},
                }
            ],
        },
    }
    sse_lines = [
        'data: {"function_calls": [{"name": "ping", "args": {}, "id": "fc_p", "thought_signature": "TS_P"}]}',
        "",
        f"data: {_json.dumps(done, ensure_ascii=False)}",
        "",
    ]
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream = _make_async_stream_mock(sse_lines)
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        async for _ in s.stream("ping"):
            pass

        history = s.get_history()

    assert len(history) == 2
    model_msg = history[1]
    assert isinstance(model_msg["content"], list)
    assert model_msg["content"][0]["function_call"]["thought_signature"] == "TS_P"


@pytest.mark.asyncio
async def test_async_session_stream_backwards_compat_no_assistant_message_in_done():
    """기존 handler (done event 에 assistant_message 없음) → text fallback."""
    done = {"event": "done", "request_id": "r-old"}
    sse_lines = [
        'data: {"text": "안녕"}',
        "",
        'data: {"finish_reason": "STOP"}',
        "",
        f"data: {_json.dumps(done)}",
        "",
    ]
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream = _make_async_stream_mock(sse_lines)
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        async for _ in s.stream("hi"):
            pass

        history = s.get_history()

    model_msg = history[1]
    assert model_msg["content"] == "안녕"


# ── send_tool_results history rollback 회귀 ──


def _async_turn1_response_with_fc() -> httpx.Response:
    return _mock_response(
        200,
        {
            "model": "gemini-3-flash-preview",
            "finish_reason": "STOP",
            "request_id": "s1",
            "function_calls": [{"name": "get_stock_price", "args": {"ticker": "005930"}, "id": "fc_abc"}],
            "assistant_message": {
                "role": "model",
                "content": [
                    {
                        "type": "function_call",
                        "function_call": {
                            "name": "get_stock_price",
                            "args": {"ticker": "005930"},
                            "id": "fc_abc",
                        },
                    }
                ],
            },
        },
    )


def _async_tools_def() -> list[dict]:
    return [
        {
            "name": "get_stock_price",
            "description": "주가 조회",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_async_session_send_tool_results_rollback_on_handler_error():
    """async client.call 실패 시 tool_msg history rollback (sync 와 동일 invariant)."""
    from llm_manager._internal.handler_client.exceptions import HandlerError

    tools = _async_tools_def()
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request = AsyncMock(
            side_effect=[
                _async_turn1_response_with_fc(),
                _mock_response(502, {"error": "Gemini 빈 응답", "error_type": "provider"}),
            ]
        )
        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        await s.call("주가 알려줘", tools=tools)

        history_before = s.get_history()
        full_before = s.get_full_history()

        with pytest.raises(HandlerError) as exc_info:
            await s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})], tools=tools)
        assert exc_info.value.status_code == 502

        assert s.get_history() == history_before, "curated_history rollback 실패 — orphan tool_msg"
        assert s.get_full_history() == full_before, "comprehensive_history rollback 실패"


@pytest.mark.asyncio
async def test_async_session_send_tool_results_rollback_on_cancellation():
    """asyncio.CancelledError 시에도 rollback — BaseException 으로 잡지 않으면 누락됨."""
    import asyncio

    tools = _async_tools_def()
    with patch("llm_manager._internal.handler_client._client.httpx.AsyncClient") as mock_client_cls:
        instance = mock_client_cls.return_value

        async def _raise_cancel(*args, **kwargs):
            raise asyncio.CancelledError()

        instance.request = AsyncMock(
            side_effect=[_async_turn1_response_with_fc(), MagicMock(side_effect=_raise_cancel)]
        )
        # 위는 first call 만 정상 응답; 두 번째 request 에서 CancelledError 발생하도록 별도 mock
        instance.request.side_effect = [_async_turn1_response_with_fc(), asyncio.CancelledError()]

        llm_manager._internal.handler_client.aio.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.aio.session()
        await s.call("주가 알려줘", tools=tools)

        history_before = s.get_history()
        with pytest.raises(asyncio.CancelledError):
            await s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})], tools=tools)
        assert s.get_history() == history_before
