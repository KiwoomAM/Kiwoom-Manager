"""llm-request 테스트 공통 fixture (서버 제거 후 — TestClient 없음)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from llm_handler.models import LLMResponse, StreamChunk, TokenUsage

# ── 공통 데이터 (테스트 모듈에서 직접 import) ────────────────────

SAMPLE_LLM_INPUT = {
    "contents": "안녕하세요",
    "model": "gemini-2.5-flash",
    "service_name": "test-service",
}

SAMPLE_RESPONSE = LLMResponse(
    text="응답입니다",
    model="gemini-2.5-flash",
    usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    finish_reason="STOP",
    request_id="req-001",
)

SAMPLE_STREAM_CHUNKS = [
    StreamChunk(text="청크1", finish_reason=None, usage=None),
    StreamChunk(
        text="청크2",
        finish_reason="STOP",
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    ),
]


@pytest.fixture()
def mock_collection():
    """MongoDB 컬렉션을 인메모리 dict 기반으로 모킹."""
    store: dict[str, dict] = {}

    col = MagicMock()

    def _find_one(query: dict):
        _id = query.get("_id")
        return store.get(_id)

    def _replace_one(filter_dict: dict, doc: dict, upsert: bool = False):
        _id = filter_dict.get("_id")
        if _id:
            store[_id] = doc

    col.find_one = MagicMock(side_effect=_find_one)
    col.replace_one = MagicMock(side_effect=_replace_one)

    with patch("llm_request._internal.store._get_collection", return_value=col):
        yield store
