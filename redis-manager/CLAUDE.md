# Redis Manager

redis-handler HTTP 서버 경유 Redis 클라이언트 패키지(`redis_manager`). 외부 에이전트가
GitHub에서 import해 쓰는 것을 전제로 한다. Redis 서버가 불가할 때 인메모리 백엔드로
투명하게 폴백하고, retry는 handler 서버가 담당한다.

> 사용자 가이드·API 표는 @README.md 참조. 이 문서는 코드 수정 시 알아야 할 것만 담는다.

## 편집 범위

- **이 패키지(`packages/redis-manager/`)만 수정한다.** mdb-manager·vdb-manager는 같은 팀
  영역이지만 그 외 `*-manager` / `*-handler`는 **다른 담당자 영역 — 절대 수정 금지.**

## Commands

```bash
# 로컬 (python3 + pytest + redis 모듈 가용 시)
cd packages/redis-manager && python3 -m pytest tests/ -v
```

## Architecture

3계층 분리:
- **handler.py** — orchestrator. 백엔드 선택, 폴백 정책, 입력 검증
- **backends/** — `KeyValueBackend` Protocol 구현. `HandlerBackend`(httpx → redis-handler 서버), `MemoryBackend`(dict + Lock)
- **base_store.py** — `BaseJsonStore`. 도메인 store의 베이스 (JSON 직렬화, 키 프리픽스)

v3: transport는 redis-handler HTTP 서버(`Kiwoom-Handler/redis-handler`, 포트 8800) 경유.
retry는 handler 서버 담당. 가용성 캐시 플래그 없음 — 모든 판단은 per-call.

## Conventions

- **신규 코드는 RedisManager API만 사용.** `get_client()`는 v3에서 항상 None (deprecated shim)
- **분산 의미론 필요한 명령(NX/XX)은 `setnx()` 등 Handler 메서드 사용.** handler 미도달 시 `RedisUnavailableError`, handler가 재시도 소진 보고 시 `RedisRetryExhausted` — 메모리 폴백 없음
- **일반 명령(get/set 등)은 폴백 자동.** 호출자가 try/except로 메모리 백업 구현 금지
- **새 명령 추가 시** handler 서버 엔드포인트 → `KeyValueBackend` Protocol → `HandlerBackend` → `MemoryBackend` → `RedisManager` 순으로 갱신
- 환경변수는 `os.getenv`에서 직접 읽는다. `.env` 자동 로드는 호출 애플리케이션 책임 (`RedisConfig.configure(handler_url=...)`로 코드 설정도 가능)

## Gotchas

- `RedisManager()`는 Singleton — 테스트에서 `RedisManager._instance = None`으로 리셋 후 MockTransport backend 주입 (tests/conftest.py 참조)
- `health_check()`/`is_redis_connected`/`ping()`은 **매 호출 HTTP 1회** (live PING) — hot path에서 호출 금지
- HTTP 클라이언트는 첫 명령 시점 lazy 생성 — import 후 `RedisConfig.configure(handler_url=...)`도 첫 사용 전이면 반영됨
- 메모리 폴백 데이터는 프로세스 종료 시 휘발 — 영속성 필요 시 handler 가용성 모니터링
- gunicorn 4 workers는 각자 독립 Singleton — 의도된 동작
- **테스트에서 `connection._redis_available` 직접 주입 금지** — v3에서 해당 상태 자체가 없음

## Environment Variables

| 변수 | 기본값 | 의미 |
|---|---|---|
| `REDIS_HANDLER_URL` | `http://192.7.9.45:8800` | redis-handler 서버 URL |
| `REDIS_HANDLER_TIMEOUT` | 5 | handler HTTP timeout (초) |
| `SERVICE_NAME` | (없음) | `X-Caller-Service` 헤더 |
