"""tests/logic/ 공통 fixture.

dedup 관련 fixture는 test_dedup.py 외에 test_resolver_standby.py,
test_runner_standby.py에서도 공유하므로 conftest로 승격.

주의: 이 파일이 상위 tests/conftest.py를 그림자 처리하므로,
기존 test_store.py가 `from conftest import`로
쓰던 SAMPLE_* 상수는 아래에서 re-export.
"""

from __future__ import annotations

import pytest

from app.logic.file_processing import dedup
from tests.conftest import (  # noqa: F401 — re-export for `from conftest import ...`
    SAMPLE_LLM_INPUT,
    SAMPLE_RESPONSE,
    SAMPLE_STREAM_CHUNKS,
)


@pytest.fixture(autouse=True)
def _isolate_redis_for_cache_store(monkeypatch):
    """cache_store 경로의 실제 Redis 연결을 끊고 메모리 폴백으로 격리.

    redis-handler 기본 REDIS_URL이 실제 dev Redis(192.7.9.45:7617)를 가리키기
    때문에, 테스트가 실제 Redis에 쓰고 세션 간 상태가 누수되는 문제 방지.

    조치:
    - redis_handler.connection의 client/가용 플래그 강제 False
    - get_client() → None → BaseJsonStore는 자동으로 메모리 백엔드 사용
    - RedisHandler 싱글톤의 _memory 백엔드를 테스트 전후로 flush

    dedup 쪽의 fake_redis fixture는 이와 별개 — dedup.get_client을 직접 patch.
    """
    import redis_handler.connection as conn_mod
    import redis_handler.handler as handler_mod
    from redis_handler.handler import RedisHandler

    monkeypatch.setattr(conn_mod, "_redis_client", None, raising=False)
    monkeypatch.setattr(conn_mod, "_redis_available", False, raising=False)
    monkeypatch.setattr(conn_mod, "init_redis", lambda: False)
    monkeypatch.setattr(handler_mod, "get_client", lambda: None)

    handler = RedisHandler()
    handler._memory.flush()

    yield

    handler._memory.flush()


class _SharedStore:
    """raw client mock 과 RedisHandler mock 이 공유하는 in-memory 저장소.

    dedup 가 hybrid 로 두 API 를 모두 사용하므로 (try_acquire/release 는 handler,
    wait 는 raw client) 두 fake 가 같은 데이터를 봐야 테스트가 정확하다.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}


class FakeRawClient:
    """raw redis-py client 의 .get / .delete 만 흉내내는 fake.

    dedup.wait_for_standby_release 가 raw `get_client()` 로 호출하는 메서드만 커버.
    TTL 만료 시뮬레이션은 안 함 (테스트 scope 밖).
    """

    def __init__(self, store: _SharedStore) -> None:
        self._store = store

    def get(self, key: str) -> str | None:
        return self._store.data.get(key)

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store.data:
                del self._store.data[k]
                count += 1
        return count


class FakeRedisHandler:
    """RedisHandler 의 setnx / delete 만 흉내내는 fake.

    dedup.try_acquire_standby / release_standby 가 호출하는 메서드만 커버.
    분산 의미론 시뮬레이션 위해 메모리 폴백 동작 안 함 — 항상 Redis 가용 상태.
    """

    def __init__(self, store: _SharedStore) -> None:
        self._store = store

    def setnx(self, key: str, value: str, *, ttl: int | None = None) -> bool:
        if key in self._store.data:
            return False
        self._store.data[key] = value
        return True

    def delete(self, key: str) -> int:
        return 1 if self._store.data.pop(key, None) is not None else 0


@pytest.fixture()
def fake_redis(monkeypatch):
    """dedup 모듈의 raw `get_client` 와 `RedisHandler` 를 동시에 fake 로 교체.

    두 fake 가 같은 _SharedStore 를 보므로 setnx 로 박은 키를 raw get 으로 조회 가능.
    반환은 raw client fake — 테스트가 `.get(key)` 로 standby 키 존재 여부 검증할 때
    기존 호출 패턴(`fake_redis.get(...)`) 과 호환.
    """
    store = _SharedStore()
    handler_fake = FakeRedisHandler(store)
    raw_fake = FakeRawClient(store)
    monkeypatch.setattr(dedup, "RedisHandler", lambda: handler_fake)
    monkeypatch.setattr(dedup, "get_client", lambda: raw_fake)
    return raw_fake


@pytest.fixture()
def no_redis(monkeypatch):
    """Redis 전체 미연결 시나리오 — setnx 는 RedisUnavailableError,
    raw get_client 는 None 반환.
    """
    from redis_handler import RedisUnavailableError

    class _DownHandler:
        def setnx(self, *a, **kw):
            raise RedisUnavailableError("test: Redis down")

        def delete(self, *a, **kw):
            return 0

    monkeypatch.setattr(dedup, "RedisHandler", lambda: _DownHandler())
    monkeypatch.setattr(dedup, "get_client", lambda: None)


@pytest.fixture()
def fast_backoff(monkeypatch):
    """backoff 상수를 짧게 조정하여 timeout 테스트를 빠르게."""
    monkeypatch.setattr(dedup, "_BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(dedup, "_BACKOFF_CAP", 0.02)
