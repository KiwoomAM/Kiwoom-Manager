"""SDK 모델 직렬화/역직렬화 테스트."""

import pytest

from llm_manager._internal.handler_client.models import (
    CacheRef,
    FileRef,
    LLMResponse,
    Part,
    StreamChunk,
    TokenUsage,
)


def test_llm_response_from_json():
    """JSON → LLMResponse 파싱."""
    data = {
        "text": "안녕하세요",
        "model": "gemini-2.5-flash-preview-05-20",
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        "finish_reason": "STOP",
        "request_id": "test-123",
    }
    resp = LLMResponse.model_validate(data)
    assert resp.text == "안녕하세요"
    assert resp.usage is not None
    assert resp.usage.total_tokens == 30


def test_stream_chunk_minimal():
    """최소 StreamChunk 파싱."""
    data = {"text": "청크"}
    chunk = StreamChunk.model_validate(data)
    assert chunk.text == "청크"
    assert chunk.usage is None


def test_file_ref_from_json():
    """JSON → FileRef 파싱."""
    data = {
        "name": "files/abc",
        "uri": "gs://bucket/abc",
        "mime_type": "application/pdf",
        "state": "ACTIVE",
    }
    ref = FileRef.model_validate(data)
    assert ref.name == "files/abc"


def test_cache_ref_from_json():
    """JSON → CacheRef 파싱."""
    data = {
        "name": "projects/123/cachedContents/abc",
        "model": "gemini-2.5-flash-preview-05-20",
    }
    ref = CacheRef.model_validate(data)
    assert ref.name == "projects/123/cachedContents/abc"


def test_token_usage_serialization():
    """TokenUsage → dict 직렬화."""
    usage = TokenUsage(input_tokens=100, output_tokens=200, total_tokens=300)
    d = usage.model_dump()
    assert d == {
        "input_tokens": 100,
        "output_tokens": 200,
        "total_tokens": 300,
        "thinking_tokens": None,
        "cached_tokens": None,
        "tool_use_tokens": None,
    }


def test_token_usage_with_cached_and_tool_use_tokens():
    """TokenUsage 확장 필드: cached_tokens / tool_use_tokens 직렬화."""
    usage = TokenUsage(
        input_tokens=6190,
        output_tokens=76,
        total_tokens=6481,
        thinking_tokens=215,
        cached_tokens=6000,
        tool_use_tokens=50,
    )
    d = usage.model_dump()
    assert d["cached_tokens"] == 6000
    assert d["tool_use_tokens"] == 50


def test_llm_response_to_dict():
    """LLMResponse.to_dict() — 유효한 JSON text."""
    resp = LLMResponse(
        text='{"grade": "A", "score": 95}',
        model="gemini-3-flash-preview",
        request_id="test-123",
    )
    result = resp.to_dict()
    assert result == {"grade": "A", "score": 95}


def test_llm_response_to_dict_invalid_json():
    """LLMResponse.to_dict() — 유효하지 않은 JSON이면 ValueError."""
    resp = LLMResponse(
        text="이것은 JSON이 아닙니다",
        model="gemini-3-flash-preview",
        request_id="test-456",
    )
    with pytest.raises(ValueError, match="JSON 파싱 실패"):
        resp.to_dict()


def test_llm_response_to_dict_nested():
    """LLMResponse.to_dict() — 중첩 JSON."""
    resp = LLMResponse(
        text='{"items": [{"name": "a"}, {"name": "b"}]}',
        model="gemini-3-flash-preview",
        request_id="test-789",
    )
    result = resp.to_dict()
    assert len(result["items"]) == 2


# ── Part 모델 테스트 ──


def test_part_from_uri():
    """Part.from_uri() — 파일 URI 참조 생성."""
    part = Part.from_uri("files/xxx-yyy", "application/pdf")
    assert part.type == "file"
    assert part.file_uri == "files/xxx-yyy"
    assert part.mime_type == "application/pdf"
    assert part.data is None


def test_part_from_uri_requires_mime():
    """Part.from_uri() — mime_type 미지정 시 TypeError."""
    import pytest

    with pytest.raises(TypeError):
        Part.from_uri("https://example.com/file")


def test_part_from_bytes():
    """Part.from_bytes() — 바이너리 데이터 base64 인코딩."""
    raw_data = b"hello world"
    part = Part.from_bytes(raw_data, "text/plain")
    assert part.type == "inline"
    assert part.mime_type == "text/plain"
    assert part.data is not None
    assert part.file_uri is None
    # base64 디코딩하면 원본 복원
    import base64

    assert base64.b64decode(part.data) == raw_data


def test_part_serialization():
    """Part.model_dump(exclude_none=True) — None 필드 제외."""
    part = Part.from_uri("files/abc", "image/png")
    dumped = part.model_dump(exclude_none=True)
    assert "data" not in dumped
    assert dumped == {"type": "file", "file_uri": "files/abc", "mime_type": "image/png"}


def test_part_tool_call_serialization():
    """tool_call Part — model_dump 시 type + tool_call 필드만 노출."""
    from llm_manager._internal.handler_client.models import Part, ToolCallPart

    part = Part(
        type="tool_call",
        tool_call=ToolCallPart(
            tool_type="GOOGLE_SEARCH_WEB",
            args={"q": "test"},
            id="tc_1",
            thought_signature="dHM=",
        ),
    )
    dumped = part.model_dump(exclude_none=True)
    assert dumped["type"] == "tool_call"
    assert dumped["tool_call"]["tool_type"] == "GOOGLE_SEARCH_WEB"
    assert dumped["tool_call"]["thought_signature"] == "dHM="
    assert "file_uri" not in dumped


def test_part_executable_code_serialization():
    from llm_manager._internal.handler_client.models import ExecutableCodePart, Part

    part = Part(
        type="executable_code",
        executable_code=ExecutableCodePart(language="PYTHON", code="print(1)"),
    )
    dumped = part.model_dump(exclude_none=True)
    assert dumped["type"] == "executable_code"
    assert dumped["executable_code"] == {"language": "PYTHON", "code": "print(1)"}


def test_llm_response_parses_grounding_and_assistant_message():
    """handler 응답 JSON 을 LLMResponse 로 파싱 — 신규 필드 모두 보존."""
    from llm_manager._internal.handler_client.models import LLMResponse

    payload = {
        "model": "gemini-3-flash-preview",
        "request_id": "req_xyz",
        "text": "삼성전자 주가는 87,500원입니다.",
        "grounding": {
            "web_search_queries": ["삼성전자 주가"],
            "chunks": [{"title": "mk.co.kr", "uri": "https://vertex.../redir/aaa"}],
            "supports": [{"segment": {"start_index": 0, "end_index": 10}, "chunk_indices": [0]}],
        },
        "tool_calls": [{"tool_type": "GOOGLE_SEARCH_WEB", "id": "tc_1", "thought_signature": "dHM="}],
        "assistant_message": {
            "role": "model",
            "content": [
                {"type": "text", "text": "삼성전자 주가는 87,500원입니다."},
                {"type": "tool_call", "tool_call": {"tool_type": "GOOGLE_SEARCH_WEB", "id": "tc_1"}},
            ],
        },
    }
    resp = LLMResponse.model_validate(payload)
    assert resp.grounding is not None
    assert resp.grounding.chunks[0].title == "mk.co.kr"
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].tool_type == "GOOGLE_SEARCH_WEB"
    assert resp.assistant_message is not None
    assert resp.assistant_message.role == "model"
    parts = resp.assistant_message.content
    assert isinstance(parts, list)
    assert len(parts) == 2
    assert parts[0].type == "text"
    assert parts[1].type == "tool_call"
    assert parts[1].tool_call is not None
    assert parts[1].tool_call.tool_type == "GOOGLE_SEARCH_WEB"


def test_llm_response_parses_url_context():
    """handler 응답의 url_context_metadata — 성공/실패 항목 모두 status 와 함께 보존."""
    from llm_manager._internal.handler_client.models import LLMResponse

    payload = {
        "model": "gemini-3-flash-preview",
        "request_id": "req_url1",
        "text": "기사 요약입니다.",
        "url_context": {
            "url_metadata": [
                {
                    "retrieved_url": "https://en.wikipedia.org/wiki/HTTP",
                    "url_retrieval_status": "URL_RETRIEVAL_STATUS_SUCCESS",
                },
                {
                    "retrieved_url": "https://www.wsj.com/some-article",
                    "url_retrieval_status": "URL_RETRIEVAL_STATUS_PAYWALL",
                },
            ]
        },
    }
    resp = LLMResponse.model_validate(payload)
    assert resp.url_context is not None
    assert len(resp.url_context.url_metadata) == 2
    assert resp.url_context.url_metadata[0].retrieved_url == "https://en.wikipedia.org/wiki/HTTP"
    assert resp.url_context.url_metadata[0].url_retrieval_status == "URL_RETRIEVAL_STATUS_SUCCESS"
    assert resp.url_context.url_metadata[1].url_retrieval_status == "URL_RETRIEVAL_STATUS_PAYWALL"


def test_stream_chunk_parses_url_context():
    """StreamChunk 도 url_context 를 grounding 과 대칭으로 파싱."""
    data = {
        "text": "청크",
        "url_context": {
            "url_metadata": [
                {
                    "retrieved_url": "https://example.com/a",
                    "url_retrieval_status": "URL_RETRIEVAL_STATUS_SUCCESS",
                }
            ]
        },
    }
    chunk = StreamChunk.model_validate(data)
    assert chunk.url_context is not None
    assert chunk.url_context.url_metadata[0].retrieved_url == "https://example.com/a"


def test_ts_wire_roundtrip():
    """ts_to_wire/ts_from_wire — bytes ↔ base64 무손실."""
    from llm_manager._internal.handler_client.models import ts_from_wire, ts_to_wire

    raw = b"\x00\x01\x02hello\xff"
    wire = ts_to_wire(raw)
    assert isinstance(wire, str)
    assert ts_from_wire(wire) == raw
    assert ts_to_wire(None) is None
    assert ts_from_wire(None) is None
