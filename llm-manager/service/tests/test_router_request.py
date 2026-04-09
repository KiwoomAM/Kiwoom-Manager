"""router 엔드포인트 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def test_client():
    return TestClient(app)


def test_health_check(test_client):
    """헬스 체크 엔드포인트."""
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_done_check_cache_miss(test_client, mock_collection):
    """done-check: MDB 캐시 miss."""
    resp = test_client.post("/v1/done-check", json={
        "llm_input": {"contents": "테스트", "model": "gemini-2.5-flash"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cache_hit"] is False
    assert len(data["cache_key"]) == 64


def test_done_check_cache_hit(test_client, mock_collection):
    """done-check: MDB 캐시 hit."""
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {"text": "캐시된 응답"},
        "metadata": {},
    }

    resp = test_client.post("/v1/done-check", json={"llm_input": llm_input})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cache_hit"] is True
    assert data["cache_key"] == cache_key


def test_request_cache_hit(test_client, mock_collection):
    """request: MDB 캐시 히트 시 즉시 반환."""
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {
            "text": "캐시된 응답",
            "model": "gemini-2.5-flash",
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            "finish_reason": "STOP",
            "request_id": "req-001",
        },
        "metadata": {"model": "gemini-2.5-flash"},
    }

    resp = test_client.post("/v1/request", json={
        "llm_input": llm_input,
        "cache_mode": True,
        "save_mode": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["cache_hit"] is True
    assert data["result"]["text"] == "캐시된 응답"


def test_request_cache_off(test_client):
    """request: cache_mode=False, save_mode=False → LLM 호출."""
    mock_response = MagicMock()
    mock_response.text = "LLM 응답"
    mock_response.model = "gemini-2.5-flash"
    mock_response.finish_reason = "STOP"
    mock_response.request_id = "req-002"
    mock_response.usage = None
    mock_response.function_calls = None
    mock_response.model_dump.return_value = {
        "text": "LLM 응답",
        "model": "gemini-2.5-flash",
        "finish_reason": "STOP",
        "request_id": "req-002",
        "usage": None,
        "function_calls": None,
    }

    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, return_value=mock_response):
        resp = test_client.post("/v1/request", json={
            "llm_input": {"contents": "테스트", "model": "gemini-2.5-flash"},
            "cache_mode": False,
            "save_mode": False,
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["text"] == "LLM 응답"
    assert data["meta"]["cache_hit"] is False


def test_fetch_not_found(test_client, mock_collection):
    """fetch: 존재하지 않는 cache_key."""
    resp = test_client.post("/v1/fetch", json={
        "cache_key": "0" * 64,
    })
    assert resp.status_code == 500  # KeyError → 500
