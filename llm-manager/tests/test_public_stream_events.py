"""공개 이벤트 계층이 `StreamChunk` 의 필드를 통과시키는가 — docs/37 §5.1.

`_events()` 는 밑단이 준 `StreamChunk` 를 이벤트 dict 로 **새로 조립**한다. 그 조립에서
`text`·`finish_reason`·`usage` 셋만 고르고 나머지 일곱을 버리는 것이 이슈 #99 다. 도구를
쓰는 소비자는 함수 호출이 이벤트에 안 실려 매니저를 못 쓴다.

`test_sync_stream.py`(정리 계약·증분성)와 축이 다르다. 그쪽은 "언제 닫히는가"를 보고
이 파일은 "무엇이 실리는가"를 본다. 패치 헬퍼(`_patch_stream`)만 conftest 에서 공유한다.
"""
from __future__ import annotations

import json

from conftest import _patch_stream

import llm_manager
from llm_manager._internal.handler_client.models import (
    CodeBlock,
    CodeResult,
    FunctionCall,
    Grounding,
    GroundingChunk,
    StreamChunk,
    TokenUsage,
    ToolCallInfo,
    ToolResponseInfo,
    UrlContextMetadata,
    UrlMetadata,
)


async def _collect(monkeypatch, chunks: list[StreamChunk]) -> list[dict]:
    """주어진 청크들을 흘려보내고 공개 이벤트 목록을 받는다."""
    async def inner():
        for c in chunks:
            yield c

    _patch_stream(monkeypatch, inner)
    stream, _meta = await llm_manager.llm_request_stream_async({"contents": "x"})
    return [e async for e in stream]


def _grounding() -> Grounding:
    return Grounding(
        web_search_queries=["삼성전자 주가"],
        chunks=[GroundingChunk(title="mk.co.kr", uri="https://vertex.../a")],
    )


def _url_context() -> UrlContextMetadata:
    return UrlContextMetadata(
        url_metadata=[
            UrlMetadata(
                retrieved_url="https://example.com/a",
                url_retrieval_status="URL_RETRIEVAL_STATUS_SUCCESS",
            )
        ]
    )


# ── 불변식 — 상수가 StreamChunk 를 다 덮는가 ────────────────────


def test_all_stream_chunk_fields_are_covered():
    """이벤트가 옮기지 못하는 `StreamChunk` 필드가 남아 있으면 안 된다.

    `_events()` 는 필드를 상수 튜플로 순회한다. 상수에서 하나를 빠뜨려도 타입 오류도
    테스트 실패도 없이 **그 필드만 조용히 사라진다** — 이슈 #99 와 같은 부류다. 케이스를
    손으로 나열하는 아래 테스트들로는 못 잡으므로 불변식으로 붙든다. `StreamChunk` 에
    필드가 늘면 여기서 터진다.

    한계: 합집합만 보므로 리스트 필드를 REPLACE 쪽에 잘못 넣는 **오배치**는 못 잡는다.
    """
    from llm_manager._internal.handler_client._common import (
        STREAM_EXTEND_FIELDS,
        STREAM_REPLACE_FIELDS,
    )

    always_present = {"text", "finish_reason", "usage"}
    covered = set(STREAM_EXTEND_FIELDS) | set(STREAM_REPLACE_FIELDS)

    assert covered == set(StreamChunk.model_fields) - always_present


# ── 필드 통과 ──────────────────────────────────────────────────


async def test_tool_fields_pass_through(monkeypatch):
    """`function_calls` 가 실린 청크는 이벤트에도 실려야 한다 (이슈 #99 본체)."""
    events = await _collect(monkeypatch, [
        StreamChunk(
            text="",
            function_calls=[FunctionCall(name="get_price", args={"sym": "005930"}, id="fc-1")],
        ),
    ])

    assert len(events) == 1
    assert events[0]["function_calls"] == [
        {"name": "get_price", "args": {"sym": "005930"}, "id": "fc-1", "thought_signature": None},
    ]


async def test_every_tool_field_passes_through(monkeypatch):
    """리스트 5종이 모두 통과한다 — `function_calls` 하나만 되는 것이 아니다."""
    events = await _collect(monkeypatch, [
        StreamChunk(
            text="",
            function_calls=[FunctionCall(name="f", id="fc-1")],
            tool_calls=[ToolCallInfo(tool_type="GOOGLE_SEARCH_WEB", id="tc-1")],
            tool_responses=[ToolResponseInfo(tool_type="GOOGLE_SEARCH_WEB", id="tc-1")],
            code_blocks=[CodeBlock(language="PYTHON", code="print(1)", id="cb-1")],
            code_results=[CodeResult(outcome="OUTCOME_OK", output="1", id="cr-1")],
        ),
    ])

    ev = events[0]
    assert [fc["name"] for fc in ev["function_calls"]] == ["f"]
    assert ev["tool_calls"][0]["tool_type"] == "GOOGLE_SEARCH_WEB"
    assert ev["tool_responses"][0]["tool_type"] == "GOOGLE_SEARCH_WEB"
    assert ev["code_blocks"][0]["code"] == "print(1)"
    assert ev["code_results"][0]["output"] == "1"


async def test_grounding_and_url_context_pass_through(monkeypatch):
    """리스트가 아닌 두 필드도 **청크별로** 그대로 실린다 (매니저가 병합하지 않는다)."""
    events = await _collect(monkeypatch, [
        StreamChunk(text="a", grounding=_grounding()),
        StreamChunk(text="b", url_context=_url_context()),
    ])

    assert events[0]["grounding"]["web_search_queries"] == ["삼성전자 주가"]
    assert "url_context" not in events[0]
    assert events[1]["url_context"]["url_metadata"][0]["retrieved_url"] == "https://example.com/a"
    assert "grounding" not in events[1]


# ── 기존 소비자 보호 ───────────────────────────────────────────


async def test_absent_fields_omitted(monkeypatch):
    """값이 없으면 키 자체가 없어야 한다 — 기존 소비자의 이벤트 모양이 안 바뀐다.

    리스트는 `None` 과 `[]` 를 모두 걸러야 하고, 컨테이너는 `None` 을 걸러야 한다.
    `Grounding()` 은 비어 있어도 truthy 라 truthiness 로 판정하면 빈 껍데기가 실린다.
    """
    events = await _collect(monkeypatch, [
        StreamChunk(
            text="hi",
            finish_reason="STOP",
            usage=TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3),
            function_calls=[],          # 빈 리스트도 실리면 안 됨
        ),
    ])

    assert set(events[0]) == {"type", "text", "finish_reason", "usage"}


async def test_list_fields_are_per_chunk(monkeypatch):
    """두 청크에 나뉜 `function_calls` 는 각 이벤트에 따로 실린다.

    이벤트 층은 누적하지 않는다 — 누적은 소비자 몫이다(저장·캐시 층의 `_StreamMeta`
    누적과는 별개). 여기서 합쳐 버리면 소비자가 청크 경계를 잃는다.
    """
    events = await _collect(monkeypatch, [
        StreamChunk(text="", function_calls=[FunctionCall(name="a", id="fc-1")]),
        StreamChunk(text="", function_calls=[FunctionCall(name="b", id="fc-2")]),
    ])

    assert [fc["name"] for fc in events[0]["function_calls"]] == ["a"]
    assert [fc["name"] for fc in events[1]["function_calls"]] == ["b"]


async def test_event_is_json_serializable(monkeypatch):
    """이벤트는 그대로 JSON 으로 떨어져야 한다 — 소비자가 SSE 로 다시 내보내는 자리다."""
    events = await _collect(monkeypatch, [
        StreamChunk(
            text="hi",
            finish_reason="STOP",
            usage=TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3),
            function_calls=[FunctionCall(name="f", args={"a": 1}, id="fc-1")],
            grounding=_grounding(),
            url_context=_url_context(),
        ),
    ])

    json.dumps(events[0])  # 예외가 나면 실패
