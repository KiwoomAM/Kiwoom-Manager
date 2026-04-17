# File Handler 설계 (Phase 2): 파일 업로드/캐싱 자동화

> 이 문서는 **Phase 2 (이번 작업)** 범위만 다룬다. Phase 3/4는 [단계별 로드맵](#단계별-로드맵)에서 간략히 기술.

## 배경

### Phase 1 (완료)

[file-checksum-cache.md](file-checksum-cache.md)에서 구현 완료:
- `file_paths` → checksum 계산 → 캐시 키에 포함
- `cached_content`, `file_uri` 등 volatile 값은 캐시 키에서 제외
- agent_llm 컬렉션에서 checksum 기반 done_check 동작

### 문제

1. **파일 업로드/캐싱이 사용자(에이전트 개발자) 책임**
   - `upload_file`, `create_cache`를 직접 호출해야 함
   - 같은 파일이라도 매번 새 URI/cache name 생성 → 비용 낭비

2. **동일 파일 반복 업로드**
   - 같은 PDF로 질문만 바꿔서 N번 호출 시, N번 업로드 발생
   - `create_cache`도 매번 호출 → 토큰/시간 낭비

### Phase 2 목표

- 사용자는 `file_paths`만 전달하면, llm-request가 **자동으로** 최적 방식(inline / Files API) 결정
- 동일 파일은 **Redis(file_cache)** 에서 기존 Files API URI / cached_content name 재사용
- 사용자는 더 이상 `upload_file`, `create_cache`를 직접 호출할 필요 없음

---

## 파일 처리 방식

| 방식 | 설명 | Phase 2에서 |
|------|------|------------|
| Inline (base64) | 요청 body에 base64 포함 | **사용** (≤20MB) |
| Files API | 서버에 파일 업로드, URI로 참조 (무료) | **사용** (>20MB) |
| Context Caching | 파일을 캐싱하여 토큰 90% 할인 | **opt-in** (`context_cache_ttl` 명시 시) |

### 파일 크기 분기

| 조건 | 처리 방식 | Redis 저장 |
|------|----------|-----------|
| ≤20MB | inline (base64 변환) | X |
| >20MB | Files API 업로드 | O (URI 캐싱, TTL 48h) |
| PDF >50MB | split → Files API 업로드 (파트별 50MB 이하) | O (파트별 URI 캐싱) |

- 파일 크기는 `FileChecksum.size`를 활용 (checksum 계산 시 이미 구함)
- PDF >50MB split은 Gemini Files API의 PDF 50MB 제한 때문

### Context Caching (opt-in)

- **기본**: context caching 안 함 (Files API/inline만)
- **opt-in**: `context_cache_ttl` 명시 시 create_cache 실행 + Redis에 저장
- **자동 재사용**: Redis에 기존 context cache가 있으면 `context_cache_ttl` 유무와 관계없이 재사용
- 비용: 토큰 90% 할인, 저장 비용 $1/1M토큰/시간 (Flash 기준)
- **같은 파일로 시간당 4회 이상 호출할 때만 권장** (손익분기점)

### Split PDF

PDF >50MB를 split하면 여러 파트가 생긴다. contents에 순서대로 나열하면 Gemini가 전체 내용을 처리한다. 파트 간 연결 관계를 명시할 프롬프트나 메타 정보 불필요.

split으로 생성된 **로컬 임시 파일**은 Files API 업로드 완료 후 즉시 삭제 (try/finally). Files API에 업로드된 데이터는 Google 서버에 48시간 유지.

---

## file_cache 저장소 (Redis)

redis-handler SDK의 `BaseJsonStore` 패턴을 활용.

### 키 설계

```
// Files API 레코드 (파일 >20MB)
key:   "file_cache:files_api:{file_checksum}"
value: JSON { file_checksum, file_name, file_size, mime_type, uri }
TTL:   48시간 (172800초)

// Context Cache 레코드 (context_cache_ttl opt-in 시 생성)
key:   "file_cache:context_cache:{hash_key}"
value: JSON { file_checksums, model, system_instruction, cached_content_name, files_api_uris }
TTL:   사용자 설정 context_cache_ttl (기본 3600초)
```

### hash_key 생성

keying.py의 canonicalize + sha256 방식과 동일:
```python
hash_key = sha256(canonicalize({
    "file_checksums": sorted(file_checksums),
    "model": model,
    "system_instruction": system_instruction or ""
}))
```

| create_cache 입력 필드 | hash_key 포함 | 이유 |
|----------------------|-------------|------|
| `model` | O | 모델 다르면 다른 캐시 |
| `contents` | O (file_checksums로 대체) | URI는 매번 변하므로 checksum 사용 |
| `system_instruction` | O | 시스템 프롬프트 다르면 다른 캐시 |
| `display_name` | X | 라벨일 뿐 |
| `ttl` | X | 수명 설정이지 내용 아님 |

### TTL 정책

| 대상 | TTL | 관리 방식 |
|------|-----|----------|
| Files API URI | 48시간 (Google 고정) | Redis native TTL |
| Context Cache | 사용자 설정 (기본 1시간) | Redis native TTL |

### 404 Fallback

Redis hit으로 URI/cached_content를 재사용했는데 Gemini에서 404 반환 시:
1. Redis 해당 키 삭제
2. 재업로드/재캐싱 진행
3. Redis에 새 레코드 저장

---

## 전체 파이프라인

```
사용자: llm_request_async(llm_input, file_paths=[...], context_cache_ttl=선택)
    ↓
① checksum 계산 (size, mime_type 포함)
    ↓
② agent_llm 조회 (done_check)
   - 캐시 키: hash(llm_input + file_checksums)
   - URI, cached_content는 캐시 키에서 제외
   ├─ HIT → LLMResponse 반환 (끝)
   └─ MISS ↓
③ Redis에서 기존 context cache 조회 (항상)
   ├─ HIT → cached_content_name 재사용 → ⑤로
   └─ MISS ↓
④ 각 파일별 독립 처리 (크기 분기)
   ├─ ≤20MB → inline (base64 Part 생성)
   │
   ├─ >20MB → Redis 조회 (file_checksum)
   │   ├─ HIT → 기존 URI 사용
   │   └─ MISS → Files API 업로드 → Redis 저장
   │
   └─ PDF >50MB → split → 각 파트를 Files API 업로드 → Redis 저장
                  (임시 파일은 try/finally로 삭제 보장)
    ↓
④-b. context_cache_ttl이 있으면 → create_cache → Redis에 context cache 저장
    ↓
⑤ llm_input 조립
   - context cache 있으면: llm_input["cached_content"] = cached_content_name
   - 없으면: contents = [Part(file), ..., "질문 텍스트"]
    ↓
⑥ llm_handler.call() 실행
    ↓
⑦ agent_llm에 결과 저장 (기존 save 로직)
```

---

## file_handler 모듈

```
llm_request/file_handler/
├── __init__.py        # public API: resolve_files
├── resolver.py        # 파일 처리 진입점 (크기 분기, 파이프라인 조율)
├── cache_store.py     # Redis CRUD (redis-handler SDK의 BaseJsonStore 활용)
├── uploader.py        # Files API 업로드 + create_cache (llm-handler SDK 사용)
└── splitter.py        # PDF 분할 (ESG 코드 복제 + 수정)
```

### resolver.py

```python
@dataclass
class ResolvedFiles:
    contents_parts: list[Part]          # inline 또는 file_uri Part들
    cached_content: str | None          # context cache name (있는 경우)


async def resolve_files(
    file_metas: list[FileChecksum],
    file_paths: list[str],
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    context_cache_ttl: int | None = None,       # None이면 context caching 안 함
) -> ResolvedFiles:
```

**내부 흐름**:
```
① Redis context cache 조회 (hash_key = sha256(checksums + model + system))
   ├─ HIT → cached_content=name, contents_parts=[] 반환
   └─ MISS ↓

② 각 파일별 독립 처리:
   for each file_meta, file_path:
       if size <= 20MB:
           → Part.from_file(file_path) (inline base64)
       elif mime_type == "application/pdf" and size > 50MB:
           → split → Files API 업로드 → URI Part들
           (임시 파일은 try/finally로 삭제 보장)
       else:
           → Redis file_cache 조회 (file_checksum)
             ├─ HIT → 기존 URI Part
             └─ MISS → Files API 업로드 → Redis 저장

③ context_cache_ttl이 있으면:
   → create_cache 시도
     ├─ 성공 → cached_content=name, Redis 저장, contents_parts=[]
     └─ 실패 → contents_parts 유지, cached_content=None

→ ResolvedFiles 반환
```

### cache_store.py

```python
# BaseJsonStore 상속, prefix="file_cache"
def find_files_api(file_checksum: str) -> dict | None
def find_context_cache(hash_key: str) -> dict | None
def save_files_api(file_checksum: str, doc: dict, ttl: int = 172800) -> None
def save_context_cache(hash_key: str, doc: dict, ttl: int = 3600) -> None
def delete_key(key: str) -> None  # 404 fallback용
```

### uploader.py

```python
# upload_file() 래퍼 제거 — llm_handler.aio.upload_file 직접 사용
async def create_context_cache(
    file_refs: list[FileRef], *, model: str,
    system_instruction: str | None = None, ttl: int = 3600,
) -> CacheRef
# create_cache 재시도: 2회, 5초 간격 (500, 503, 429, RESOURCE_EXHAUSTED)
```

### splitter.py

```python
def split_pdf(path: str, target_mb: int = 50) -> list[str]
# ESG의 split_by_size() 복제 + llm-request에 맞게 수정
# 페이지별 바이트 추정 → 스케일링 → 누적 그룹핑 → 초과 시 재귀 재분할
```

---

## runner.py 변경

### 시그니처

```python
async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,       # 신규: context cache TTL (None=안 함)
) -> tuple[LLMResponse, dict]:
```

4개 함수 모두 동일: `llm_request`, `llm_request_async`, `llm_request_stream`, `llm_request_stream_async`

### 흐름 변경

```
기존:  file_paths → checksum → keying_input → done_check → (hit or miss) → call
변경:  file_paths → checksum → keying_input → done_check
       ├─ HIT → 반환
       └─ MISS → resolve_files() → llm_input 조립 → call → save
```

---

## keying.py (Phase 1에서 완료, 검증만)

| 항목 | 상태 |
|------|------|
| `cached_content` 캐시 키에서 제외 | 이미 구현됨 (line 78-81) |
| FileRef/Part URI 정규화 | 이미 구현됨 (line 156-160) |
| `file_checksums` 캐시 키에 포함 | 이미 구현됨 (line 39) |

**캐시 키 동작**: 같은 파일(checksum) + 같은 질문 + 같은 설정 → cache hit. URI/cached_content가 달라도 무관.

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| Files API 업로드 실패 | llm-handler 측 재시도에 위임 |
| create_cache 실패 | 재시도 2회, 5초 간격 |
| Gemini 404 (만료된 URI/cache) | Redis 키 삭제 → 재업로드/재캐싱 → Redis 갱신 |
| LLM 호출 실패 | 기존 runner.py 로직 유지 |
| PDF split 실패 | PyMuPDF 에러 전파, 임시 파일 try/finally로 삭제 보장 |
| file_paths에 파일 없음 | FileNotFoundError (기존 동작) |

---

## 의존성

| 패키지 | 용도 | 신규/기존 |
|--------|------|----------|
| `PyMuPDF (fitz)` | PDF 분할 | 신규 |
| `kiwoom-redis-handler` | file_cache Redis 저장소 | 신규 |
| `kiwoom-llm-handler` | upload_file, create_cache, call, stream | 기존 |
| `kiwoom-mdb-handler` | agent_llm 컬렉션 (LLM 응답 캐시) | 기존 |
| `filetype` | MIME 감지 | 기존 |

---

## 디렉토리 구조

```
packages/llm-request/
├── pyproject.toml                    # PyMuPDF, kiwoom-redis-handler 추가
├── llm_request/
│   ├── __init__.py                   # file_handler export 추가
│   ├── checksum.py                   # 기존 유지
│   ├── done_check.py                 # 기존 유지
│   ├── keying.py                     # 기존 유지 (Phase 1에서 완료)
│   ├── meta.py                       # 기존 유지
│   ├── orchestrator.py               # 기존 보관 (수정 안 함)
│   ├── runner.py                     # 수정: resolve_files 호출, context_cache_ttl
│   ├── store.py                      # 기존 유지
│   └── file_handler/                 # ★ 신규
│       ├── __init__.py
│       ├── resolver.py
│       ├── cache_store.py
│       ├── uploader.py
│       └── splitter.py
├── tests/
│   ├── ... (기존 유지)
│   ├── test_resolver.py              # ★ 신규
│   ├── test_cache_store.py           # ★ 신규
│   ├── test_uploader.py              # ★ 신규
│   └── test_splitter.py              # ★ 신규
└── plans/
```

---

## 사용 예시

### 기본 — 파일만 전달하면 자동 처리

```python
result, meta = await llm_request_async(
    {"contents": "이 PDF 요약해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)
```

### 같은 파일로 여러 번 호출 — context caching opt-in

```python
# 첫 호출: context_cache_ttl 명시 → context cache 생성
result1, _ = await llm_request_async(
    {"contents": "요약해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
    context_cache_ttl=1800,
)

# 이후: context_cache_ttl 없어도 Redis에서 기존 캐시 자동 재사용 (90% 할인)
result2, _ = await llm_request_async(
    {"contents": f"{result1.text}를 기반으로 분석해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)
```

### 대용량 PDF — 자동 split

```python
# 100MB PDF → 자동 split → Files API → URI Parts
result, meta = await llm_request_async(
    {"contents": "ESG 점수 평가해줘", "model": "gemini-2.5-flash",
     "system": "당신은 ESG 평가 전문가입니다."},
    file_paths=["large_report.pdf"],
)
```

### 혼합 파일 — 각 파일별 최적 경로

```python
# 5MB 이미지(inline) + 30MB PDF(Files API) → 자동 혼합 처리
result, meta = await llm_request_async(
    {"contents": "이 이미지와 PDF를 비교 분석해줘", "model": "gemini-2.5-flash"},
    file_paths=["chart.png", "report.pdf"],
)
```

### 텍스트만 — 기존과 동일

```python
result, meta = await llm_request_async({"contents": "오늘 날씨 어때?"})
```

---

## 구현 TODO

### 1. file_handler 모듈

- [ ] `file_handler/__init__.py` — resolve_files export
- [ ] `file_handler/resolver.py`
  - [ ] ResolvedFiles dataclass
  - [ ] resolve_files() — 크기 분기, Redis 조회, 업로드, context cache
  - [ ] contents 병합 순서: [Part(file), ..., "질문 텍스트"]
- [ ] `file_handler/cache_store.py` — BaseJsonStore 상속, Redis CRUD
- [ ] `file_handler/uploader.py` — Files API 업로드, create_cache, 재시도, 임시 파일 삭제
- [ ] `file_handler/splitter.py` — ESG split_by_size 복제, 재귀 재분할

### 2. runner.py

- [ ] 4개 함수에 `context_cache_ttl` 파라미터 추가
- [ ] done_check miss 후 resolve_files() 호출 + llm_input 조립
- [ ] 404 fallback 처리

### 3. keying.py 검증

- [ ] cached_content 제외 동작 확인
- [ ] FileRef/Part URI 정규화 확인

### 4. 의존성 및 export

- [ ] pyproject.toml에 PyMuPDF, kiwoom-redis-handler 추가
- [ ] `__init__.py`에 file_handler export

### 5. 테스트

- [ ] test_resolver.py — 크기 분기, context cache 재사용, 혼합 파일
- [ ] test_cache_store.py — Redis CRUD, TTL, hash_key
- [ ] test_uploader.py — 업로드, create_cache, 재시도, 임시 파일 삭제
- [ ] test_splitter.py — PDF 분할, 재귀 재분할, 분할 파트가 50MB 이하인데 Files API 업로드 거부당하는 케이스 확인 (실제 업로드 테스트)

### 6. 문서 (구현 완료 후)

- [ ] README: context_cache_ttl 사용법, 손익분기점, 캐시 키 경고

---

## 미래 고려사항

- **Files API 프로젝트 저장 제한 20GB**: 대량 파일 업로드 시 초과 가능. 사용량 모니터링 + 초과 시 오래된 파일 정리 또는 업로드 제한 로직 필요
- **파일 원본**: 현재 저장 안 함. 사용자가 매 호출마다 `file_paths` 제공. Redis 만료 시 재업로드

---

## 단계별 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| 1 | checksum 기반 캐시 키 ([file-checksum-cache.md](file-checksum-cache.md)) | 완료 |
| 2 | **file_handler 라이브러리 구현** (이 문서) | **이번 작업** |
| 3 | llm-handler 합체 + 서버화 (SDK+Service 분리, 담당자 합의 후) | 미래 |
| 4 | orchestrator(run_many) 파일 연동 + 파일 서버 + MDB 파일 존재 여부 체크 | 미래 |

### Phase 3 요약: 서버화

llm-handler와 llm-request를 하나의 SDK+Service로 합침. SDK에서 checksum 계산 + Redis 조회 → cache hit 시 파일 전송 없이 cached_content name만 서버에 전달.

### Phase 4 요약: orchestrator + 파일 서버

orchestrator.py에 file_handler 연동 (run_many + 공유 파일). 파일 서버 구축하여 파일 원본 저장. MDB에서 파일 서버 데이터 존재 여부 체크.

### 왜 라이브러리 먼저 서버화 나중에?

| 관점 | 라이브러리 (Phase 2) | 서버화 (Phase 3) |
|------|-------------------|----------------|
| Agent 부담 | handler SDK 전부 agent 메모리에 로드 | SDK 하나만 |
| 설정 관리 | Redis/MDB/LLM URL 다 설정 | Service URL 하나만 |
| 보안 | agent가 모든 키 보유 | 키는 서버에만 |
| 네트워크 | 로컬 함수 호출 (빠름) | HTTP 왕복 (수 ms) |

**Phase 2를 라이브러리로 먼저**: 로직 검증이 우선. 서버화는 구조 변환이지 로직 변경이 아님.
