"""context_cache_lifecycle 단위 테스트.

Gemini / Redis 호출은 monkeypatch — 외부 시스템 의존 X.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from llm_request._internal.context_cache_lifecycle import expire_caches


@pytest.mark.asyncio
async def test_redis_miss_is_idempotent_success(monkeypatch):
    """Redis 키 없음 → expired (idempotent)."""
    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: None,
    )
    resp = await expire_caches(["missing_hash"], 60)
    assert resp.expired == ["missing_hash"]
    assert resp.failed == []


@pytest.mark.asyncio
async def test_gemini_404_message_fallback_is_expired(monkeypatch):
    """raw 예외 메시지 fallback — '404 Not Found' substring → expired."""
    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: {"cached_content_name": "cachedContents/abc"},
    )

    async def fake_update(name, ttl):
        raise RuntimeError("404 Not Found")

    monkeypatch.setattr(
        "llm_request._internal.context_cache_lifecycle.llm_aio.update_cache",
        fake_update,
    )
    resp = await expire_caches(["h1"], 60)
    assert resp.expired == ["h1"]
    assert resp.failed == []


@pytest.mark.asyncio
async def test_handler_error_404_type_based_is_expired(monkeypatch):
    """HandlerError(status_code=404) 타입 기반 정식 경로 — expired."""
    from llm_request._internal.handler_client.exceptions import HandlerError

    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: {"cached_content_name": "cachedContents/abc"},
    )

    async def fake_update(name, ttl):
        raise HandlerError("cache not found", status_code=404)

    monkeypatch.setattr(
        "llm_request._internal.context_cache_lifecycle.llm_aio.update_cache",
        fake_update,
    )
    resp = await expire_caches(["h1"], 60)
    assert resp.expired == ["h1"]
    assert resp.failed == []


@pytest.mark.asyncio
async def test_handler_error_500_type_based_is_failed(monkeypatch):
    """HandlerError(status_code=500) — 404 아님 → failed."""
    from llm_request._internal.handler_client.exceptions import HandlerError

    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: {"cached_content_name": "cachedContents/abc"},
    )

    async def fake_update(name, ttl):
        raise HandlerError("internal error", status_code=500)

    monkeypatch.setattr(
        "llm_request._internal.context_cache_lifecycle.llm_aio.update_cache",
        fake_update,
    )
    resp = await expire_caches(["h1"], 60)
    assert resp.expired == []
    assert len(resp.failed) == 1
    assert resp.failed[0].cache_id == "h1"


@pytest.mark.asyncio
async def test_success_path(monkeypatch):
    """성공 경로 — 두 cache_id 모두 expired + shrink 호출."""
    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: {"cached_content_name": f"cachedContents/{h}"},
    )
    monkeypatch.setattr(
        "llm_request._internal.context_cache_lifecycle.llm_aio.update_cache",
        AsyncMock(),
    )
    shrink_calls = []
    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.shrink_context_cache_ttl",
        lambda h, t: shrink_calls.append((h, t)),
    )
    resp = await expire_caches(["h1", "h2"], 30)
    assert set(resp.expired) == {"h1", "h2"}
    assert set(shrink_calls) == {("h1", 30), ("h2", 30)}
    assert resp.failed == []


@pytest.mark.asyncio
async def test_gemini_5xx_is_failure(monkeypatch):
    """Gemini 5xx (404 외) → failed."""
    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: {"cached_content_name": "cachedContents/abc"},
    )

    async def fake_update(name, ttl):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(
        "llm_request._internal.context_cache_lifecycle.llm_aio.update_cache",
        fake_update,
    )
    resp = await expire_caches(["h1"], 60)
    assert resp.expired == []
    assert len(resp.failed) == 1
    assert resp.failed[0].cache_id == "h1"
    # 사람이 읽을 한글 문장 + 진단용 클래스명 병기
    assert "실패" in resp.failed[0].error
    assert "RuntimeError" in resp.failed[0].error


@pytest.mark.asyncio
async def test_redis_shrink_failure_still_expired(monkeypatch):
    """Gemini update 성공 + Redis EXPIRE 실패 → expired (Gemini 청구 끝남)."""
    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.find_context_cache",
        lambda h: {"cached_content_name": "cachedContents/abc"},
    )
    monkeypatch.setattr(
        "llm_request._internal.context_cache_lifecycle.llm_aio.update_cache",
        AsyncMock(),
    )

    def shrink_raises(h, t):
        raise RuntimeError("Redis down")

    monkeypatch.setattr(
        "llm_request._internal.file_processing.cache_store.shrink_context_cache_ttl",
        shrink_raises,
    )
    resp = await expire_caches(["h1"], 60)
    assert resp.expired == ["h1"]
    assert resp.failed == []
