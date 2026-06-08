"""SDK 동기 인터페이스 테스트."""

from enum import Enum
from unittest.mock import patch

import httpx
import pytest
from pydantic import BaseModel as PydanticBaseModel

import llm_manager._internal.handler_client
from llm_manager._internal.handler_client.exceptions import HandlerConnectionError, HandlerError


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> httpx.Response:
    """httpx.Response 모킹."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test"),
    )
    return resp


def test_call_success():
    """sync call → LLMResponse."""
    mock_data = {
        "text": "응답",
        "model": "gemini-2.5-flash-preview-05-20",
        "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
        "finish_reason": "STOP",
        "request_id": "abc",
    }

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)

        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.call("test", model="gemini-2.5-flash-preview-05-20")

    assert result.text == "응답"
    assert result.usage is not None
    assert result.usage.total_tokens == 15


def test_call_handler_error():
    """Handler 에러 응답 → HandlerError."""
    error_data = {"error": "rate limit exceeded", "request_id": "xyz"}

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(429, error_data)

        llm_manager._internal.handler_client.configure(base_url="http://test:8100")

        with pytest.raises(HandlerError) as exc_info:
            llm_manager._internal.handler_client.call("test")

    assert exc_info.value.status_code == 429


def test_call_connection_error():
    """Handler 연결 실패 → HandlerConnectionError."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = httpx.ConnectError("Connection refused")

        llm_manager._internal.handler_client.configure(base_url="http://test:8100")

        with pytest.raises(HandlerConnectionError):
            llm_manager._internal.handler_client.call("test")


def test_configure_changes_client():
    """configure() 호출 시 클라이언트 재생성."""
    llm_manager._internal.handler_client.configure(base_url="http://custom:9999", timeout=30.0)
    client = llm_manager._internal.handler_client._get_client()
    assert client._http.base_url == httpx.URL("http://custom:9999")


# ── 통합 call(input=...) 테스트 ──


def test_call_unified_simple():
    """통합 call(input=...) — 단순 텍스트."""
    mock_data = {
        "text": "통합 응답",
        "model": "gemini-3-flash-preview",
        "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
        "finish_reason": "STOP",
        "request_id": "unified-1",
    }
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.call("안녕하세요")
    assert result.text == "통합 응답"
    call_args = instance.request.call_args
    body = call_args.kwargs["json"]
    assert body["messages"] == [{"role": "user", "content": "안녕하세요"}]


def test_call_unified_with_system():
    """통합 call — system 지시사항."""
    mock_data = {"text": "시스템 응답", "model": "gemini-3-flash-preview", "request_id": "unified-2"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call("test", system="한국어로 답변")
    body = instance.request.call_args.kwargs["json"]
    assert body["system_instruction"] == "한국어로 답변"


def test_call_unified_with_output_json():
    """통합 call — output='json' + to_dict()."""
    mock_data = {"text": '{"result": "ok"}', "model": "gemini-3-flash-preview", "request_id": "unified-3"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.call("JSON 결과", output="json")
    body = instance.request.call_args.kwargs["json"]
    assert body["output"] == "json"
    assert result.to_dict() == {"result": "ok"}


def test_call_unified_with_schema():
    """통합 call — Pydantic schema → output='json' + response_schema 자동 추론."""

    class Grade(PydanticBaseModel):
        grade: str
        score: float

    mock_data = {"text": '{"grade": "A", "score": 0.95}', "model": "gemini-3-flash-preview", "request_id": "unified-4"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.call("등급 평가", schema=Grade)
    body = instance.request.call_args.kwargs["json"]
    assert body["output"] == "json"
    assert "response_schema" in body
    assert body["response_schema"]["properties"]["grade"]["type"] == "string"
    assert result.to_dict() == {"grade": "A", "score": 0.95}


def test_call_unified_with_enum_schema():
    """통합 call — Enum schema → output='enum' + response_schema 자동 추론."""

    class Sentiment(Enum):
        POSITIVE = "Positive"
        NEGATIVE = "Negative"
        NEUTRAL = "Neutral"

    mock_data = {"text": "Positive", "model": "gemini-3-flash-preview", "request_id": "unified-5"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call("감성 분류", schema=Sentiment)
    body = instance.request.call_args.kwargs["json"]
    assert body["output"] == "enum"
    assert body["response_schema"] == {"type": "STRING", "enum": ["Positive", "Negative", "Neutral"]}


def test_call_unified_with_thinking():
    """통합 call — thinking 모드."""
    mock_data = {"text": "깊은 사고 결과", "model": "gemini-3-flash-preview", "request_id": "unified-6"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call("분석", thinking="high")
    body = instance.request.call_args.kwargs["json"]
    assert body["thinking"] == "high"


def test_call_unified_with_part_from_uri():
    """통합 call — Part.from_uri()로 파일 참조."""
    from llm_manager._internal.handler_client.models import Part

    mock_data = {"text": "파일 분석 결과", "model": "gemini-3-flash-preview", "request_id": "unified-7"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call(["분석해줘", Part.from_uri("files/xxx-yyy", "application/pdf")])
    body = instance.request.call_args.kwargs["json"]
    msg = body["messages"][0]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert any(p["type"] == "file" for p in msg["content"])
    assert any(p["type"] == "text" for p in msg["content"])


def test_call_unified_with_history():
    """통합 call — history 대화 이력."""
    mock_data = {"text": "이력 기반 응답", "model": "gemini-3-flash-preview", "request_id": "unified-8"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call(
            "결론이 뭐야?",
            history=[
                {"role": "user", "content": "요약해줘"},
                {"role": "model", "content": "요약하면..."},
            ],
        )
    body = instance.request.call_args.kwargs["json"]
    assert len(body["messages"]) == 3
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][2]["content"] == "결론이 뭐야?"


def test_call_unified_with_tools():
    """통합 call — tools."""
    mock_data = {"text": "tool 결과", "model": "gemini-3-flash-preview", "request_id": "unified-9"}
    tools = [{"function_declarations": [{"name": "fn", "description": "d"}]}]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call("날씨", tools=tools)
    body = instance.request.call_args.kwargs["json"]
    assert body["tools"] == tools


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


def test_delete_file():
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, {"status": "deleted"})
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.delete_file("files/abc123")
    instance.request.assert_called_once()


def test_delete_file_404_idempotent():
    """404 → 예외 없이 정상 반환."""
    error_data = {"error": "not found", "error_type": "provider"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(404, error_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.delete_file("files/gone")  # 예외 발생 안 함


def test_get_file():
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, _file_json())
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.get_file("files/abc123")
    assert result is not None
    assert result.name == "files/abc123"
    assert result.display_name == "test.pdf"
    assert result.size_bytes == 1024


def test_get_file_not_found_returns_none():
    """404 → None 반환."""
    error_data = {"error": "not found", "error_type": "provider"}
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(404, error_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.get_file("files/nonexistent")
    assert result is None


def test_list_files():
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, [_file_json(name="files/a"), _file_json(name="files/b")])
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.list_files()
    assert len(result) == 2
    assert result[0].name == "files/a"
    assert result[1].name == "files/b"


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


def test_get_cache():
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, _cache_json())
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.get_cache("cachedContents/abc123")
    assert result.name == "cachedContents/abc123"
    assert result.total_token_count == 5000
    assert result.update_time == "2026-01-01T12:00:00Z"


def test_update_cache():
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, _cache_json())
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_manager._internal.handler_client.update_cache("cachedContents/abc123", ttl=7200)
    assert result.name == "cachedContents/abc123"
    call_args = instance.request.call_args
    assert call_args.kwargs["json"] == {"ttl": 7200}


def test_create_cache_explicit_params():
    """create_cache — 명시적 파라미터가 올바른 body로 변환."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, _cache_json())
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.create_cache(
            "대량 텍스트",
            model="gemini-2.5-flash",
            system="분석가",
            display_name="test-cache",
            ttl=300,
        )
    body = instance.request.call_args.kwargs["json"]
    assert body["model"] == "gemini-2.5-flash"
    assert body["system_instruction"] == "분석가"
    assert body["display_name"] == "test-cache"
    assert body["ttl"] == 300
    assert body["contents"] == [{"role": "user", "content": "대량 텍스트"}]


def test_create_cache_with_fileref():
    """create_cache — FileRef + 텍스트 → 정규화 + role 래핑."""
    from llm_manager._internal.handler_client.models import FileRef

    ref = FileRef(name="files/abc", uri="https://example.com/abc", mime_type="application/pdf", state="ACTIVE")
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, _cache_json())
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.create_cache([ref, "분석해줘"], model="gemini-2.5-flash")
    body = instance.request.call_args.kwargs["json"]
    contents = body["contents"]
    assert len(contents) == 1
    assert contents[0]["role"] == "user"
    assert isinstance(contents[0]["content"], list)
    assert any(p["type"] == "file" for p in contents[0]["content"])


def test_create_cache_with_tools():
    """create_cache — tools/tool_config 패스스루."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, _cache_json())
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.create_cache(
            "텍스트",
            model="gemini-2.5-flash",
            tools=[{"name": "fn", "description": "d", "parameters": {}}],
            tool_config="any",
        )
    body = instance.request.call_args.kwargs["json"]
    assert body["tools"] == [{"name": "fn", "description": "d", "parameters": {}}]
    assert body["tool_config"] == "any"


# =============================================================================
# call(messages=...) / stream(messages=...) — B 패턴 (명시 messages)
# =============================================================================


def test_call_messages_path():
    """call(messages=[Content, ...]) → body['messages'] 가 그대로 직렬화됨."""
    from llm_manager._internal.handler_client.models import Content, ContentPart, FunctionResponse

    mock_data = {
        "text": "최종 답변",
        "model": "gemini-3-flash-preview",
        "finish_reason": "STOP",
        "request_id": "msg1",
    }
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(200, mock_data)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        llm_manager._internal.handler_client.call(
            messages=[
                Content(role="user", content="주가 알려줘"),
                Content(
                    role="model",
                    content=[
                        ContentPart(type="text", text="조회중..."),
                    ],
                ),
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
            tool_config="auto",
        )
    body = instance.request.call_args.kwargs["json"]
    assert len(body["messages"]) == 3
    assert body["messages"][0] == {"role": "user", "content": "주가 알려줘"}
    assert body["messages"][1]["role"] == "model"
    assert body["messages"][2]["role"] == "tool"
    assert body["messages"][2]["content"][0]["function_response"]["id"] == "fc_1"
    assert body["tool_config"] == "auto"


def test_call_mutually_exclusive_both():
    """call(contents='x', messages=[...]) → ValueError (둘 다 지정)."""
    from llm_manager._internal.handler_client.models import Content

    llm_manager._internal.handler_client.configure(base_url="http://test:8100")
    with pytest.raises(ValueError, match="exactly one of: contents, messages"):
        llm_manager._internal.handler_client.call(
            contents="hi",
            messages=[Content(role="user", content="hi")],
        )


def test_call_mutually_exclusive_neither():
    """call() (둘 다 미지정) → ValueError."""
    llm_manager._internal.handler_client.configure(base_url="http://test:8100")
    with pytest.raises(ValueError, match="exactly one of: contents, messages"):
        llm_manager._internal.handler_client.call()


def test_call_messages_with_history_raises():
    """call(messages=[...], history=[...]) → ValueError (의미 중복)."""
    from llm_manager._internal.handler_client.models import Content

    llm_manager._internal.handler_client.configure(base_url="http://test:8100")
    with pytest.raises(ValueError, match="messages and history are mutually exclusive"):
        llm_manager._internal.handler_client.call(
            messages=[Content(role="user", content="hi")],
            history=[{"role": "user", "content": "prev"}],
        )


def test_parse_sse_line_done_event_carries_payload():
    """parse_sse_line 의 done event 가 StopIteration.value 에 full payload 동봉."""
    import pytest

    from llm_manager._internal.handler_client._common import parse_sse_line

    line = 'data: {"event": "done", "request_id": "abc", "assistant_message": {"role": "model", "content": []}}'
    with pytest.raises(StopIteration) as exc:
        parse_sse_line(line)
    assert exc.value.value["request_id"] == "abc"
    assert exc.value.value["assistant_message"]["role"] == "model"


def test_parse_sse_line_done_event_backwards_compat_no_payload():
    """assistant_message 없는 기존 handler 의 done event 도 정상 처리 (value=dict, .get None)."""
    import pytest

    from llm_manager._internal.handler_client._common import parse_sse_line

    line = 'data: {"event": "done", "request_id": "xyz"}'
    with pytest.raises(StopIteration) as exc:
        parse_sse_line(line)
    assert exc.value.value.get("assistant_message") is None


def test_sync_client_captures_done_payload_after_stream():
    """SyncClient.stream() 종료 후 _last_done_payload 에 done event data 저장."""
    import json as _json

    from llm_manager._internal.handler_client._sync_client import SyncClient

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

    class _MockStreamResponse:
        status_code = 200

        def iter_lines(self):
            yield from sse_lines

        def iter_bytes(self):
            return iter([])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _MockStreamResponse()
        client = SyncClient(base_url="http://test:8100")
        list(client.stream(contents="hi", model="gemini-3-flash-preview"))
    assert client._last_done_payload is not None
    am = client._last_done_payload["assistant_message"]
    assert isinstance(am, dict) and am["content"][0]["thought_signature"] == "BASE64"


def test_stream_messages_path():
    """stream(messages=[Content, ...]) → body['messages'] 정상 직렬화."""
    from llm_manager._internal.handler_client.models import Content

    sse_lines = [
        'data: {"text": "hi"}',
        "",
        'data: {"event": "done"}',
        "",
    ]

    class _MockStreamResponse:
        status_code = 200

        def iter_lines(self):
            yield from sse_lines

        def iter_bytes(self):
            return iter([])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _MockStreamResponse()
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        chunks = list(
            llm_manager._internal.handler_client.stream(
                messages=[Content(role="user", content="hi")],
                model="gemini-3-flash-preview",
            )
        )
    assert chunks  # 적어도 하나
    body = instance.stream.call_args.kwargs["json"]
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_last_assistant_message_returns_none_when_payload_missing():
    """sync `last_assistant_message()` smoke — payload 없거나 키 누락 시 None.

    Round-trip (stream → 헬퍼 read) 검증은 동일 `_done_payload_var` 메커니즘을 쓰는
    `test_async.py::test_last_assistant_message_returns_validated_content_after_stream` 에서 보장.
    """
    from llm_manager._internal.handler_client._common import _done_payload_var

    _done_payload_var.set(None)
    assert llm_manager._internal.handler_client.last_assistant_message() is None

    _done_payload_var.set({"event": "done", "request_id": "r"})  # assistant_message 키 누락
    assert llm_manager._internal.handler_client.last_assistant_message() is None

    _done_payload_var.set(None)
