"""tests/logic/ 공통 fixture.

dedup 관련 fixture는 test_dedup.py 외에 test_resolver_standby.py,
test_runner_standby.py에서도 공유하므로 conftest로 승격.

주의: 이 파일이 상위 tests/conftest.py를 그림자 처리하므로,
기존 test_store.py / test_orchestrator.py가 `from conftest import`로
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


class FakeRedisClient:
    """SETNX(NX/EX), GET, DELETE만 지원하는 단순 in-memory fake.

    TTL(ex)은 저장만 하고 만료 시뮬레이션은 안 함 (테스트 scope 밖).
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self._store:
            return None  # SETNX 실패
        self._store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count


@pytest.fixture()
def fake_redis(monkeypatch):
    """dedup.get_client을 FakeRedisClient로 교체."""
    client = FakeRedisClient()
    monkeypatch.setattr(dedup, "get_client", lambda: client)
    return client


@pytest.fixture()
def no_redis(monkeypatch):
    """dedup.get_client이 None 반환 — Redis 미연결 시나리오."""
    monkeypatch.setattr(dedup, "get_client", lambda: None)


@pytest.fixture()
def fast_backoff(monkeypatch):
    """backoff 상수를 짧게 조정하여 timeout 테스트를 빠르게."""
    monkeypatch.setattr(dedup, "_BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(dedup, "_BACKOFF_CAP", 0.02)
