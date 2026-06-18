"""공개 API `update_context_cache` / `_async` 단위 테스트 — docs/26 §3.3/§4.5.

검증(빈 ids / ttl 범위 / cachedContents 가드)은 ensure_configured 전에 일어나므로
실 redis 없이 ValueError 만 확인. 위임은 ensure_configured·extend_caches mock.
"""
from __future__ import annotations

import pytest


def test_in_all():
    import llm_manager
    assert "update_context_cache" in llm_manager.__all__
    assert "update_context_cache_async" in llm_manager.__all__


@pytest.mark.asyncio
async def test_rejects_cached_content_name():
    """Gemini cache id(`cachedContents/...`)를 넣으면 ValueError (hash_key 만 받음)."""
    import llm_manager
    with pytest.raises(ValueError):
        await llm_manager.update_context_cache_async(["cachedContents/abc"], ttl=60)


@pytest.mark.asyncio
async def test_empty_ids_raises():
    import llm_manager
    with pytest.raises(ValueError):
        await llm_manager.update_context_cache_async([], ttl=60)


@pytest.mark.asyncio
async def test_ttl_out_of_range_raises():
    import llm_manager
    with pytest.raises(ValueError):
        await llm_manager.update_context_cache_async(["h1"], ttl=0)
    with pytest.raises(ValueError):
        await llm_manager.update_context_cache_async(["h1"], ttl=3601)


@pytest.mark.asyncio
async def test_delegates_to_extend_caches(monkeypatch):
    """단일 str 정규화 + ttl 전달 → extend_caches 위임."""
    import llm_manager
    from llm_manager.models import ExpireResult

    monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)
    captured: dict = {}

    async def fake_extend(ids, ttl):
        captured.update(ids=ids, ttl=ttl)
        return ExpireResult(expired=ids, failed=[])

    monkeypatch.setattr(llm_manager, "extend_caches", fake_extend)

    res = await llm_manager.update_context_cache_async("h1", ttl=1800)
    assert captured == {"ids": ["h1"], "ttl": 1800}
    assert res.expired == ["h1"]
