"""llm-manager 테스트 공통 fixture (서버 제거 후 — TestClient 없음)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import llm_manager
from llm_manager._internal.handler_client.models import LLMResponse, StreamChunk, TokenUsage

# ── 공통 데이터 (테스트 모듈에서 직접 import) ────────────────────

SAMPLE_LLM_INPUT = {
    "contents": "안녕하세요",
    "model": "gemini-2.5-flash",
    "task_name": "test-service",
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


@pytest.fixture(autouse=True)
def _set_service_name(monkeypatch):
    """build_meta 가 env SERVICE_NAME 을 요구하므로 테스트 전역에 기본값 주입.

    SERVICE_NAME 미설정을 검증하는 테스트는 개별적으로 monkeypatch.delenv 한다.
    """
    monkeypatch.setenv("SERVICE_NAME", "test-service")


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

    with patch("llm_manager._internal.store._get_collection", return_value=col):
        yield store


# ── 공통 헬퍼 (테스트 모듈에서 `from conftest import ...`) ──────


def _patch_stream(monkeypatch, inner_factory, meta=None):
    """공개 `llm_request_stream_async` 가 주어진 async 제너레이터를 쓰도록 갈아끼운다.

    `test_sync_stream.py`(정리 계약·증분성)와 `test_public_stream_events.py`(필드 통과)가
    같은 패치 방식을 쓰므로 여기 한 곳에 둔다. 사본을 두면 한쪽만 고쳐져 어긋난다.
    """
    monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

    async def fake_runner_stream(*args, **kwargs):
        return inner_factory(), (meta if meta is not None else {"source": "live"})

    monkeypatch.setattr(
        llm_manager.runner, "llm_request_stream_async", fake_runner_stream,
    )
