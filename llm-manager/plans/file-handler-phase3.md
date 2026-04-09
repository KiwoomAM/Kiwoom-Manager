# File Handler Phase 3: llm-request 서버화

> Phase 2(라이브러리 구현) 완료 후 진행.

## 목표

llm-request를 독립 서버로 분리 (SDK + Service). llm-handler와의 합체는 Phase 4에서.

## 왜 서버화하는가

1. **의존성 정리**: client(agent)가 llm-handler, mdb-handler, redis-handler를 전부 직접 갖고 있는 게 지저분
2. **구조적 복잡도**: 캐시 로직, 파일 핸들링, LLM 호출이 클라이언트에 다 몰려있음
3. **rate limiting**: 나중에 agent별 call 개수 조절이 필요 → 중앙 서버가 필수

## 핵심 설계 결정

### 파일은 서버로 보내지 않는다 (Redis 파일 캐시 miss 시에만)

- checksum 계산은 client SDK에서 수행 (파일이 로컬에 있으니까)
- 서버에는 checksum만 전달
- 서버가 MDB 캐시(LLM 응답 캐시) 조회 → HIT이면 파일 불필요, 즉시 반환
- MDB 캐시 MISS일 때만 client가 파일을 서버로 업로드 (lazy upload)

### SDK에서 Redis 파일 캐시 조회

- SDK가 Redis 파일 캐시를 조회하여 파일이 이미 Files API에 업로드되었는지 확인
- MDB 캐시(LLM 응답) miss여도 Redis 파일 캐시에 파일 URI가 있으면 → 파일 전송 불필요
- Redis 파일 캐시 miss일 때만 파일을 서버로 전송

### 단계적 서버화

1. **Phase 3 (이번)**: llm-request를 독립 SDK + Service로 분리
2. **Phase 4 (나중)**: 이미 서버화된 llm-handler와 합침

## 아키텍처

```
Client SDK (agent 머신)                    llm-request Service (별도 서버)
┌───────────────────────┐                 ┌──────────────────────────────┐
│ file_paths → checksum │                  │                              │
│       ↕               │  1. checksum     │                              │
│ Redis 파일 캐시 조회    │ ─────────────→  │ MDB 캐시 조회 (LLM 응답)      │
│ - 파일 URI 있음        │  2a. MDB HIT    │   ├─ HIT → 결과 반환          │
│   → 파일 안 보냄       │ ←────────────── │   └─ MISS                    │
│ - 파일 URI 없음        │  2b. MDB MISS   │                              │
│   → 파일 전송          │ ─────────────→  │ resolve_files → LLM 호출     │
│                       │  3. 결과         │ → MDB 캐시 저장 (LLM 응답)    │
│                       │ ←────────────── │ → Redis 파일 캐시 저장         │
└───────────────────────┘                 └──────────────────────────────┘
```

## SDK 역할 (agent 머신에서 실행)

- `file_checksum()` 계산 (file_paths → SHA-256)
- Redis 파일 캐시 조회 (파일이 이미 Files API에 업로드되었는지 확인)
- 서버에 HTTP 요청 (checksum + llm_input)
- MDB 캐시 miss + Redis 파일 캐시 miss 시 파일 업로드
- 응답을 Python 객체로 변환

## Service 역할 (별도 서버)

- 캐시 키 생성 + MDB 캐시 조회 (LLM 응답 캐시, done_check)
- 파일 처리 (resolve_files: inline/Files API/split)
- Redis 파일 캐시 저장 (Files API URI)
- LLM 호출 (llm-handler SDK → Gemini)
- MDB 캐시 저장 (LLM 응답)

## 엔드포인트

| 엔드포인트 | 역할 | SDK 함수 | 우선순위 |
|-----------|------|---------|---------|
| `POST /v1/request` | LLM 요청 (캐시 조회 → 호출 → 저장) | `llm_request_async()` | 핵심 |
| `POST /v1/files` | 파일 업로드 (Redis 파일 캐시 miss 시) | SDK 내부 호출 | 핵심 |
| `POST /v1/done-check` | MDB 캐시 존재 여부만 확인 | `done_check()` | 부가 |
| `POST /v1/fetch` | 캐시 키로 결과 직접 조회 | `fetch_by_cache_key()` | 부가 |

### 파일 업로드 프로토콜 (엔드포인트 분리 방식)

파일 업로드(`/v1/files`)와 LLM 요청(`/v1/request`)을 분리한다.

```
SDK 내부 흐름 (llm_request_async):

1. file_paths → checksum 계산 (로컬)
2. Redis 파일 캐시 조회 (로컬)
   - miss인 파일 있으면 → POST /v1/files (파일 업로드)
3. POST /v1/request (checksum + llm_input)
   - 서버: MDB 캐시 조회 → HIT이면 즉시 반환
   - 서버: MISS → resolve_files → LLM 호출 → 저장 → 반환
```

분리 이유:
- 기존 llm-handler도 `/v1/generate`와 `/v1/files`를 분리해서 사용
- 파일 업로드와 LLM 요청이 각각 한 가지 일만 담당
- 나중에 파일만 미리 올려놓는 것도 가능

### SDK public API

```python
# 사용자 코드 — 기존과 동일
from llm_request import llm_request_async, done_check, fetch_by_cache_key

# 핵심: LLM 요청 (내부에서 checksum + Redis 조회 + HTTP 호출)
response, meta = await llm_request_async(
    {"contents": "분석해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
    cache_mode=True,
    save_mode=True,
)

# 부가: 캐시 존재 확인만
result = await done_check(llm_input, file_checksums=checksums)

# 부가: 캐시 키로 직접 조회
response, meta = await fetch_by_cache_key(cache_key)
```

## 디렉토리 구조

```
packages/llm-request/
├── sdk/
│   ├── pyproject.toml
│   └── llm_request/
│       ├── __init__.py          # public API (llm_request_async 등)
│       ├── _client.py           # httpx 클라이언트
│       ├── checksum.py          # file_checksum() (Phase 2에서 이동)
│       └── models.py            # 요청/응답 타입
├── service/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── app/
│       ├── main.py              # FastAPI 앱
│       ├── config.py            # 환경변수 (MDB_URL, REDIS_URL, LLM_HANDLER_URL, port=8101 등)
│       ├── core/
│       │   ├── errors.py        # 에러 계층
│       │   └── providers.py     # MDB/Redis 연결 싱글톤
│       ├── models/
│       │   ├── requests.py      # 요청 DTO
│       │   └── responses.py     # 응답 DTO
│       ├── routers/
│       │   └── request.py       # /v1/request, /v1/done-check 등
│       └── logic/               # Phase 2 핵심 로직 이동
│           ├── runner.py
│           ├── keying.py
│           ├── done_check.py
│           ├── store.py
│           ├── meta.py
│           ├── orchestrator.py
│           └── file_handler/
│               ├── resolver.py
│               ├── cache_store.py
│               ├── uploader.py
│               └── splitter.py
├── plans/
└── tests/
```

## 선행 조건

- Phase 2 구현 완료 + 검증
- 배포 인프라 준비 (포트 할당 등)

## TODO

- [x] 엔드포인트 정의 (4개, `/v1/request` 중심)
- [x] 파일 업로드 프로토콜 (엔드포인트 분리 방식)
- [x] SDK public API 인터페이스
- [ ] 스트리밍 전달 방식 결정 (SSE 등, 후순위)
- [ ] Service 구현 (Phase 2 로직 이동 + FastAPI 래핑)
- [ ] SDK 구현 (checksum + Redis 조회 + httpx 클라이언트)
- [ ] 테스트
- [ ] 기존 agent 코드 마이그레이션 (import 변경)
- [ ] 배포
