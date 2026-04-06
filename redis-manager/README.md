# Redis Handler

> Kiwoom Redis Handler v1.0 — 메모리 폴백 포함 범용 Redis 클라이언트

Redis 연결 관리, 기본 CRUD, JSON 저장소 베이스를 제공하는 Python 라이브러리입니다.
Redis 서버가 불가할 때 메모리 백엔드로 투명하게 폴백하므로, 호출자는 Redis 가용 여부를 신경쓸 필요 없이 동일한 API를 사용합니다.

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
|------|--------|------|
| `REDIS_URL` | `redis://192.7.9.45:7617/0` | Redis 서버 URL |
| `REDIS_SOCKET_TIMEOUT` | `5` | 소켓 타임아웃 (초) |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | `5` | 연결 타임아웃 (초) |

## 디렉터리 구조

```
redis_handler/
├── __init__.py      # 공개 API + 전역 인스턴스 초기화
├── config.py        # RedisConfig (환경변수 기��� 설정)
├── connection.py    # 연결 관리 (init/close/get_client)
├── handler.py       # RedisHandler (Singleton CRUD) + _MemoryBackend
└── base_store.py    # BaseJsonStore (JSON 저장소 베이스 클래스)
```

## Quick Start

```python
from redis_handler import RedisHandler

rh = RedisHandler()

# String CRUD
rh.set("key", "value", ttl=3600)
val = rh.get("key")
rh.delete("key")

# Set 연산
rh.sadd("myset", "a", "b", "c")
members = rh.smembers("myset")
rh.srem("myset", "a")

# TTL / Key
rh.expire("key", 600)
remaining = rh.ttl("key")
rh.exists("key")

# 상태 확인
rh.health_check()  # {"connected": True, "storage": "redis", "config": {...}}
rh.close()
```

## API Reference

### RedisConfig

Redis 연결 설정. 환경변수에서 자동 로드되며, `configure()`로 명시적 설정도 ��능합니다.

```python
from redis_handler import RedisConfig

# 명시적 설정
RedisConfig.configure(url="redis://localhost:6379/0", socket_timeout=10)

# 환경변수에서 재로드
RedisConfig.reload_from_env()

# 설정 확인
RedisConfig.get_summary()    # {"url": "...", "socket_timeout": 5, ...}
RedisConfig.is_configured()  # True
```

### RedisHandler

Singleton CRUD 핸들러. Redis 불가 시 메모리로 자동 폴백합니다.

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| **String** | | |
| `get(key)` | `Optional[str]` | 값 조회 |
| `set(key, value, ttl=None)` | `bool` | 값 저장 (ttl: 초, None이면 영구) |
| `setex(key, ttl, value)` | `bool` | TTL과 함께 저장 (Redis 호환 시그니처) |
| `delete(key)` | `int` | 키 삭제 (삭제된 수 반���) |
| **Key** | | |
| `exists(key)` | `bool` | 키 존재 여부 |
| `ttl(key)` | `int` | 남은 TTL (초). -1 = 영구, -2 = 키 없음 |
| `expire(key, seconds)` | `bool` | 키에 TTL 설정 |
| **Set** | | |
| `sadd(key, *members)` | `int` | Set에 멤버 추가 (새로 추가된 수) |
| `smembers(key)` | `Set[str]` | Set 전체 멤버 조회 |
| `srem(key, *members)` | `int` | Set에서 멤버 제거 (제거된 수) |
| **유틸리티** | | |
| `ping()` | `bool` | Redis 연결 확인 |
| `health_check()` | `dict` | `{"connected": bool, "storage": str, "config": dict}` |
| `close()` | `None` | ��결 종료 |
| `reset()` | `None` | 연결 종료 + 메모리 초기화 (테스트용) |
| `cleanup_memory()` | `int` | 메모리 백엔드 만료 키 정리 |

| Property | 타입 | 설명 |
|----------|------|------|
| `is_redis_connected` | `bool` | Redis 서버 연결 여부 |
| `storage_mode` | `str` | 현재 모드: `"redis"` 또는 `"memory"` |

### Connection 함수 (저수준)

일반적으로 `RedisHandler`를 통해 접근하면 되지만, 직접 제어가 필요할 때 사용합니다.

```python
from redis_handler import init_redis, get_client, is_available, close_redis

init_redis()              # Redis 초기화 (모듈 로드 시 자동 호출)
client = get_client()     # redis.Redis 인스턴스 또는 None
is_available()            # 연결 가능 여부
close_redis()             # 연결 종료
check_redis_connection()  # 헬스 체크 (verbose 로그 출력)
```

### BaseJsonStore

도메인별 JSON 저장소를 만들기 위한 베이스 클래스. 키 프리픽스, JSON 직렬화/역직렬화, TTL 관리를 제공합니다.

```python
from redis_handler import BaseJsonStore

class MyStore(BaseJsonStore):
    def __init__(self):
        super().__init__(prefix="my:store", default_ttl=3600)

    def save(self, item_id: str, data: dict) -> bool:
        return self._set_json(self._make_key(item_id), data)

    def load(self, item_id: str) -> dict | None:
        return self._get_json(self._make_key(item_id))

    def remove(self, item_id: str) -> int:
        return self._delete(self._make_key(item_id))

store = MyStore()
store.save("abc", {"name": "test", "score": 95})
data = store.load("abc")  # {"name": "test", "score": 95}
```

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `_make_key(*parts)` | `str` | 프리픽스 기반 키 생성 (`prefix:part1:part2`) |
| `_get_json(key)` | `Optional[Any]` | JSON 역직렬화하여 조회 |
| `_set_json(key, value, ttl=None)` | `bool` | JSON 직렬화하여 저장 (ttl 미지정 시 default_ttl 사용) |
| `_delete(key)` | `int` | 키 삭제 |
| `_exists(key)` | `bool` | 키 존재 여부 |
| `_ttl(key)` | `int` | 남은 TTL |
| `_expire(key, seconds)` | `bool` | TTL 설정 |
| `_sadd(key, *members)` | `int` | Set 멤버 추가 |
| `_smembers(key)` | `set` | Set 멤버 조회 |
| `_srem(key, *members)` | `int` | Set 멤버 제거 |
| `storage_mode` (property) | `str` | `"redis"` 또는 `"memory"` |

## 사용 패턴

### 전역 싱글톤 인스턴스

```python
from redis_handler import redis_handler

# 모듈 로드 시 자동 초기화된 전역 인스턴스
redis_handler.set("key", "value")
redis_handler.get("key")
```

### 도메인 Store 구현 (실제 사용 예시)

백엔드의 `ChatHistoryStore`가 `BaseJsonStore`를 상속하여 세션 채팅 이력을 관리합니다:

```python
from redis_handler import BaseJsonStore

class ChatHistoryStore(BaseJsonStore):
    def __init__(self, ttl=604800, max_size=20):
        super().__init__(prefix="chat:history", default_ttl=ttl)
        self._max_size = max_size

    def get(self, session_id: str) -> list:
        return self._get_json(self._make_key(session_id)) or []

    def add(self, session_id: str, message: dict) -> None:
        history = self.get(session_id)
        history.append(message)
        history = history[-self._max_size:]  # 최대 크기 유지
        self._set_json(self._make_key(session_id), history)

    def clear(self, session_id: str) -> None:
        self._delete(self._make_key(session_id))
```

키 형식: `chat:history:{session_id}`

## 주의사항

- **Singleton**: `RedisHandler()`는 항상 동일 인스턴스를 반환. 리셋 필요 시 `rh.reset()` 사용
- **메모리 폴백**: Redis 불가 시 인메모리 dict로 동작. 프로세스 종료 시 데이터 소실
- **TTL**: `set(key, value, ttl=3600)` — 초 단위. `None`이면 영구 저장
- **Thread-safe**: 메모리 백엔드는 `threading.Lock`으로 동시 접근 보호
- **자동 초기화**: `import redis_handler` 시 `init_redis()`가 자동 호출됨
- **에러 처리**: Redis 명령 실패 시 warning 로그 후 메모리 폴백으로 자동 전환 (예외를 던지지 않음)
