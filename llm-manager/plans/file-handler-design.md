# File Handler 설계: 파일 업로드/캐싱 자동화

## 배경

### 현재 상태 (Phase 1 완료)

[file-checksum-cache.md](file-checksum-cache.md)에서 구현 완료된 사항:
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

3. **run_many에서 파일 미지원**
   - orchestrator가 `file_paths`를 `llm_request_async`에 전달하지 않는 버그

### 목표

- 사용자는 `file_paths`만 전달하면, llm-request가 **자동으로** 최적 방식(inline/Files API/context caching) 결정
- 동일 파일은 **MDB(file_cache 컬렉션)** 에서 기존 업로드/캐시 재사용
- run_many에서 공유 파일 1회 처리 후 모든 state에 주입

### Phase 1과의 차이

Phase 1에서는 사용자가 직접 `upload_file()` → `FileRef`를 `contents`에 넣고, `file_paths`는 캐시 키 생성용으로만 사용했다.

Phase 2에서는 **사용자가 `contents`에 텍스트만 넣고 `file_paths`만 전달하면**, llm-request가 자동으로:
1. 파일 크기에 따라 최적 방식 결정
2. 업로드/캐싱 수행
3. `contents`에 Part 추가 또는 `cached_content` 설정

즉, 사용자는 더 이상 `upload_file`, `create_cache`를 직접 호출할 필요가 없다.

---

## 사전 정보

### Gemini 파일 처리 방식

| 방식 | 설명 | 이 설계에서 |
|------|------|-----------|
| Inline (base64) | 요청 body에 base64 포함 | **사용** (≤20MB) |
| Files API | 서버에 파일 업로드, URI로 참조 | **사용** (>20MB) |
| Context Caching | 파일을 캐싱하여 토큰 90% 할인 | **사용** (PDF >50MB) |
| 암시적 캐싱 | Gemini 2.5+에서 자동 적용 | **미사용** (명시적 캐싱만) |

### 파일 크기 임계값

| 조건 | 처리 방식 | file_cache 저장 |
|------|----------|----------------|
| ≤20MB | inline (base64 변환) | X |
| >20MB | Files API 업로드 | O |
| PDF >50MB | split → Files API → create_cache | O |

- 파일 크기는 checksum 계산 시 이미 구하는 `FileChecksum.size`를 활용
- inline은 매번 파일에서 base64 변환 (CPU 비용 무시 가능), agent_llm 캐시 hit이면 변환 자체 불필요

### TTL 정책

| 대상 | TTL | 관리 방식 |
|------|-----|----------|
| Files API URI | 48시간 (Google 고정) | Redis native TTL |
| Context Cache | 사용자 설정 (기본 1시간) | Redis native TTL |

Redis의 native TTL로 Google 만료와 자동 동기화. 만료 시 키가 자동 삭제됨.

### ESG 참조 로직

[Kiwoom-ESG/backend/LLM/](../../../Kiwoom-ESG/backend/LLM/) 에서 포팅:
- `file_manager.py` → `split_by_size()`: 페이지 기반 PDF 분할, 초과 시 재귀 재분할
- `cache_manager.py` → `create_cache_for_files()`: 재시도 2회, 5초 간격
- `evaluator.py` → `_upload_and_cache()`: split → upload → cache 파이프라인

---

## file_cache 저장소 (Redis)

file_cache는 **Redis**에 저장한다. key-value + TTL 용도에 최적이며, 미래 전체 마이그레이션의 선행 작업.

### 키 설계

```
// Files API 레코드 (파일 >20MB)
key:   "file_cache:files_api:{file_checksum}"
       예: "file_cache:files_api:sha256:abcd1234..."
value: JSON
{
  "file_checksum": "sha256:abcd1234...",
  "file_name": "report.pdf",
  "file_size": 52428800,
  "mime_type": "application/pdf",
  "uri": "https://generativelanguage.googleapis.com/..."
}
TTL:   48시간 (172800초)

// Context Cache 레코드 (PDF >50MB, split 후 캐싱)
key:   "file_cache:context_cache:{hash_key}"
       예: "file_cache:context_cache:sha256:ef5678..."
value: JSON
{
  "file_checksums": ["sha256:abcd1234..."],
  "model": "gemini-2.5-flash",
  "system_instruction": "...",
  "cached_content_name": "cachedContents/abc123...",
  "files_api_uris": ["https://..."]
}
TTL:   사용자 설정 cache_ttl (기본 3600초)
```

### 조회 방식
- Files API: `GET file_cache:files_api:{file_checksum}`
- Context Cache: `GET file_cache:context_cache:{hash_key}`
- 키가 없으면 (만료 포함) → miss → 업로드/캐싱 진행

**Context Cache hash_key 구성**:

`create_cache`의 실제 입력 필드:

| 필드 | hash_key 포함 | 이유 |
|------|-------------|------|
| `model` | **O** | 모델 다르면 다른 캐시 |
| `contents` | **O** (file_checksums로 대체) | URI는 매번 변하므로 checksum 사용 |
| `system_instruction` | **O** | 시스템 프롬프트 다르면 다른 캐시 |
| `display_name` | X | 라벨일 뿐 |
| `ttl` | X | 수명 설정이지 내용 아님 |

---

## 전체 파이프라인

### 단일 호출 (llm_request_async)

```
사용자: llm_request_async(llm_input, file_paths=[...])
    ↓
① checksum 계산 (size, mime_type 포함)
    ↓
② agent_llm 조회 (done_check)
   - 캐시 키: hash(llm_input + file_checksums)
   - URI, cached_content는 캐시 키에서 제외
   ├─ HIT → LLMResponse 반환 (끝)
   └─ MISS ↓
③ 파일 크기 분기
   ├─ ≤20MB → inline (base64 변환, contents에 Part 추가)
   │         file_cache 저장 안 함 → ⑤로
   │
   ├─ >20MB → file_cache 조회 (file_checksum으로)
   │   ├─ HIT → 기존 URI 사용 → ⑤로
   │   └─ MISS → Files API 업로드 → file_cache 저장 → ⑤로
   │
   └─ PDF >50MB → file_cache 조회 (hash_key로)
       ├─ HIT → 기존 cached_content_name 사용 → ⑤로
       └─ MISS → split → Files API 업로드 → create_cache
                 → file_cache 저장 → ⑤로
    ↓
⑤ llm_input 조립
   - inline: contents에 base64 Part 추가
   - Files API: contents에 file_uri Part 추가
   - context_cache: llm_input["cached_content"] = cached_content_name
    ↓
⑥ llm_handler.call() 실행
    ↓
⑦ agent_llm에 결과 저장 (기존 save 로직)
```

### 배치 호출 (run_many)

```
사용자: run_many(states, file_paths=[...])
    ↓
① 공유 file_paths의 checksum 계산
    ↓
② 파일 크기 분기 (file_cache 조회 스킵)
   ├─ ≤20MB → inline 준비
   ├─ >20MB → Files API 업로드
   └─ PDF >50MB → split → Files API 업로드 → create_cache
    ↓
③ file_cache에 저장 (1회)
    ↓
④ 모든 state의 llm_input에 파일 참조 주입
    ↓
⑤ 각 state별 기존 파이프라인
   - done_check (캐시 키에 file_checksums 포함) → cache hit/miss → call → save
   - semaphore로 동시성 제어
```

### 단일 vs 배치 차이

| | 단일 호출 | 배치 호출 |
|--|---------|---------|
| file_cache 조회 | O | X (스킵) |
| 파일 업로드 | 필요 시 1회 | 무조건 1회 |
| file_cache 저장 | O | O |
| done_check | 1회 | 각 state별 (질문이 다르니까) |

### 404 Fallback

file_cache hit으로 URI/cached_content를 재사용했는데 Gemini에서 404 반환 시:
1. file_cache 해당 레코드 삭제
2. 재업로드/재캐싱 진행
3. file_cache에 새 레코드 저장

---

## keying.py 변경

### 캐시 키에서 제거

- `cached_content` → 제거 (매번 달라지는 volatile 값)
- contents 안의 `file_uri`, `Part(type="file")` → 정규화 시 URI 제거

### 캐시 키에 유지

- `file_checksums` → 파일 동일성 판단의 핵심

**결과**: 같은 파일(checksum 동일) + 같은 질문 + 같은 설정 → 어떤 URI/cached_content를 썼든 cache hit.

### 경고사항 (README 명시)

> 캐시 키는 파일 내용(checksum) 기반입니다.
> - 같은 파일이면 URI/cached_content가 달라도 cache hit
> - 파일이 수정되면(checksum 변경) 기존 캐시 무효화, 새로 호출
> - 모델, temperature 등 llm_input 설정이 다르면 별도 캐시 엔트리
> - `cached_content`, `file_uri`는 캐시 키에서 제외됨

---

## file_handler 모듈 구조

```
llm_request/file_handler/
├── __init__.py        # public API: resolve_files
├── resolver.py        # 파일 처리 진입점 (크기 분기, 파이프라인 조율)
├── cache_store.py     # file_cache Redis CRUD (조회, 저장, 갱신)
├── uploader.py        # Files API 업로드 + create_cache 호출
└── splitter.py        # PDF 분할 (ESG에서 포팅)
```

### resolver.py — 진입점

```python
@dataclass
class ResolvedFiles:
    contents_parts: list[Part]          # inline 또는 file_uri Part들
    cached_content: str | None          # context cache name (있는 경우)
    file_cache_docs: list[dict]         # file_cache에 저장할/저장된 문서들


async def resolve_files(
    file_metas: list[FileChecksum],
    file_paths: list[str],
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    cache_ttl: int = 3600,
    use_file_cache: bool = True,        # run_many는 False로 전달
    database: str | None = None,
) -> ResolvedFiles:
    """
    파일 크기에 따라 최적 방식(inline/Files API/context caching) 결정.
    file_cache 조회 → miss면 업로드/캐싱 → 결과 반환.
    """
```

**분기 로직**:
```
for each file_meta, file_path:
    if file_meta["size"] <= 20MB:
        → Part.from_file(file_path) (inline base64)
    elif file_meta["mime_type"] == "application/pdf" and file_meta["size"] > 50MB:
        → PDF split 경로 (splitter → uploader → create_cache)
    else:
        → Files API 업로드 경로
```

### cache_store.py — file_cache Redis 조작

```python
def find_files_api(
    file_checksum: str,
) -> dict | None:
    """Redis에서 Files API 레코드 조회.
    key: file_cache:files_api:{file_checksum}
    """


def find_context_cache(
    hash_key: str,
) -> dict | None:
    """Redis에서 Context Cache 레코드 조회.
    key: file_cache:context_cache:{hash_key}
    """


def save_files_api(
    file_checksum: str,
    doc: dict,
    ttl: int = 172800,          # 48시간
) -> None:
    """Files API 레코드 저장 (Redis SET + TTL)."""


def save_context_cache(
    hash_key: str,
    doc: dict,
    ttl: int = 3600,            # 기본 1시간
) -> None:
    """Context Cache 레코드 저장 (Redis SET + TTL)."""


def delete_key(key: str) -> None:
    """404 fallback 시 키 삭제."""
```

### uploader.py — API 호출

```python
async def upload_file(
    path: str,
    mime_type: str,
) -> FileRef:
    """Files API 업로드. FileRef(uri, name, mime_type) 반환."""


async def create_context_cache(
    file_refs: list[FileRef],
    *,
    model: str,
    system_instruction: str | None = None,
    ttl: int = 3600,
) -> CacheRef:
    """
    create_cache 호출. CacheRef(name, model, ...) 반환.
    재시도: 2회, 5초 간격 (500, 503, 429, RESOURCE_EXHAUSTED).
    """
```

### splitter.py — PDF 분할

```python
def split_pdf(
    path: str,
    target_mb: int = 50,
) -> list[str]:
    """
    PDF를 target_mb 이하 파트로 분할.
    반환: 분할된 임시 파일 경로 리스트.

    ESG 로직 포팅:
    - 페이지별 바이트 추정 (이미지 xref + 텍스트/벡터)
    - 추정값을 실제 파일 크기에 맞게 스케일링
    - 누적 그룹핑으로 target_bytes 이하 파트 생성
    - 생성된 파트가 초과 시 재귀적 재분할
    """
```

---

## runner.py 변경

### 공개 함수 시그니처

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
    cache_ttl: int = 3600,              # 신규: create_cache용 TTL
) -> tuple[LLMResponse, dict]:
```

4개 함수 모두 동일 적용: `llm_request`, `llm_request_async`, `llm_request_stream`, `llm_request_stream_async`

### _prepare() 변경

```
기존 흐름:
  file_paths → checksum → keying_input → done_check → (hit or miss)

변경 흐름:
  file_paths → checksum → keying_input → done_check
  ├─ HIT → 반환
  └─ MISS → resolve_files() 호출
            → llm_input 조립 (contents/cached_content 주입)
            → 이후 call/save
```

`resolve_files()` 호출 위치는 `_prepare()` 이후, `llm_handler.call()` 이전.

---

## orchestrator.py 변경

### run_many 시그니처

```python
async def run_many(
    states: Iterable[dict],
    *,
    file_paths: list[str] | None = None,    # 신규: 공유 파일
    concurrency: int = 5,
    database: str | None = None,
    cache_ttl: int = 3600,                  # 신규: create_cache용 TTL
) -> AsyncGenerator[tuple[LLMResponse, dict], None]:
```

### run_many 내부 흐름

```python
# 1. 공유 파일 처리 (1회)
if file_paths:
    file_metas = [file_checksum(p) for p in file_paths]
    first_input = states[0]["llm_input"]  # 첫 번째 state에서 model/system 추출
    resolved = await resolve_files(
        file_metas, file_paths,
        model=first_input.get("model"),
        system_instruction=first_input.get("system"),
        cache_ttl=cache_ttl,
        use_file_cache=False,         # MDB 조회 스킵, 바로 업로드
    )
    # file_cache에 저장 (1회)

# 2. 모든 state의 llm_input에 파일 참조 주입
for state in states:
    llm_input = state["llm_input"]
    if resolved.cached_content:
        llm_input["cached_content"] = resolved.cached_content
    if resolved.contents_parts:
        # contents에 Part 추가
    # keying용 file_checksums 주입
    llm_input["file_checksums"] = [m["checksum"] for m in file_metas]

# 3. 각 state별 기존 파이프라인 (done_check → call → save)
```

### run_many_stream도 동일 변경

### 기존 버그 수정

orchestrator가 개별 state의 `file_paths`를 `llm_request_async`에 전달하지 않던 문제.
새 설계에서는 run_many 레벨에서 파일을 처리하므로 개별 state에 file_paths를 넣는 경우는 없음.

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| Files API 업로드 실패 | llm-handler 측 재시도에 위임 |
| create_cache 실패 | 재시도 2회, 5초 간격 (500, 503, 429, RESOURCE_EXHAUSTED) |
| Gemini 404 (만료된 URI/cache) | file_cache 레코드 삭제 → 재업로드/재캐싱 → file_cache 갱신 |
| LLM 호출 실패 | 기존 runner.py 로직 유지 |
| PDF split 실패 | PyMuPDF 에러 전파 |
| file_paths에 파일 없음 | FileNotFoundError (기존 동작) |

---

## 의존성 변경

### 신규 추가 (pyproject.toml)

- `PyMuPDF (fitz)`: PDF 분할용
- `kiwoom-redis-handler`: file_cache Redis 저장소

### 기존 유지

- `kiwoom-llm-handler >= 0.1.0`: upload_file, create_cache, call, stream
- `kiwoom-mdb-handler >= 2.0.0`: agent_llm 컬렉션 (LLM 응답 캐시)
- `filetype >= 1.2.0`: MIME 감지

---

## 이 작업 범위

### 포함

- file_handler 모듈 신규 생성 (resolver, cache_store, uploader, splitter)
- file_cache MDB 컬렉션 스키마 및 TTL index
- runner.py 변경 (resolve_files 호출, cache_ttl 파라미터)
- orchestrator.py 변경 (공유 file_paths, file_paths 미전달 버그 수정)
- keying.py 변경 (cached_content 캐시 키 제외 — Phase 1에서 완료 확인 필요)
- 테스트

### 미포함

- 이미지/오디오/비디오 전용 분할 로직 (PDF만)
- Gemini 이외 LLM 지원
- file_cache 관리 CLI/API
- README 업데이트 (구현 완료 후 별도 진행)

---

## 미래 방향

현재 file_cache는 Redis, LLM 응답 캐시(agent_llm)는 MDB에 저장한다.
향후 파일 서버 구축 시:
- **파일 자체** → 파일 서버에서 관리
- **메타데이터/조회** → Redis로 통합 (agent_llm도 Redis 마이그레이션 검토)
- `cache_store.py`만 교체하면 resolver/uploader/splitter는 변경 없음

---

## 수정 대상 파일 목록

| 파일 | 작업 |
|------|------|
| `llm_request/file_handler/__init__.py` | **신규** — public API export |
| `llm_request/file_handler/resolver.py` | **신규** — 파일 처리 진입점 |
| `llm_request/file_handler/cache_store.py` | **신규** — file_cache Redis CRUD |
| `llm_request/file_handler/uploader.py` | **신규** — Files API + create_cache |
| `llm_request/file_handler/splitter.py` | **신규** — PDF 분할 (ESG 포팅) |
| `llm_request/runner.py` | 수정 — resolve_files 호출, cache_ttl |
| `llm_request/orchestrator.py` | 수정 — 공유 file_paths, 버그 수정 |
| `llm_request/keying.py` | 확인 — cached_content 제외 동작 검증 |
| `llm_request/__init__.py` | 수정 — file_handler export 추가 |
| `pyproject.toml` | 수정 — PyMuPDF 의존성 |
| `tests/test_resolver.py` | **신규** |
| `tests/test_cache_store.py` | **신규** |
| `tests/test_uploader.py` | **신규** |
| `tests/test_splitter.py` | **신규** |
| `tests/test_orchestrator.py` | 수정 — 파일 관련 테스트 추가 |

---

## 사용 예시 (구현 후)

### 단일 호출 — 파일만 전달하면 자동 처리

```python
from llm_request import llm_request_async

# 사용자는 file_paths만 전달, 업로드/캐싱은 llm-request가 자동 처리
result, meta = await llm_request_async(
    {"contents": "이 PDF 요약해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)
# meta["source"] == "cache" or "live"
```

### 대용량 PDF — 자동 split + context caching

```python
# 100MB PDF → 자동으로 split → Files API → create_cache
result, meta = await llm_request_async(
    {
        "contents": "ESG 점수 평가해줘",
        "model": "gemini-2.5-flash",
        "system": "당신은 ESG 평가 전문가입니다.",
    },
    file_paths=["large_report.pdf"],
    cache_ttl=1800,  # context cache 30분
)
```

### 배치 호출 — 같은 파일 + 다른 질문 N개

```python
from llm_request import run_many

states = [
    {"llm_input": {"contents": "ESG 점수 평가해줘", "model": "gemini-2.5-flash"}},
    {"llm_input": {"contents": "탄소배출 데이터 추출해줘", "model": "gemini-2.5-flash"}},
    {"llm_input": {"contents": "거버넌스 항목 분석해줘", "model": "gemini-2.5-flash"}},
]

async for response, meta in run_many(states, file_paths=["report.pdf"], cache_ttl=1800):
    print(response.text)
```

### 텍스트만 — 기존과 동일

```python
# file_paths 생략 → 기존 동작 그대로
result, meta = await llm_request_async(
    {"contents": "오늘 날씨 어때?"},
)
```
