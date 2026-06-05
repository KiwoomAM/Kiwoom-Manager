# Redis Manager

메모리 폴백 + 재시도 내장 Redis 클라이언트 패키지(`redis_manager`). 외부 에이전트가
GitHub에서 import해 쓰는 것을 전제로 한다. Redis 서버가 불가할 때 인메모리 백엔드로
투명하게 폴백하고, 일시 장애는 exponential backoff + jitter로 자동 복구한다.

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
- **backends/** — `KeyValueBackend` Protocol 구현. `RedisBackend`(redis-py + retry), `MemoryBackend`(dict + Lock)
- **base_store.py** — `BaseJsonStore`. 도메인 store의 베이스 (JSON 직렬화, 키 프리픽스)

## Conventions

- **신규 코드는 RedisHandler API만 사용.** `get_client()` 직접 호출 금지 (encapsulation 위반)
- **분산 의미론 필요한 명령(NX/XX)은 `setnx()` 등 Handler 메서드 사용.** Redis 미연결 시 `RedisUnavailableError` 발생 — 메모리 폴백 없음
- **일반 명령(get/set 등)은 폴백 자동.** 호출자가 try/except로 메모리 백업 구현 금지
- **새 SET 옵션 추가 시** `KeyValueBackend` Protocol → `RedisBackend` → `MemoryBackend` 3곳 모두 갱신 (OCP 보장)
- **재시도 정책은 RedisBackend 메서드의 `@retryable` 데코레이터로 적용.** Handler에서 try/except는 폴백 분기 전용
- 환경변수는 `os.getenv`에서 직접 읽는다. `.env` 자동 로드는 호출 애플리케이션 책임 (`RedisConfig.configure(url=...)`로 코드 설정도 가능).

## Gotchas

- `RedisHandler()`는 Singleton — 테스트에서 `rh.reset()`으로 초기화
- `import redis_manager`는 연결을 시도하지 않는다 — 첫 `get_client()` 호출 시 lazy로 연결한다 (import 블로킹 방지)
- 재시도 최악 지연 ~16초 (`socket_timeout=5s × 3 + backoff`). SLA 민감 경로는 `REDIS_RETRY_MAX_ATTEMPTS=1`
- 메모리 폴백 데이터는 프로세스 종료 시 휘발 — 영속성 필요 시 Redis 가용성 모니터링
- gunicorn 4 workers는 각자 독립 Singleton — 의도된 동작
- NX/XX는 `MemoryBackend`도 simulate하지만 단일 프로세스 한정 — Handler에서 폴백 차단됨
- **REDIS_URL은 고정 기본값**: 미설정 시 `redis://192.7.9.45:7617/0`. 사용자는 보통 설정하지 않는다. SERVICE_NAME 같은 caller 식별은 쓰지 않으므로 일반 사용에서 설정할 env가 없다.

## Environment Variables

| 변수 | 기본값 | 의미 |
|---|---|---|
| `REDIS_URL` | `redis://192.7.9.45:7617/0` | 연결 URL (고정 기본값, 보통 설정 불필요) |
| `REDIS_SOCKET_TIMEOUT` | 5 | 소켓 타임아웃(초) |
| `REDIS_RETRY_MAX_ATTEMPTS` | 3 | 첫 시도 포함 |
| `REDIS_RETRY_INITIAL_BACKOFF` | 0.1 | 초 |
| `REDIS_RETRY_MAX_BACKOFF` | 1.0 | 초, 상한 |
| `REDIS_RETRY_MULTIPLIER` | 2.0 | exp 배율 |
| `REDIS_RETRY_JITTER_RATIO` | 0.2 | ±20% (0=비활성) |
