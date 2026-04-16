# PR: llm-request 패키지 대규모 리팩토링 — 라이브러리 → SDK + Service 분리

## 한줄 요약

`llm-request`를 단일 라이브러리에서 **SDK(클라이언트) + Service(FastAPI 서버)** 구조로 분리하고,
파일 첨부 처리를 자동화하는 file_handler를 구현했습니다.

---

## 목차

1. [변경 배경](#1-변경-배경)
2. [변경 흐름 (시간순)](#2-변경-흐름-시간순)
3. [최종 아키텍처](#3-최종-아키텍처)
4. [주요 변경 상세](#4-주요-변경-상세)
5. [파일 변경 요약](#5-파일-변경-요약)
6. [테스트](#6-테스트)
7. [마이그레이션 영향](#7-마이그레이션-영향)

---

## 1. 변경 배경

### 근본 문제: Files API URI가 캐시를 깬다

ESG Agent는 분석 보고서 PDF 등 파일을 Gemini에 전송할 때 **Gemini Files API**를 사용합니다.
Files API는 파일을 업로드하면 `files/abc123` 형태의 URI를 반환하는데,
**같은 파일이라도 업로드할 때마다 URI가 바뀝니다.**

기존 `llm-request`는 이 URI를 LLM 입력 그대로 캐시 키에 포함시켰기 때문에,
같은 파일로 같은 질문을 해도 URI가 달라진 순간 **항상 cache miss**가 발생했습니다.

```
1차 호출: llm_input = {contents: "분석해줘", file_uri: "files/abc123"} → cache miss → LLM 호출
2차 호출: llm_input = {contents: "분석해줘", file_uri: "files/xyz789"} → cache miss → LLM 호출  ← 같은 파일인데!
```

이 문제를 해결하려면 **"파일의 동일성"을 URI가 아닌 파일 자체의 정보로 판단**해야 했습니다.
파일명 + 수정시각 + 크기로 구성한 checksum을 캐시 키에 넣고,
URI 등 매번 바뀌는 volatile 값은 캐시 키에서 제외하는 방식으로 해결했습니다.

### 파일 처리가 에이전트 책임이었던 문제

기존에는 파일을 Gemini에 전송하는 방식(inline / Files API / context cache)을 **에이전트 개발자가 직접 결정하고 구현**해야 했습니다.

- 20MB 이하 → inline(base64)으로 보낼지
- 20MB 초과 → Files API로 업로드할지
- 자주 쓰는 파일 → context cache를 만들어 토큰 비용을 아낄지
- 50MB 초과 PDF → 어떻게 분할해서 올릴지

이 판단과 처리가 에이전트마다 중복 구현되거나, 아예 구현되지 않은 채로 방치되었습니다.
**파일 처리의 일반화**가 필요했습니다.

### 목표

1. **파일 동일성 = checksum 기반**: 같은 파일이면 URI가 달라도 cache hit
2. **파일 처리 자동화**: inline/Files API/split 분기를 llm-request가 책임지고, 에이전트는 파일 메타정보만 넘기면 됨
3. **Files API URI 재사용**: 한 번 업로드한 파일의 URI를 Redis에 저장해 중복 업로드 방지
4. **서버화**: 에이전트마다 DB에 직접 연결하지 않고, 중앙 Service가 처리

이 과정에서 파일 저장 자체는 llm-request의 범위를 벗어난다는 판단 하에
**file_manager** 서비스를 별도 아이디어로 분리했습니다.
llm-request Service는 파일 바이트를 직접 보관하지 않고, file_manager에 HTTP 요청으로 가져오는 구조입니다.

### 변경 요약

| 문제 | 해결 |
|------|------|
| Files API URI가 캐시 키에 포함 → 항상 miss | checksum 기반 캐시 키, URI는 volatile로 제외 |
| 파일 처리 방식을 에이전트가 직접 구현 | file_handler가 크기/타입별 자동 분기 |
| 같은 파일 매번 재업로드 | Redis에 URI 캐시 (47h), 재사용 |
| PDF 50MB 초과 업로드 불가 | 자동 분할 후 병렬 업로드 |
| 에이전트마다 DB 직접 연결 | Service(port 8101)로 중앙화 |
| 파일 저장 책임 불명확 | file_manager 서비스로 분리 |

---

## 2. 변경 흐름 (시간순)

이 PR의 커밋들은 크게 **5단계**로 나뉩니다.

### Stage 1: 라이브러리 기반 다듬기

| 커밋 | 내용 |
|------|------|
| `8c14231` | `llm_worker` → `llm_request` 패키지명 변경 + 함수명 통일 |
| `89b0793` | 에러 핸들링 개선 — silent failure 방지 |
| `c433606` | MongoDB 문서 키를 `cache_key` → `_id`로 변경 (중복 방지) |
| `fc13f2a` | `meta` → `metadata` 필드명 통일 |
| `68fdc54` | LLM 호출 실패 시 에러 결과도 MongoDB에 저장 |
| `657533d` | error-saving 로직 재검토 후 제거 (불필요 판단) |
| `a99b888` | 캐시 키 생성 시 빈 문자열 처리 통일 |

**왜?** 서버화 전에 기존 라이브러리의 동작을 안정화. 캐시 키 결정성(determinism)을 확보해야
서버화 후에도 같은 입력 → 같은 캐시 키가 보장됨.

### Stage 2: Checksum 기반 파일 캐시 키

| 커밋 | 내용 |
|------|------|
| `d3fb1ee` | 파일 포함 요청의 checksum 저장 로직 설계 (docs) |
| `f07a572` | `checksum.py` 구현 — SHA-256(file_name + mtime + size) |
| `b46efc6` | `fetch_by_cache_key()` 함수 구현 — 캐시 키로 직접 조회 |

**왜?** 파일의 동일성을 판단하는 핵심. `file_uri`나 `cached_content`는 업로드할 때마다 바뀌므로
캐시 키에서 제외하고, 파일 내용 기반의 checksum만 포함시켜야 cache hit 가능.

**핵심 결정**: 파일 전체를 SHA-256 해싱하면 2GB 파일에 ~4초 소요.
→ `(file_name, file_mtime, file_size)` 튜플로 대체. mtime touch 시 false miss 감수.

### Stage 3: File Handler 구현 (라이브러리 단계)

| 커밋 | 내용 |
|------|------|
| `61e5b39` | file_handler 핵심 모듈 구현 (resolver, cache_store, uploader, splitter) |
| `bc53b1a` | validators 추가 (MIME 검증, 2GB 제한) |
| `fd35eb0` | 파일 분류 로직 개선 (partial mixing 전략) |
| `805f2c8` | 버그 수정 |

**왜?** 서버화 전에 **라이브러리로 먼저 동작 검증**. 이 단계에서 파일 처리 로직의 정확성을 확인한 뒤
서버로 옮기는 전략. ("로직 검증 먼저, 구조 변환은 나중에")

**file_handler 핵심 로직:**

```
파일 입력
  ↓
classify_files()  ← 파일 크기/타입별 분류
  ├── inline_pool (≤20MB, 이미지/오디오 포함 시)
  │                (≤50MB, PDF/텍스트만 있을 때)
  └── files_api_pool (나머지)
  ↓
process_inline()  ← 바이트 → base64 Part
  ↓
process_files_api()  ← 3단계 폴백
  ├── A. Redis context_cache HIT → cached_content 반환
  ├── B. Redis files_api HIT → URI 재사용
  └── C. MISS → 업로드 → Redis 저장
        └── PDF >50MB → split_pdf() → 파트별 업로드
  ↓
(opt) D. context_cache 생성 (context_cache_ttl 지정 시)
```

### Stage 4: 서버화 (SDK + Service 분리)

| 커밋 | 내용 |
|------|------|
| `03dd9c0` | **핵심 커밋** — 전체 구조를 `sdk/` + `service/`로 분리 |

**왜?** 에이전트 머신에서 MongoDB/Redis/LLM-handler에 직접 연결하지 않고,
Service가 중앙에서 모든 백엔드 로직을 처리.

**변경 내용:**

```
변경 전 (라이브러리):
  llm_request/
    ├── __init__.py     ← 직접 MDB/Redis/LLM 호출
    ├── runner.py
    ├── keying.py
    └── ...

변경 후 (SDK + Service):
  sdk/llm_request/
    ├── __init__.py     ← HTTP 클라이언트만 (httpx)
    ├── _client.py      ← AsyncClient (base_url → Service)
    ├── models.py       ← RequestResult, DoneCheckResult
    └── exceptions.py   ← SDKError, ServiceError

  service/app/
    ├── main.py         ← FastAPI 앱 (port 8101)
    ├── config.py       ← 환경변수 설정
    ├── core/
    │   ├── providers.py     ← MDB/Redis/LLM 핸들러 초기화
    │   ├── errors.py        ← 에러 계층구조
    │   └── file_manager_client.py  ← file_manager HTTP 클라이언트
    ├── logic/
    │   ├── runner.py        ← prepare → 파일처리 → call → finalize
    │   ├── keying.py        ← 캐시 키 생성
    │   ├── done_check.py    ← MDB 조회
    │   ├── store.py         ← MDB 저장
    │   ├── orchestrator.py  ← 배치 처리
    │   └── file_handler/    ← (Stage 3에서 구현한 로직 이관)
    ├── models/              ← Request/Response DTO
    └── routers/
        └── request.py       ← API 엔드포인트
```

**Service API 엔드포인트:**

| Method | Path | 용도 |
|--------|------|------|
| POST | `/v1/request` | LLM 요청 (비스트리밍) |
| POST | `/v1/request/stream` | LLM 요청 (SSE 스트리밍) |
| POST | `/v1/done-check` | 캐시 조회만 (LLM 호출 X) |
| POST | `/v1/fetch` | cache_key로 직접 조회 |
| GET | `/health` | 헬스체크 |

### Stage 5: File Manager 분리

| 커밋 | 내용 |
|------|------|
| `73a3311` | file_manager 분리 + SDK에서 파일 업로드 로직 제거 |
| `d81b804` | file_manager 완전 분리 |

**왜?** llm-request가 "LLM 캐싱 서비스"와 "파일 관리"를 동시에 담당하면 책임이 과중.
파일 저장/조회는 별도 `file_manager` 패키지로 분리.

**변경 사항:**
- SDK: 파일 바이트를 직접 전송하지 않음. `FileMeta`(메타정보만) 전달
- Service: 파일 바이트가 필요할 때 `file_manager` 서비스에 HTTP 요청으로 가져옴 (lazy fetch)
- `file_manager_client.py` 추가 — file_manager 서비스와의 HTTP 통신 담당

---

## 3. 최종 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│  Agent (에이전트 머신)                                        │
│                                                              │
│  from llm_request import llm_request_async                   │
│  result, meta = await llm_request_async(                     │
│      llm_input, file_metas=[FileMeta(...)]                   │
│  )                                                           │
│                                                              │
│  SDK 역할: HTTP 전송만 (httpx → Service)                      │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP POST /v1/request
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  llm-request Service (FastAPI, port 8101)                    │
│                                                              │
│  ┌─ prepare() ──────────────────────────────────────────┐    │
│  │  1. file_metas → checksum 계산                        │    │
│  │  2. llm_input + checksums → canonicalize → SHA-256    │    │
│  │  3. MDB 캐시 조회 (done_check)                        │    │
│  │     └── HIT → 즉시 반환                               │    │
│  └───────────────────────────────────────────────────────┘    │
│                       │ MISS                                  │
│  ┌─ file_handler ────────────────────────────────────────┐    │
│  │  4. classify_files() → inline_pool / files_api_pool   │    │
│  │  5. inline: file_manager에서 bytes → Part.from_bytes  │    │
│  │  6. files_api: Redis 조회 → HIT면 URI 재사용           │    │
│  │               → MISS면 upload → Redis 저장             │    │
│  │               → PDF>50MB면 split → 파트별 업로드       │    │
│  └───────────────────────────────────────────────────────┘    │
│                       │                                       │
│  ┌─ call ────────────────────────────────────────────────┐    │
│  │  7. llm-handler SDK로 Gemini API 호출                 │    │
│  └───────────────────────────────────────────────────────┘    │
│                       │                                       │
│  ┌─ finalize() ──────────────────────────────────────────┐    │
│  │  8. 결과 + 메타데이터 MDB 저장                         │    │
│  │  9. (result, meta) 반환                               │    │
│  └───────────────────────────────────────────────────────┘    │
│                                                              │
│  외부 연결:                                                   │
│  ├── MongoDB (agent_llm 컬렉션) — LLM 응답 캐시             │
│  ├── Redis — Files API URI 캐시 (47h) + context_cache       │
│  ├── llm-handler (port 8100) — Gemini API 호출              │
│  └── file_manager (port 8102) — 파일 바이트 조회             │
└──────────────────────────────────────────────────────────────┘
```

### 3-Layer 캐시 구조

```
요청 도착
  │
  ├─ Layer 1: MongoDB (agent_llm)
  │   키 = SHA-256(llm_input + file_checksums)
  │   "같은 질문 + 같은 파일 → 같은 응답"
  │   HIT → LLM 호출 없이 즉시 반환
  │
  ├─ Layer 2A: Redis files_api (TTL 47h)
  │   키 = file_cache:files_api:{checksum}
  │   "이 파일 이미 Gemini에 업로드됨?"
  │   HIT → URI 재사용, 재업로드 불필요
  │
  └─ Layer 2B: Redis context_cache (TTL 사용자 지정)
      키 = file_cache:context_cache:{hash}
      "이 파일+모델 조합의 context cache 있음?"
      HIT → cached_content 이름 재사용 (토큰 90% 할인)
```

---

## 4. 주요 변경 상세

### 4.1 캐시 키 결정성 (keying.py)

**문제**: LLM 입력에는 `timestamp`, `request_id`, `file_uri` 등 매번 바뀌는 값이 포함됨.
이 값들이 캐시 키에 들어가면 동일 요청도 매번 cache miss.

**해결**:
```python
# canonicalize_input()이 하는 일:
# 1. 허용 필드만 추출 (contents, model, temperature, file_checksums 등)
# 2. volatile 키 제거 (timestamp, request_id, cached_content, file_uri 등)
# 3. None → "", float → 소수점 4자리, 중첩 정렬
# 4. JSON 직렬화 → SHA-256
```

**핵심 규칙**:
- `file_checksums` → 캐시 키에 **포함** (파일 동일성 핵심)
- `cached_content` → 캐시 키에서 **제외** (업로드마다 달라짐)
- `file_uri` → 캐시 키에서 **제외** (업로드마다 달라짐)

### 4.2 파일 분류 전략 (resolver.py — classify_files)

단순한 "전부 inline" 또는 "전부 Files API"가 아닌, **partial mixing** 전략:

```
파일 목록: [3MB img, 15MB pdf, 25MB pdf, 100MB video]

1. video → 무조건 Files API (inline 불가)
2. 나머지를 크기순 정렬: [3MB img, 15MB pdf, 25MB pdf]
3. 이미지/오디오 포함 → 임계값 20MB
   → 3MB img (누적 3MB, ≤20MB ✓) → inline
   → 15MB pdf (누적 18MB, ≤20MB ✓) → inline
   → 25MB pdf (누적 43MB, >20MB ✗) → Files API

결과: inline=[3MB img, 15MB pdf], files_api=[25MB pdf, 100MB video]
```

**왜 partial mixing?**
- 작은 파일을 Files API로 보내면 업로드 오버헤드만 증가
- 큰 파일을 inline으로 보내면 요청 body가 거대해짐
- 적절히 섞어서 비용과 성능의 균형

### 4.3 PDF 분할 (splitter.py)

Gemini Files API 단일 파일 제한: 50MB.

```
100MB PDF (200페이지)
  ↓
split_pdf(target_mb=50)
  ↓
페이지별 바이트 추정 (이미지 + 텍스트/벡터)
  ↓
그리디 패킹: 청크 ≤50MB까지 페이지 누적
  ↓
[청크1: 1-95페이지, 48MB] [청크2: 96-200페이지, 52MB]
  ↓
52MB 청크 → 재귀 분할 (최대 depth 3)
  ↓
후처리: 인접한 작은 청크 병합 (≤45MB)
  ↓
최종: [part1.pdf, part2.pdf, part3.pdf]
  ↓
각각 Files API 업로드 (병렬)
```

### 4.4 SDK 설계 (_client.py, __init__.py)

SDK는 의도적으로 **최소한의 역할**만 수행:

```python
# SDK가 하는 것:
# - llm_input과 file_metas를 JSON으로 Service에 POST
# - 응답 파싱 (result, meta 튜플)
# - 에러 변환 (HTTP 4xx/5xx → SDKError/ServiceError)

# SDK가 하지 않는 것:
# - MongoDB 직접 접근 ✗
# - Redis 직접 접근 ✗
# - LLM 호출 ✗
# - 파일 바이트 처리 ✗
```

**에러 계층**:
```
SDKError (base)
├── ServiceConnectionError  ← Service 연결 실패/타임아웃
└── ServiceError            ← Service가 4xx/5xx 반환
    └── error_type: "validation" | "cache" | "file" | "handler"
```

### 4.5 Service 에러 처리 (core/errors.py)

```
LLMRequestServiceError (base, 500)
├── CacheError (502)              ← Redis/MDB 장애
├── FileUploadError (502)         ← Gemini 업로드 실패
├── ValidationError (422)         ← 입력 검증 실패
├── UnsupportedFileTypeError (422) ← 지원하지 않는 MIME
└── FileTooLargeError (422)       ← 2GB 초과
```

**fail-fast 원칙**: MIME 타입과 파일 크기는 `prepare()` 진입 전에 검증.
비싼 MDB 조회나 LLM 호출 전에 빨리 거부.

### 4.6 Gemini 404 자동 복구

```
LLM 호출 → Gemini 404 (파일 만료/삭제)
  ↓
resolve_files_after_404()
  ↓
1. Redis files_api:{checksum} 삭제
2. Redis context_cache:{hash} 삭제
3. resolve_files() 재실행 → 재업로드
4. LLM 재호출 (1회만)
5. 또 404 → 에러 raise
```

---

## 5. 파일 변경 요약

### 새로 추가된 파일

| 경로 | 역할 |
|------|------|
| `sdk/llm_request/__init__.py` | SDK 공개 API (llm_request_async, done_check 등) |
| `sdk/llm_request/_client.py` | httpx AsyncClient 래퍼 |
| `sdk/llm_request/_file_meta.py` | FileMeta 모델 (임시 stub) |
| `sdk/llm_request/aio.py` | llm_handler.aio 재수출 |
| `sdk/llm_request/models.py` | SDK 타입 정의 |
| `sdk/llm_request/exceptions.py` | SDK 예외 클래스 |
| `sdk/pyproject.toml` | SDK 패키지 설정 |
| `service/app/main.py` | FastAPI 앱 (lifespan, 미들웨어, 에러 핸들러) |
| `service/app/config.py` | pydantic-settings 환경변수 |
| `service/app/core/providers.py` | MDB/Redis/LLM 핸들러 초기화 |
| `service/app/core/errors.py` | Service 에러 계층 |
| `service/app/core/file_manager_client.py` | file_manager HTTP 클라이언트 |
| `service/app/logic/runner.py` | 핵심 파이프라인 (prepare→call→finalize) |
| `service/app/logic/keying.py` | 캐시 키 생성 |
| `service/app/logic/done_check.py` | MDB 캐시 조회 |
| `service/app/logic/store.py` | MDB 결과 저장 |
| `service/app/logic/orchestrator.py` | 배치 처리 (run_many) |
| `service/app/logic/types.py` | FileChecksum 타입 |
| `service/app/logic/file_handler/resolver.py` | 파일 크기별 분기 로직 |
| `service/app/logic/file_handler/cache_store.py` | Redis 파일 캐시 CRUD |
| `service/app/logic/file_handler/uploader.py` | Files API 업로드 + context cache |
| `service/app/logic/file_handler/splitter.py` | PDF 분할 |
| `service/app/logic/file_handler/validators.py` | MIME/크기 검증 |
| `service/app/models/requests.py` | Request DTO |
| `service/app/models/responses.py` | Response DTO |
| `service/app/models/file_meta.py` | FileMeta 서버 모델 |
| `service/app/routers/request.py` | API 엔드포인트 정의 |
| `service/Dockerfile` | Service 컨테이너 |
| `service/pyproject.toml` | Service 패키지 설정 |

### 이동된 파일 (라이브러리 → Service)

| 원래 위치 | 새 위치 | 비고 |
|-----------|---------|------|
| `llm_request/runner.py` | `service/app/logic/runner.py` | 파일 처리 로직 추가 |
| `llm_request/keying.py` | `service/app/logic/keying.py` | file_checksums 지원 추가 |
| `llm_request/done_check.py` | `service/app/logic/done_check.py` | 변경 미미 |
| `llm_request/store.py` | `service/app/logic/store.py` | file_meta 저장 추가 |
| `llm_request/meta.py` | `service/app/logic/meta.py` | 변경 없음 |
| `llm_request/orchestrator.py` | `service/app/logic/orchestrator.py` | 서버 컨텍스트 적응 |
| `tests/conftest.py` | `service/tests/conftest.py` | fixture 업데이트 |
| `tests/test_*.py` | `service/tests/logic/test_*.py` | import 경로 변경 |

### 이동된 문서

| 원래 위치 | 새 위치 |
|-----------|---------|
| `plans/DESIGN.md` | `docs/DESIGN.md` |
| `plans/error-saving.md` | `docs/error-saving.md` |
| `plans/fetch-by-cache-key.md` | `docs/fetch-by-cache-key.md` |
| `plans/checksum-cache-key.md` | `docs/file-handler-phase1.md` |

### 삭제된 파일

| 파일 | 이유 |
|------|------|
| `llm_request/__init__.py` | SDK로 대체 (기존 라이브러리 진입점) |
| `quickstart.ipynb` | `quickstarts/` 폴더로 재구성 |
| `docs/single-flight-dedup.md` | in-memory 방식 → Redis standby 방식으로 전략 변경, `cache-stampede-dedup.md`로 대체 |

---

## 6. 테스트

### SDK 테스트 (`sdk/tests/`)

```bash
pytest sdk/tests/ -v
```

- checksum 계산 정확성
- HTTP 클라이언트 모킹

### Service 테스트 (`service/tests/`)

```bash
pytest service/tests/ -v
```

| 테스트 파일 | 검증 내용 |
|------------|----------|
| `test_keying.py` | 캐시 키 결정성, volatile 필드 제외, file_checksums 포함 |
| `test_done_check.py` | MDB 캐시 조회 로직 |
| `test_store.py` | MDB 저장 로직 |
| `test_meta.py` | 메타데이터 구성 |
| `test_cache_store.py` | Redis 파일 캐시 CRUD |
| `test_splitter.py` | PDF 분할 로직 |
| `test_uploader.py` | Files API 업로드 + context cache |
| `test_orchestrator.py` | 배치 처리 |
| `test_router_request.py` | API 엔드포인트 통합 테스트 |

---

## 7. 마이그레이션 영향

### 기존 사용자 코드 변경 필요

```python
# Before (라이브러리 직접 호출)
from llm_request import llm_request_async
result, meta = await llm_request_async(llm_input)

# After (SDK → Service 경유)
from llm_request import llm_request_async
result, meta = await llm_request_async(llm_input, file_metas=[...])
# 내부적으로 SDK가 Service(port 8101)에 HTTP 요청
```

### 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_REQUEST_URL` | `http://localhost:8101` | SDK → Service 연결 |
| `MDB_HANDLER_URL` | `http://localhost:8300` | Service → MongoDB |
| `LLM_HANDLER_URL` | `http://localhost:8100` | Service → Gemini |
| `REDIS_URL` | `redis://localhost:6379/0` | Service → Redis |
| `FILE_MANAGER_URL` | `http://localhost:8102` | Service → file_manager |

### Docker

Service는 `service/Dockerfile`로 컨테이너화 가능. port 8101 expose.

### Redis 필수 의존성

**Redis가 없으면 Service가 시작되지 않습니다.** Redis는 선택적 최적화가 아니라,
Files API URI 캐시(47h)와 context_cache 재사용에 필수적인 인프라입니다.
startup 시 Redis 연결을 확인하며, 연결 실패 시 에러를 raise하고 서비스가 종료됩니다.

```
REDIS_URL 미설정 또는 연결 불가 → Service startup 실패
```

---

## 8. 이 PR에 포함되지 않은 작업 (다음 단계)

### Cache Stampede Dedup (설계 완료, 미구현)

동시 요청 N개가 동일한 cache miss를 만났을 때 N번 중복으로 업로드/LLM 호출이 발생하는 문제.

```
t=0ms   req A, B, C 동시 도착
t=1ms   각자 done_check → MDB miss
t=2ms   각자 Redis files_api 조회 → miss
t=3ms   각자 upload_file() → Gemini 업로드 3회  ⚠️
t=11s   각자 LLM 호출 3회 → 토큰 비용 3배  ⚠️
```

**설계 방향**: Redis SETNX로 "처리 중" standby 레코드를 먼저 선점.
후속 요청은 exponential backoff(0.5→1→2→4→8s, cap 10s)로 대기 후 결과 재사용.
3개 캐시 계층(files_api, context_cache, agent_llm) 모두 적용 예정.

상세 설계: [cache-stampede-dedup.md](cache-stampede-dedup.md)
구현 예정 모듈: `service/app/logic/dedup.py`

### Phase B: Files API 용량 관리 (20GB 한도)

Gemini Files API 계정당 20GB 저장 한도 초과 시 자동 삭제 로직.
`llm-handler`에 `delete_file()` SDK가 노출되어야 구현 가능 → 의존성 해결 후 진행 예정.

---

## 관련 설계 문서

이 PR의 변경은 아래 설계 문서를 기반으로 진행되었습니다:

| 문서 | 내용 |
|------|------|
| [DESIGN.md](DESIGN.md) | 전체 설계 철학 |
| [file-handler-phase1.md](file-handler-phase1.md) | checksum 기반 캐시 키 설계 |
| [file-handler-phase2.md](file-handler-phase2.md) | file_handler 자동화 설계 |
| [file-handler-phase3.md](file-handler-phase3.md) | 서버화 설계 |
| [file-handler-phase3-implementation.md](file-handler-phase3-implementation.md) | 서버화 구현 상세 |
| [file-manager-separation.md](file-manager-separation.md) | file_manager 분리 설계 |
| [inline-vs-files-api-strategy.md](inline-vs-files-api-strategy.md) | inline/Files API 분기 전략 |
| [cache-stampede-dedup.md](cache-stampede-dedup.md) | 동시 요청 중복 방지 설계 |
| [concepts.md](concepts.md) | 개념 정리 (학습 기록) |
