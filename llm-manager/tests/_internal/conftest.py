"""tests/logic/ 공통 fixture.

dedup 관련 fixture는 test_dedup.py 외에 test_resolver_standby.py,
test_runner_standby.py에서도 공유하므로 conftest로 승격.

주의: 이 파일이 상위 tests/conftest.py를 그림자 처리하므로,
기존 test_store.py가 `from conftest import`로
쓰던 SAMPLE_* 상수는 아래에서 re-export.
"""

from __future__ import annotations

import httpx
import pytest

from llm_manager._internal.file_processing import cache_store, dedup
from tests.conftest import (  # noqa: F401 — re-export for `from conftest import ...`
    SAMPLE_LLM_INPUT,
    SAMPLE_RESPONSE,
    SAMPLE_STREAM_CHUNKS,
)


@pytest.fixture(autouse=True)
def _isolate_redis_for_cache_store():
    """cache_store 경로가 실제 dev redis-handler로 나가지 않게 메모리 폴백으로 격리 (v3).

    v3 redis-manager는 redis-handler HTTP 경유 — 기본 handler(192.7.9.45:8800)로
    실제 호출이 나가면 테스트가 외부 상태에 의존/누수된다. redis-manager 공식 테스트
    패턴을 따라 RedisManager Singleton을 리셋하고 'handler 미도달' MockTransport
    backend를 주입한다(일반 명령은 메모리 폴백, NX/XX는 예외). 세션 간 상태 누수
    방지를 위해 메모리 백엔드를 테스트 전후로 flush.

    dedup 쪽 fake_redis/no_redis fixture는 이와 별개 — dedup.RedisManager를 직접 patch.
    """
    from redis_manager.backends.handler_backend import HandlerBackend
    from redis_manager.handler import RedisManager

    def _handler_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("handler down (test default)")

    RedisManager._instance = None
    manager = RedisManager()
    manager._backend.close()
    transport = httpx.MockTransport(_handler_down)
    manager._backend = HandlerBackend(
        client=httpx.Client(transport=transport, base_url="http://test")
    )
    manager._memory.flush()

    yield

    manager._backend.close()
    manager._memory.flush()
    RedisManager._instance = None


class FakeRedisHandler:
    """RedisManager의 setnx / get / delete를 흉내내는 단일 fake (v3).

    v3에서 dedup은 단일 경로 — try_acquire_standby/release_standby(setnx/delete)와
    wait_for_standby_release(get)가 모두 RedisManager 경유. 따라서 하나의 fake로 커버한다.
    분산 의미론 시뮬레이션 위해 메모리 폴백 동작 안 함 — 항상 Redis 가용 상태.
    TTL 만료 시뮬레이션은 안 함 (테스트 scope 밖).
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def setnx(self, key: str, value: str, *, ttl: int | None = None) -> bool:
        if key in self.data:
            return False
        self.data[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0


class FakeFileCacheStore:
    """cache_store._store(FileCacheStore/BaseJsonStore)의 in-memory 대체.

    dedup의 락(standby)과 별개로, files_api/context_cache의 **결과저장** 레이어는
    BaseJsonStore 경유라 RedisManager 패치만으로는 안 가려진다. 스텁하지 않으면
    find/save_files_api·context_cache가 진짜 공유 Redis로 새어, 이전 run이 남긴
    키를 다음 run이 HIT → 업로드/fetch가 skip되며 테스트가 깨진다. TTL 만료 미시뮬.
    """

    def __init__(self) -> None:
        self.files_api: dict[str, dict] = {}
        self.context_cache: dict[str, dict] = {}

    def find_files_api(self, file_checksum: str) -> dict | None:
        return self.files_api.get(file_checksum)

    def save_files_api(self, file_checksum: str, doc: dict, ttl: int | None = None) -> None:
        self.files_api[file_checksum] = doc

    def delete_files_api(self, file_checksum: str) -> None:
        self.files_api.pop(file_checksum, None)

    def find_context_cache(self, hash_key: str) -> dict | None:
        return self.context_cache.get(hash_key)

    def save_context_cache(self, hash_key: str, doc: dict, ttl: int | None = None) -> None:
        self.context_cache[hash_key] = doc

    def delete_context_cache(self, hash_key: str) -> None:
        self.context_cache.pop(hash_key, None)

    def shrink_context_cache_ttl(self, hash_key: str, ttl: int) -> None:
        pass


@pytest.fixture()
def fake_redis(monkeypatch):
    """모든 Redis 접근을 in-memory fake로 교체 (테스트 격리).

    두 레이어를 함께 가린다:
      - 락(standby): `dedup.RedisManager` → FakeRedisHandler (setnx/get/delete)
      - 결과저장: `cache_store._store` → FakeFileCacheStore (files_api/context_cache)

    반환은 락 fake 인스턴스 — 테스트가 `fake_redis.get(key)`로 standby 키 존재
    여부를 검증한다.
    """
    fake = FakeRedisHandler()
    monkeypatch.setattr(dedup, "RedisManager", lambda: fake)
    monkeypatch.setattr(cache_store, "_store", FakeFileCacheStore())
    return fake


@pytest.fixture()
def no_redis(monkeypatch):
    """Redis 전체 미연결 시나리오.

    setnx(NX/XX)는 RedisUnavailableError(폴백 차단), get(일반 명령)은 None 반환
    (v3 메모리 폴백 의미 — strict-read 미지원).
    """
    from redis_manager import RedisUnavailableError

    class _DownHandler:
        def setnx(self, *a, **kw):
            raise RedisUnavailableError("test: Redis down")

        def get(self, *a, **kw):
            return None

        def delete(self, *a, **kw):
            return 0

    monkeypatch.setattr(dedup, "RedisManager", lambda: _DownHandler())


@pytest.fixture()
def fast_backoff(monkeypatch):
    """backoff 상수를 짧게 조정하여 timeout 테스트를 빠르게."""
    monkeypatch.setattr(dedup, "_BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(dedup, "_BACKOFF_CAP", 0.02)
