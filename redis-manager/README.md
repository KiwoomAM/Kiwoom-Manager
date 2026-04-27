# Redis Handler

> Kiwoom Redis Handler v2.0 — 메모리 폴백 + 재시도 + SET 옵션 내장 Redis 클라이언트

Redis 연결 관리, CRUD, JSON 저장소 베이스를 제공하는 Python 라이브러리. Redis 서버가 불가할 때 메모리 백엔드로 투명하게 폴백하며, 일시적 네트워크 장애는 exponential backoff + jitter로 자동 복구한다.

## v2.0 변경사항

- ✅ **재시도 로직** — `redis.ConnectionError`/`TimeoutError`/`BusyLoadingError` 등 일시 장애 자동 복구
- ✅ **SET 옵션 확장** — `nx`, `xx`, `keepttl` 지원. `setnx()` 편의 메서드 추가
- ✅ **Backend Protocol** — `KeyValueBackend` Protocol로 백엔드 추상화 (OCP/DIP)
- ✅ **도메인 예외** — `RedisHandlerError`, `RedisRetryExhausted`, `RedisUnavailableError`
- ✅ **테스트 커버리지** — 백엔드/handler/base_store 단위 테스트

기존 `RedisHandler().set(k, v, ttl=N)` 같은 호출은 100% 호환.

## 요구사항

- Python >= 3.10
- redis >= 5.0.0
- python-dotenv >= 1.0.0

## 설치

```bash
pip install -e packages/redis-handler
```

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `REDIS_URL` | `redis://192.7.9.45:7617/0` | Redis 서버 URL |
| `REDIS_SOCKET_TIMEOUT` | 5 | 소켓 타임아웃 (초) |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | 5 | 연결 타임아웃 (초) |
| `REDIS_RETRY_MAX_ATTEMPTS` | 3 | 첫 시도 포함 최대 시도 |
| `REDIS_RETRY_INITIAL_BACKOFF` | 0.1 | 첫 백오프 (초) |
| `REDIS_RETRY_MAX_BACKOFF` | 1.0 | 백오프 상한 (초) |
| `REDIS_RETRY_MULTIPLIER` | 2.0 | exp 배율 |
| `REDIS_RETRY_JITTER_RATIO` | 0.2 | 지터 ±20% |

## Quick Start

```python
from redis_handler import RedisHandler

rh = RedisHandler()

# 일반 set/get (Redis 실패 시 메모리 폴백 자동)
rh.set("key", "value", ttl=3600)
val = rh.get("key")
rh.delete("key")

# 분산 잠금 / 캐시 스탬피드 방지 (Redis 필수)
acquired = rh.setnx("lock:job", "owner-A", ttl=60)
if acquired:
    try:
        do_work()
    finally:
        rh.delete("lock:job")
else:
    wait_or_skip()

# 토큰 갱신 (값만, 만료시각 유지)
rh.set("session:abc", new_token, keepttl=True)

# Set 연산
rh.sadd("myset", "a", "b", "c")
members = rh.smembers("myset")
rh.srem("myset", "a")

# 헬스 체크
rh.health_check()  # {"connected": True, "storage": "redis", "config": {...}}
```

## SET 옵션 매트릭스

| 패턴 | 호출 | 의미 |
|---|---|---|
| 영구 저장 | `rh.set(k, v)` | TTL 없음 |
| TTL (legacy) | `rh.set(k, v, ttl=60)` | 60초 후 만료 |
| TTL (신규) | `rh.set(k, v, ex=60)` | 60초 후 만료 |
| 분산 잠금 | `rh.setnx(k, v, ttl=30)` | 30초 락 |
| TTL 유지 갱신 | `rh.set(k, v, keepttl=True)` | 값만 교체 |
| 갱신만 허용 | `rh.set(k, v, xx=True)` | 키 없으면 False |

상호 배타 위반(예: `ttl=10, ex=20` 또는 `nx=True, xx=True`) → `ValueError`.

## API Reference

### RedisHandler

| 메서드 | 반환값 | 설명 |
|---|---|---|
| `get(key)` | `Optional[str]` | 값 조회 |
| `set(key, value, ttl=None, *, ex=None, nx=False, xx=False, keepttl=False)` | `bool` | 값 저장 |
| `setex(key, ttl, value)` | `bool` | TTL과 함께 저장 |
| `setnx(key, value, *, ttl=None)` | `bool` | 키 없을 때만 저장 (분산 잠금) |
| `delete(key)` | `int` | 키 삭제 |
| `exists(key)` | `bool` | 키 존재 여부 |
| `ttl(key)` | `int` | 남은 TTL. -1=영구, -2=없음 |
| `expire(key, seconds)` | `bool` | TTL 설정 |
| `sadd(key, *members)` | `int` | Set에 멤버 추가 |
| `smembers(key)` | `Set[str]` | Set 전체 멤버 |
| `srem(key, *members)` | `int` | Set에서 멤버 제거 |
| `ping()` | `bool` | Redis 연결 확인 |
| `health_check()` | `dict` | `{"connected", "storage", "config"}` |
| `close()` | `None` | 연결 종료 |
| `reset()` | `None` | 연결 종료 + 메모리 초기화 |
| `cleanup_memory()` | `int` | 메모리 백엔드 만료 키 정리 |

| Property | 타입 | 설명 |
|---|---|---|
| `is_redis_connected` | `bool` | Redis 서버 연결 여부 |
| `storage_mode` | `str` | `"redis"` 또는 `"memory"` |

### 예외 처리

```python
from redis_handler import RedisHandler, RedisUnavailableError, RedisRetryExhausted

rh = RedisHandler()

# 일반 명령은 폴백 자동 — 예외 노출 없음
val = rh.get("k")  # Redis 실패 시 메모리에서 조회

# NX/XX는 Redis 필수 — 예외 분기 필요
try:
    acquired = rh.setnx("lock", "owner", ttl=30)
except RedisUnavailableError:
    # Redis 미연결 — 작업 포기 또는 다른 전략
    return
except RedisRetryExhausted:
    # 재시도 소진 — 일시 장애 지속
    return
```

### BaseJsonStore (도메인 store 작성용)

```python
from redis_handler import BaseJsonStore

class MyStore(BaseJsonStore):
    def __init__(self):
        super().__init__(prefix="my:store", default_ttl=3600)

    def save(self, item_id, data):
        return self._set_json(self._make_key(item_id), data)

    def load(self, item_id):
        return self._get_json(self._make_key(item_id))

    # 분산 잠금 패턴
    def acquire_lock(self, item_id):
        return self._setnx_json(
            self._make_key("lock", item_id),
            {"acquired_at": datetime.utcnow().isoformat()},
            ttl=30,
        )
```

| 메서드 | 반환값 | 설명 |
|---|---|---|
| `_make_key(*parts)` | `str` | 프리픽스 기반 키 (`prefix:p1:p2`) |
| `_get_json(key)` | `Optional[Any]` | JSON 역직렬화 조회 |
| `_set_json(key, value, ttl=None, *, nx=False, xx=False, keepttl=False)` | `bool` | JSON 직렬화 저장 |
| `_setnx_json(key, value, ttl=None)` | `bool` | SETNX의 JSON 버전 |
| `_delete(key)` | `int` | 키 삭제 |
| `_exists(key)` | `bool` | 존재 여부 |
| `_ttl(key)` | `int` | 남은 TTL |
| `_expire(key, seconds)` | `bool` | TTL 설정 |
| `_sadd/_smembers/_srem` | | Set 연산 위임 |

## Architecture

```
[Application Code]
   ↓
RedisHandler (orchestrator)
   ├── 입력 검증 (ttl/ex/nx/xx/keepttl 충돌)
   ├── 백엔드 선택 (Redis 가용성)
   └── 폴백 정책 (NX/XX 차단, 일반 명령 자동)
   ↓
KeyValueBackend Protocol
   ├── RedisBackend (redis-py + @retryable)
   └── MemoryBackend (dict + threading.Lock)
```

## 주의사항

- **Singleton**: `RedisHandler()`는 항상 동일 인스턴스. 리셋 필요 시 `rh.reset()`
- **메모리 폴백**: Redis 불가 시 인메모리 dict로 동작. 프로세스 종료 시 데이터 소실
- **TTL**: 초 단위. `None`이면 영구 저장
- **Thread-safe**: 메모리 백엔드는 `threading.Lock`으로 동시 접근 보호
- **자동 초기화**: `import redis_handler` 시 `init_redis()`가 자동 호출됨
- **재시도 비용**: 최악 ~16초 지연 (`socket_timeout=5s × 3 + backoff`)
- **NX/XX 폴백 없음**: 분산 의미론 보호. Redis 미연결 시 `RedisUnavailableError`
