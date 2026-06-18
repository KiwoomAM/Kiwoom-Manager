"""공개 API `delete_context_cache` / `_async` 단위 테스트 — docs/26 §3.2/§4.3.

구 `delete_cache` 제거 + 5초 고정(시간 인자 없음) + cachedContents 가드.
"""
from __future__ import annotations

import inspect

import pytest


def test_in_all_and_old_name_removed():
    import llm_manager
    assert "delete_context_cache" in llm_manager.__all__
    assert "delete_context_cache_async" in llm_manager.__all__
    # breaking: 구 이름은 완전히 제거 (별칭 없음)
    assert "delete_cache" not in llm_manager.__all__
    assert "delete_cache_async" not in llm_manager.__all__
    assert not hasattr(llm_manager, "delete_cache")
    assert not hasattr(llm_manager, "delete_cache_async")


def test_no_grace_seconds_param():
    """시간 인자 제거 — 시그니처에 grace_seconds 없음 (무조건 5초)."""
    import llm_manager
    params = inspect.signature(llm_manager.delete_context_cache_async).parameters
    assert "grace_seconds" not in params
    assert "ttl" not in params


@pytest.mark.asyncio
async def test_rejects_cached_content_name():
    import llm_manager
    with pytest.raises(ValueError):
        await llm_manager.delete_context_cache_async(["cachedContents/abc"])


@pytest.mark.asyncio
async def test_empty_ids_raises():
    import llm_manager
    with pytest.raises(ValueError):
        await llm_manager.delete_context_cache_async([])


@pytest.mark.asyncio
async def test_delegates_with_fixed_5s(monkeypatch):
    """단일 str 정규화 + 무조건 grace=5 로 expire_caches 위임."""
    import llm_manager
    from llm_manager.models import ExpireResult

    monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)
    captured: dict = {}

    async def fake_expire(ids, grace):
        captured.update(ids=ids, grace=grace)
        return ExpireResult(expired=ids, failed=[])

    monkeypatch.setattr(llm_manager, "expire_caches", fake_expire)

    res = await llm_manager.delete_context_cache_async("h1")
    assert captured == {"ids": ["h1"], "grace": 5}
    assert res.expired == ["h1"]
