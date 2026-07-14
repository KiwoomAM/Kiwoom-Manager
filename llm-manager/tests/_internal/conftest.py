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

    # cache_store._store 는 import 시점에 RedisManager 를 박제(BaseJsonStore.__init__:
    # `self._handler = handler or RedisManager()`)하므로, 위 싱글톤 교체가 닿지 않는다.
    # 메모리 매니저 기준으로 _store 를 재생성해 결과저장(files_api/context_cache)도 같은
    # 격리에 포함시킨다 (teardown 에서 원복).
    _orig_store = cache_store._store
    cache_store._store = cache_store.FileCacheStore()

    yield

    cache_store._store = _orig_store
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
        self.ttls: dict[str, int | None] = {}  # 마지막 set/setnx 의 ttl (검증용)

    def setnx(self, key: str, value: str, *, ttl: int | None = None) -> bool:
        if key in self.data:
            return False
        self.data[key] = value
        self.ttls[key] = ttl
        return True

    def set(self, key: str, value: str, ttl: int | None = None, **kwargs) -> bool:
        # record_failure 용 — 덮어쓰기 허용(setnx 아님). ttl 은 검증 위해 보관.
        self.data[key] = value
        self.ttls[key] = ttl
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, key: str) -> int:
        self.ttls.pop(key, None)
        return 1 if self.data.pop(key, None) is not None else 0


@pytest.fixture()
def fake_redis(monkeypatch):
    """dedup의 락(standby) 레이어를 in-memory fake로 교체.

    `dedup.RedisManager` → FakeRedisHandler (setnx/get/delete). standby 선점/해제는
    NX/XX 가 필요한데 autouse `_isolate_redis_for_cache_store` 의 메모리 폴백은 NX/XX 를
    지원하지 않으므로 dedup 만 별도 fake 로 가린다.

    결과저장(cache_store/files_api·context_cache) 격리는 autouse fixture 가 담당한다
    (메모리 매니저 기준으로 `cache_store._store` 재생성).

    반환은 fake 인스턴스 — 테스트가 `fake_redis.get(key)`로 standby 키 존재를 확인.
    """
    fake = FakeRedisHandler()
    monkeypatch.setattr(dedup, "RedisManager", lambda: fake)
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


@pytest.fixture()
def patient_waiter(monkeypatch):
    """timeout 라운드 소진(liveness 탈출구)을 사실상 끄는 fixture.

    C2 수정 후 대기자는 timeout 에서 SETNX 재획득에 실패하면 다음 라운드로 계속 기다리고,
    _MAX_TIMEOUT_ROUNDS 를 소진해야만 중복을 감수하고 자기가 작업한다. "느린 leader 를
    기다린다"를 검증하는 테스트가 이 탈출구에 **먼저** 걸리면 결과가 벽시계 타이밍에
    좌우돼 플래키해진다(부하에 따라 라운드 소진 vs leader 완료 순서가 바뀜).
    라운드 상한을 크게 잡아 그 경쟁을 제거한다 — 검증 대상은 "중복 작업 0" 이지
    탈출구 발동 시점이 아니다.
    """
    monkeypatch.setattr(dedup, "_MAX_TIMEOUT_ROUNDS", 100)
