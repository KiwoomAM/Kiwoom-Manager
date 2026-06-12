"""_config.py 테스트 (구 providers.py).

외부 의존 초기화 전체를 테스트하진 않고,
Redis startup health check와 standby flush 로직만 검증.
"""

from __future__ import annotations

import pytest

from llm_manager._internal import _config as providers

# ── _ensure_redis_connected ──────────────────────────────────


class TestEnsureRedisConnected:
    def test_raises_when_init_redis_returns_false(self, monkeypatch):
        """init_redis() False → RuntimeError."""
        import redis_manager
        monkeypatch.setattr(redis_manager, "init_redis", lambda: False)

        with pytest.raises(RuntimeError, match="Redis 연결 실패"):
            providers._ensure_redis_connected()

    def test_passes_when_init_redis_returns_true(self, monkeypatch):
        """init_redis() True → 정상 통과."""
        import redis_manager
        monkeypatch.setattr(redis_manager, "init_redis", lambda: True)

        # 예외 없이 통과
        providers._ensure_redis_connected()


# ── _flush_stale_standbys ────────────────────────────────────


class FakeRedisClient:
    """keys + delete만 지원하는 단순 fake."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def seed(self, *keys: str) -> None:
        for k in keys:
            self._store[k] = "dummy"

    def keys(self, pattern: str) -> list[str]:
        # 매우 단순한 glob 매칭 (`*` → `.*`)
        import re
        regex = re.compile("^" + pattern.replace("*", ".*") + "$")
        return [k for k in self._store if regex.match(k)]

    def scan_iter(self, match: str | None = None, count: int | None = None):
        """redis-py scan_iter mock. match 패턴에 매치하는 키 yield."""
        import re
        if match is None:
            yield from list(self._store)
            return
        regex = re.compile("^" + match.replace("*", ".*") + "$")
        for k in list(self._store):  # list화 — yield 중 delete 대응
            if regex.match(k):
                yield k

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count


class TestFlushStaleStandbys:
    def test_removes_file_cache_standby_keys(self, monkeypatch):
        client = FakeRedisClient()
        client.seed(
            "file_cache:files_api_standby:abc",
            "file_cache:context_cache_standby:xyz",
            "file_cache:files_api:keep",  # 본 캐시 — 보존
        )
        import redis_manager
        monkeypatch.setattr(redis_manager, "RedisManager", lambda: client)

        providers._flush_stale_standbys()

        assert client.keys("file_cache:*_standby:*") == []
        assert "file_cache:files_api:keep" in client._store  # 본 캐시 보존

    def test_removes_agent_llm_standby_keys(self, monkeypatch):
        client = FakeRedisClient()
        client.seed(
            "agent_llm_standby:ck1",
            "agent_llm_standby:ck2",
        )
        import redis_manager
        monkeypatch.setattr(redis_manager, "RedisManager", lambda: client)

        providers._flush_stale_standbys()

        assert client.keys("agent_llm_standby:*") == []

    def test_no_standbys_is_noop(self, monkeypatch):
        client = FakeRedisClient()
        client.seed("file_cache:files_api:xyz")  # 본 캐시만 존재
        import redis_manager
        monkeypatch.setattr(redis_manager, "RedisManager", lambda: client)

        providers._flush_stale_standbys()  # 아무 것도 삭제 안 함

        assert "file_cache:files_api:xyz" in client._store

    def test_redis_unavailable_does_not_raise(self, monkeypatch):
        """정리할 standby 키가 없으면 삭제 없이 통과 (방어).

        v3 scan_iter/delete는 handler 미도달 시 메모리 폴백이라 별도 예외 경로가
        없다 — 빈 결과(메모리 폴백 포함)에서도 raise하지 않음을 검증."""
        client = FakeRedisClient()  # seed 없음 → scan_iter 빈 결과
        import redis_manager
        monkeypatch.setattr(redis_manager, "RedisManager", lambda: client)

        providers._flush_stale_standbys()  # no raise

    def test_mixed_namespaces_both_cleaned(self, monkeypatch):
        client = FakeRedisClient()
        client.seed(
            "file_cache:files_api_standby:a",
            "file_cache:context_cache_standby:b",
            "agent_llm_standby:c",
            "file_cache:files_api:keep",  # 본 캐시 보존
        )
        import redis_manager
        monkeypatch.setattr(redis_manager, "RedisManager", lambda: client)

        providers._flush_stale_standbys()

        assert len(client._store) == 1
        assert "file_cache:files_api:keep" in client._store
