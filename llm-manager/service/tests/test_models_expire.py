"""Expire Context Cache 요청/응답 Pydantic 모델 단위 테스트."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.requests import ExpireContextCacheRequest
from app.models.responses import ExpireContextCacheResponse, ExpireFailure


class TestExpireRequest:
    """ExpireContextCacheRequest validation."""

    def test_requires_at_least_one_cache_id(self):
        with pytest.raises(ValidationError):
            ExpireContextCacheRequest(cache_ids=[])

    def test_grace_seconds_default_3(self):
        req = ExpireContextCacheRequest(cache_ids=["abc"])
        assert req.grace_seconds == 3

    def test_grace_seconds_lower_bound(self):
        with pytest.raises(ValidationError):
            ExpireContextCacheRequest(cache_ids=["abc"], grace_seconds=0)

    def test_grace_seconds_upper_bound(self):
        with pytest.raises(ValidationError):
            ExpireContextCacheRequest(cache_ids=["abc"], grace_seconds=3601)

    def test_grace_seconds_within_range_ok(self):
        req = ExpireContextCacheRequest(cache_ids=["abc"], grace_seconds=300)
        assert req.grace_seconds == 300


class TestExpireResponse:
    """ExpireContextCacheResponse + ExpireFailure shape."""

    def test_response_shape(self):
        resp = ExpireContextCacheResponse(
            expired=["abc"],
            failed=[ExpireFailure(cache_id="xyz", error="boom")],
        )
        assert resp.expired == ["abc"]
        assert resp.failed[0].cache_id == "xyz"
        assert resp.failed[0].error == "boom"

    def test_empty_lists_allowed(self):
        resp = ExpireContextCacheResponse(expired=[], failed=[])
        assert resp.expired == []
        assert resp.failed == []
