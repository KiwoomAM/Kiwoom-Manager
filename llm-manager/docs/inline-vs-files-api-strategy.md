# inline vs Files API 전략 (파일 전달 파이프라인)

> `file_manager` 분리([file-manager-separation.md](file-manager-separation.md)) 이후
> **받은 파일을 Gemini에 어떻게 전달할지** 결정하는 문서.
> SDK는 파일 bytes를 보내지 않으며, Service가 file_manager로부터 bytes를 수급한다.

## 1. 배경 / 문제 정의

Phase 3 설계는 **SDK 중심 분기**였다:
- `≤20MB` → SDK가 base64 inline으로 body에 포함
- `>20MB` → SDK가 `/v1/files`로 사전 업로드 후 Service는 Redis에서 URI 조회

이 분기는 다음 전제 위에 있었다:
1. SDK가 **로컬 파일**에 접근 가능
2. SDK가 **크기/타입 판단**을 직접 수행

`file_manager` 분리로 **두 전제 모두 무효화**된다:
- Service는 파일 bytes가 없는 상태로 요청을 받음 (`FileMeta` 리스트만)
- 필요할 때 `file_manager`로부터 bytes를 HTTP로 수급
- SDK는 얇은 HTTP 래퍼로만 기능

따라서 **분기·캐시·업로드 로직 전부 Service 내부로 이전** 및 재설계해야 한다.

이 문서는 분기 규칙, 캐시 활용, 파일 처리 흐름만 다룬다.
인터페이스 변경(DTO, SDK 얇아짐 등)은 문서 ①에서 결정됨.

## 2. 사전 정보

### 2.1 Gemini inline 제한 (모달리티별 — 재확인)

Google 공식 문서를 모달리티별로 확인한 결과:

| 모달리티 | 요청 body **전체** 제한 | 개별 파일 제한 | 출처 |
|---|---|---|---|
| 오디오 | **20 MB** (prompt + system + inline files 합산) | 없음 (9.5시간 길이 한도) | [audio docs](https://ai.google.dev/gemini-api/docs/audio) |
| 이미지 | **20 MB** (prompt + system + inline bytes 합산) | 명시 없음 | [image-understanding docs](https://ai.google.dev/gemini-api/docs/image-understanding) |
| PDF | 명시 없음 (일반 100MB 적용 추정) | **50 MB** per file | [document-processing docs](https://ai.google.dev/gemini-api/docs/document-processing) |
| 비디오 | inline 사실상 불가 | Files API 전용 | [video docs](https://ai.google.dev/gemini-api/docs/video) |

**중요**: 이미지/오디오 20MB는 **개별 파일 한도가 아닌 요청 body 전체 한도**. 여러 파일 + 프롬프트 + system instruction을 모두 합산한 값이 20MB를 넘으면 Files API 필수.

### 2.2 캐시 TTL

| 캐시 | TTL | 비고 |
|---|---|---|
| Gemini Files API URI | **48시간** | 만료 후 404 발생 → 재업로드 필요 |
| Gemini Context Cache | 사용자 설정 TTL (기본 1h) | 토큰 90% 할인 but 저장비 발생 (시간당 4회 미만이면 손해) |
| Redis `files_api:{checksum}` | 47시간 (안전 마진) | Gemini 48h보다 1h 짧게 |
| Redis `files_api_uri:{uri}` | 47시간 (Files API와 동일) | 역인덱스 (§8 용량 관리용) |
| Redis `context_cache:{hash}` | 사용자 TTL과 동일 | Gemini Context Cache와 동기화 |

### 2.3 base64 오버헤드
원본 × 약 1.33배. 원본 20MB → base64 ~26.6MB. 요청 body 한도 계산 시 주의.

### 2.4 지원 mime 화이트리스트

Gemini API 공식 문서에서 확인한 지원 mime 타입. 이 목록에 없으면 거부(§4.5).

| 모달리티 | 확정도 | mime 목록 |
|---|---|---|
| 이미지 | ✅ 공식 | `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/heif` |
| 오디오 | ✅ 공식 | `audio/wav`, `audio/mp3`, `audio/aiff`, `audio/aac`, `audio/ogg`, `audio/flac` |
| 문서 (비전 지원) | ✅ 공식 | `application/pdf` |
| 문서 (텍스트 추출만) | ⚠️ 보수적 | `text/plain`, `text/html`, `text/csv`, `text/markdown`, `text/xml`, `application/json`, `application/xml` |
| 비디오 | ⚠️ 보수적 | `video/mp4`, `video/mpeg`, `video/mov`, `video/avi`, `video/x-flv`, `video/webm`, `video/3gpp` |

> "보수적"으로 표시된 항목은 공식 문서에 명시적 목록은 없으나 예시/통상적으로 지원되는 포맷. Gemini가 거부하면 에러가 상위로 전파됨.
> 
> 문서 중 PDF만 **비전 지원**(이미지·표·레이아웃 인식). 나머지 텍스트계는 **텍스트 추출만**되어 시각 요소(차트·포맷)가 손실됨 — 호출자 인지 필요.
> 
> **미확인 (TBD)**: Gemini Files API가 `text/*` / `application/json` 등 텍스트계를 업로드 형태로 받는지 공식 문서에서 확인되지 않음. 현재 설계는 텍스트도 Files API 경로로 흘러갈 수 있으나, 미지원이면 런타임 에러로 전파됨. 향후 확인 후 "텍스트는 inline 전용" 정책 추가 가능.
> 
> **오디오 9.5시간 제한**: 파일 bytes만으론 재생 길이 산출 불가 → **사전 체크 불가**. 업로드 후 Gemini가 거부하면 런타임 에러로 전파.

## 3. 3계층 캐시 구조

llm-request가 관여하는 캐시 전체 그림.

| # | 저장소 | 키 | 값 | 목적 | 관할 |
|---|---|---|---|---|---|
| 1 | file_manager DB | `(file_name, file_mtime, file_size)` 튜플 | metadata 블록 전체 (file_name, file_mtime, file_size, file_path, mime_type, extension) | 같은 파일 중복 저장 방지 | file_manager |
| 2-A | Redis `files_api:{checksum}` | llm-request 자체 계산 checksum | Files API URI (+ size, PDF split 정보) | 48h 내 재업로드 방지 | llm-request |
| 2-B | Redis `context_cache:{hash_key}` | sha256(checksums + model + system) | Gemini Context Cache name (+ file_checksums 리스트) | 같은 파일+모델+system으로 여러 프롬프트 시 토큰 절약 | llm-request |
| 3 | MDB `agent_llm` | cache_key = sha256(llm_input + checksums) | LLM 응답 결과 | 동일 요청 재호출 방지 | llm-request |

**Layer 1 주의**: file_manager는 내부적으로 **checksum을 만들지 않는다**. 3필드 인덱스로 관리. llm-request는 받은 metadata로 **자체 해싱**해서 Layer 2·3의 키를 만든다 (문서 ① §3.2). 두 시스템이 서로 다른 식별 방식을 써도, llm-request 내부 키 일관성만 유지되면 문제 없음.

**Layer 2 용량 관리 (§8, Phase B)**: Files API 20GB 상한 대응은 **Redis SCAN + TTL 기반 FIFO**로 처리한다. 즉 Redis 키(checksum)를 시작점으로 오래된 것을 골라 Gemini 삭제 + Redis 삭제. Gemini 쪽 파일 목록으로부터 Redis 원본 키를 역추적할 일이 없으므로 **역인덱스는 두지 않는다** (역인덱스 개념: `docs/concepts.md` 참고).

**cache_key 불변 조건 (§3 주의)**: Layer 3의 `cache_key`에는 `files_api_uri`(Gemini URI) 또는 `cached_content` 같은 **volatile 값은 절대 포함되지 않는다**. 캐시 키 재현성을 위해 (file_name, file_mtime, file_size) 기반 checksum만 입력으로 사용. 이미 [keying.py](../service/app/logic/keying.py)의 `build_llm_input_subset` / `canonicalize_llm_input`에서 volatile 제거 로직이 구현되어 있음.

## 4. 핵심 설계 결정

### 4.1 파일별 분류 — 최소 개별 체크 + 합계 조정 (부분 섞기)

**Step 1 — 비디오만 선제 분리**

| 조건 | 결정 |
|---|---|
| `video/*` | **Files API** (Gemini inline 자체 불가) |
| 그 외 전부 | **inline 후보 (일단)** |

> 나머지 개별 한도(PDF 50MB, image/audio/text 20MB)는 **Step 2의 합계 체크에 녹아들어** 별도 체크 불필요. PDF 50MB 초과는 이후 Files API 업로드 단계(§6.2 C 분기)의 `splitter.split_pdf`에서 처리 — classify_files는 이 부분 관심 없음.

**Step 2 — 합계 체크 (파일 + 비파일 요청 구성요소)**

요청 body에는 파일 외에도 텍스트·system·tools·schema·history·config 등 "비파일 구성요소"가 포함된다. 이것들도 Gemini 요청 전체 크기 한도에 카운트되므로 합계 계산에 **반드시 포함**.

```python
def _estimate_non_file_bytes(llm_input: dict) -> int:
    """파일 Part를 제외한 llm_input의 JSON 직렬화 byte 수 근사.
    
    classify_files 호출 시점에는 llm_input에 파일 Part가 아직 들어있지 않으므로
    (파일은 file_metas로 별도 전달) 단순 직렬화 크기로 충분.
    """
    try:
        return len(json.dumps(llm_input, default=str).encode("utf-8"))
    except Exception:
        return 0  # 추정 실패 시 0 → inline 판정에 관대한 쪽으로 fallback
```

모달리티 인지 유지:
- `inline_pool`에 image 또는 audio 포함 → **합계 20MB** (엄격)
- PDF/텍스트만 → **합계 50MB** (완화)

**Step 3 — 작은 것부터 정렬 후 일괄 결정**

정렬 방향은 **오름차순(작은 것부터)**. 반복 호출 시 Redis HIT으로 재사용되는 이점이 큰 파일일수록 Files API가 유리하므로, 작은 파일을 inline에 우선 남기고 큰 파일을 Files API로 넘긴다.

```python
def classify_files(
    file_metas: list[FileMeta],
    llm_input: dict,
) -> tuple[list[FileMeta], list[FileMeta]]:
    inline_pool: list[FileMeta] = []
    files_api_pool: list[FileMeta] = []
    
    # Step 1: 비디오만 강제 분리
    for m in file_metas:
        if m.mime_type.startswith("video/"):
            files_api_pool.append(m)
        else:
            inline_pool.append(m)
    
    # Step 2: 합계 한도 결정 (모달리티 인지)
    has_image_or_audio = any(
        m.mime_type.startswith(("image/", "audio/")) for m in inline_pool
    )
    limit = 20 * 1024 * 1024 if has_image_or_audio else 50 * 1024 * 1024
    
    # Step 3: 작은 것부터 정렬 + 한 번 순회 (재평가 없음 — Q2 보수)
    inline_pool.sort(key=lambda m: m.file_size)   # 오름차순
    total = _estimate_non_file_bytes(llm_input)
    kept: list[FileMeta] = []
    for m in inline_pool:
        if total + m.file_size <= limit:
            kept.append(m)
            total += m.file_size
        else:
            files_api_pool.append(m)
    
    return kept, files_api_pool
```

### 4.1.1 자동 분류 원칙

- **호출자는 inline/Files API를 직접 지정할 수 없다**. Service가 자동 분류.
- **inline 우선**: 제한을 넘지 않는 한 inline 사용 (Files API 업로드 비용·지연 회피).
- Files API는 제한으로 어쩔 수 없는 파일에만 사용.
- **반복 호출 최적화 (오름차순 정렬 근거)**: 작은 파일을 inline에 우선 남기고, 큰 파일을 Files API로 넘김 → 자주 쓰이는 큰 파일은 Redis HIT으로 48h 내 재사용되어 매 호출 fetch 비용 절약.

### 4.2 이진 분기 vs 부분 섞기

| 방식 | 규칙 | 장점 | 단점 |
|---|---|---|---|
| 이진 | 조건 만족 → **전부 inline** / 아니면 **전부 Files API** | 구현 단순, 흐름 명확 | 작은 파일도 큰 파일 때문에 Files API 경유 — 비용 낭비 |
| **부분 섞기 (선택)** | 파일별로 타입 한도 체크 → 가능한 것만 inline, 나머지 Files API | inline 비용·속도 이점 최대화, 같은 호출에서 유연 | 구현 복잡, contents에 URI Part/inline Part 혼재 |

**부분 섞기 선택 이유**
- **inline = 비용 0** (Files API 업로드·저장비 없음). 제한 허용 범위 내에선 무조건 이득
- 큰 파일 하나가 섞였다고 작은 파일까지 Files API로 몰아가면 불필요한 업로드 비용
- Gemini API는 같은 `contents` 배열 안에 inline Part와 URI Part 혼재를 원래부터 허용
- 구현 복잡도는 분류 함수(§4.1) 하나로 집약 → 다른 모듈은 결과만 받아서 처리
- **정렬 방향(작은 것부터)로 반복 호출 이득 극대화**: 작은 파일은 inline로 매번 간단히 전송, 큰 파일은 Files API + Redis HIT으로 48h 내 재사용 → 큰 파일의 반복 fetch 비용 제거

### 4.3 임시 파일 처리 — /tmp (방식 B)

| 방식 | 저장 | 장점 | 단점 |
|---|---|---|---|
| A. 메모리 | Python `bytes` 변수 | I/O 없음 | 대용량 부담, PDF split 불가(PyMuPDF는 path 필요) |
| **B. /tmp (선택)** | `tempfile.NamedTemporaryFile(delete=True)` | 대용량 OK, split 호환, 자동 삭제 | 디스크 I/O 1회 |

**B 선택 이유**
- `splitter.py`(PyMuPDF)가 파일 경로 필요 → split 필요한 PDF는 B 필수
- `uploader.upload_file(path, mime)` 기존 시그니처 유지 가능 → 코드 재사용
- `with NamedTemporaryFile(delete=True)` 블록으로 누수 방지

### 4.4 context_cache와 부분 섞기의 관계

Context Cache는 **Files API URI 기반**이라 inline Part에는 적용되지 않는다. 부분 섞기 하에선 같은 호출에서 일부 파일만 Files API를 타는 상황이 자연스럽게 발생한다.

**정책**

| 상황 | 처리 |
|---|---|
| `context_cache_ttl` 지정됨 | Files API로 분류된 파일들만 context_cache 대상. **inline으로 분류된 파일은 context_cache에 참여하지 않음** (=매 호출 inline 전송) |
| `context_cache_ttl` 지정됨 + **모든 파일이 inline**으로 분류된 경우 | **강제로 전부 Files API 로 승급** + 경고 로그. 호출자가 캐시 의도를 명확히 밝힌 것이므로 존중 |
| `context_cache_ttl` 지정 없음 | §4.1 분류 결과 그대로 사용 (inline 우선) |

**두 번째 경우 경고 문구 예시**
```python
logger.warning(
    "context_cache_ttl=%d 지정됨 — 모든 파일이 inline 조건을 만족하지만 "
    "context cache 활용을 위해 전 파일을 Files API 경로로 승급합니다.",
    context_cache_ttl,
)
```

**참고**: 이미 일부 파일이 Files API로 분류된 상태라면 승급이 불필요 — 그 파일들로 context_cache를 생성하고, inline 파일은 그대로 매 호출 inline Part로 전송.

### 4.5 지원하지 않는 mime 타입 거부

Gemini가 지원하지 않는 파일 타입이 file_manager로부터 전달될 수 있다 (mime 오탐지, 지원 외 포맷 등). **LLM 호출 전에 거부**하여 비용·시간을 낭비하지 않는다.

**정책**: `SUPPORTED_MIME_TYPES` 화이트리스트(§2.4)에 없는 mime이 하나라도 포함되면 **전체 요청 거부**.

**검증 시점**: 요청 수신 직후, `prepare()` (cache_key 계산·MDB 조회) **진입 이전**. 잘못된 입력은 fail-fast — 캐시 조회 비용도 낭비하지 않음.

**에러**: `UnsupportedFileTypeError` (HTTP 422) raise.

**방어적 검증**: file_manager가 잘못된 mime_type을 전달해도 llm-request 쪽에서 2차 방어.

### 4.6 개별 파일 2GB 초과 거부

Gemini Files API는 **파일당 2GB** 상한. 초과 파일은 inline(20MB/50MB)·Files API 어느 경로로도 처리 불가.

**정책**: `FileMeta.file_size`가 2GB를 초과하는 파일이 하나라도 포함되면 **전체 요청 거부** (비디오/PDF/이미지/오디오/텍스트 타입 무관).

**검증 시점**: mime 화이트리스트 체크 **직후**, `prepare()` 진입 이전. 순서: `validate_mime_types` → `validate_file_sizes` → `prepare()`

**에러**: `FileTooLargeError` (HTTP 422) raise.

**방어적 검증**: file_manager에서도 선제 필터될 가능성 있으나, llm-request에서 2차 방어.

## 5. 전체 파이프라인

```
LLMRequestBody (llm_input + file_metas + context_cache_ttl)
       │
       ▼
┌───────────────────────────────────────────────────┐
│  validate_mime_types()  [validators.py]            │
│  - SUPPORTED_MIME_TYPES 화이트리스트 체크 (§2.4)      │
│  - 지원 안 하면 UnsupportedFileTypeError (HTTP 422) │
└───────────────────────────────────────────────────┘
       │ (통과)
       ▼
┌───────────────────────────────────────────────────┐
│  validate_file_sizes()  [validators.py]            │
│  - 개별 파일 2GB 이하 체크 (§4.6)                    │
│  - 초과 시 FileTooLargeError (HTTP 422)             │
└───────────────────────────────────────────────────┘
       │ (통과)
       ▼
┌───────────────────────────────────────────────────┐
│  prepare()  [runner.py]                           │
│  - 각 FileMeta → checksum 계산                      │
│    (keying.make_file_checksum)                    │
│  - cache_key = hash(llm_input + checksums)        │
│  - MDB agent_llm 조회                               │
└───────────────────────────────────────────────────┘
       │
       ├─ HIT  → cached result 즉시 반환 (Layer 3)
       │
       └─ MISS
            │
            ▼
┌───────────────────────────────────────────────────┐
│  classify_files(file_metas, llm_input)  [resolver]  │
│  - Step 1: 비디오만 선제 분리                        │
│  - Step 2: _estimate_non_file_bytes + 파일 size 합  │
│            모달리티 인지 한도 (20 or 50MB)           │
│  - Step 3: 작은 것부터 정렬, 한 번 순회 결정          │
│  - context_cache_ttl + 전원 inline이면 Files API 승급│
└───────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────┬─────────────────────────┐
│  inline_pool 처리 (§6.1) │ files_api_pool 처리 (§6.2)│
│                         │                         │
│  for m in inline_pool:  │  A/B/C miss 3단계 분기  │
│    bytes = fetch(path)  │  - A: context_cache HIT │
│    part = from_bytes()  │  - B: files_api HIT     │
│                         │  - C: 신규 업로드        │
│  → inline_parts          │  → uri_parts             │
│                         │    (+ cached_content)   │
└─────────────────────────┴─────────────────────────┘
            │
            ▼
┌───────────────────────────────────────────────────┐
│  call_input['contents'] =                         │
│    inline_parts + uri_parts + 원래 텍스트           │
│  (cached_content 있으면 별도 필드로)                 │
│  llm_handler.aio_call(**call_input)               │
└───────────────────────────────────────────────────┘
       │
       ▼
┌───────────────────────────────────────────────────┐
│  finalize()                                       │
│  - MDB agent_llm 저장 (Layer 3)                    │
│  - (LLMResponse, meta) 반환                        │
└───────────────────────────────────────────────────┘
```

## 6. 분기 상세

### 6.1 inline 분류 파일 처리 (Redis 우회)

`classify_files()`의 **`inline_pool`** 에 담긴 파일만 대상. Redis 전혀 접근하지 않음.

```python
inline_parts: list[Part] = []
for meta in inline_pool:
    bytes_data = await fetch_file_bytes(meta.file_path)  # file_manager HTTP
    inline_parts.append(
        Part.from_bytes(data=bytes_data, mime_type=meta.mime_type)
    )
```

**특성**
- **Layer 2-A / 2-B 전혀 접근 안 함** — Redis 조회·저장 없음
- 매 호출마다 file_manager에서 bytes 수급 (제한 내 작은 파일이라 부담 낮음)
- Files API 48h TTL 재업로드 이슈 없음
- **tempfile 불필요** — `Part.from_bytes`는 메모리 bytes를 그대로 수용. PDF split 같은 파일 경로 요구 로직은 Files API 경로(§6.2)에만 해당
- context_cache와 무관 (inline 파일은 context cache 참여하지 않음)

### 6.2 Files API 분류 파일 처리 — miss 3단계 분기 (A / B / C)

`classify_files()`의 **`files_api_pool`** 에 담긴 파일만 대상. Redis + Gemini Files API 활용.

**핵심 변경**: `context_cache` hash_key 계산에 사용하는 checksum 리스트는 **`files_api_pool`의 checksum만** 포함한다. inline 파일은 context cache에 참여하지 않음.

```
Files API 분류 진입
  │
  ▼
① Redis context_cache 조회
   hash_key = make_context_cache_key(
       checksums=[make_file_checksum(m) for m in files_api_pool],
       model, system_instruction,
   )
   │
   ├─ HIT (= 분기 A) ───────────────────────────────────┐
   │   • call_input['cached_content'] = cached_content_name │
   │   • bytes 재다운로드 없음, Files API 재업로드 없음       │
   │   • uri_parts = []  (cached_content가 파일을 대신함)  │
   │                                                       │
   └─ MISS                                                 │
        │                                                  │
        ▼                                                  │
② files_api_pool의 각 파일 Redis files_api:{checksum} 조회    │
   │                                                       │
   ├─ 모두 HIT (= 분기 B)                                   ││
   │   • 기존 URI로 Part.from_uri 구성 → uri_parts        ││
   │   • context_cache_ttl 있으면 create_cache + Redis 저장 ││
   │                                                      ││
   └─ 일부/전부 MISS (= 분기 C)                              ││
        │                                                 ││
        ▼                                                 ││
   MISS 파일마다:                                           ││
     • file_manager에서 bytes 다운로드                       ││
     • tempfile(/tmp)에 저장                                ││
     • (Phase B) ensure_files_api_capacity(size) 호출 — §8 ││
     • PDF && size > 50MB:                                 ││
         splitter.split_pdf(path) → upload_pdf_parts       ││
     • else:                                               ││
         upload_file(tmp_path, mime_type) → FileRef.uri    ││
     • save_files_api(checksum, {size, uris, ...})         ││
     • context_cache_ttl 있으면 create_cache + Redis 저장   ││
   • HIT 파일 + 방금 업로드된 파일 URI → uri_parts         ││
                                                           ││
┌──────────────────────────────────────────────────────────┘│
│  결과: uri_parts (Part.from_uri 리스트), 필요 시 cached_content │
│                                                           │
│  최종 합치기 (resolve_files 반환 후):                          │
│    call_input['contents'] =                                │
│      inline_parts(§6.1) + uri_parts + 원래 텍스트          │
│    cached_content가 있으면 call_input에 별도 필드로           │
└───────────────────────────────────────────────────────────┘
```

**분기별 비용 특성**

| 분기 | bytes 다운로드 | Files API 업로드 | create_cache | 특성 |
|---|---|---|---|---|
| A | ❌ | ❌ | ❌ | 가장 저렴. 같은 파일+모델+system 재호출 |
| B | ❌ | ❌ | 조건부 | 같은 파일이지만 context cache 만료/첫 생성 |
| C | MISS 파일만 | MISS 파일만 | 조건부 | 신규 파일 또는 48h TTL 만료 |

### 6.3 PDF split (>50MB)

- 기존 `splitter.split_pdf(path, target_mb=50)` 그대로 활용
- C 분기에서 bytes → tempfile 저장 → split 필요 여부 체크
- split된 각 파트는 개별 Files API URI로 업로드 → Redis `files_api:{checksum}`에 `split: True` + `uris: [...]` 저장
- 역인덱스는 **각 파트 URI마다** 등록 (§8 용량 관리에서 파트별 삭제 가능성 대비)
- 재호출 시 Redis HIT이면 복수 URI로 복수 Part 구성

## 7. 모듈별 상세

### 7.1 `runner.py`

**제거**
- `_apply_inline_files()`, `_apply_cached_files()`, `_build_server_call_input()`

**수정**
- `llm_request_async` / `llm_request_stream_async`: **진입 직후 validation 수행 (prepare 호출 이전 — fail-fast)**.
  ```python
  async def llm_request_async(llm_input, *, file_metas=None, ...):
      if file_metas:
          validate_mime_types(file_metas)     # §4.5 화이트리스트
          validate_file_sizes(file_metas)     # §4.6 2GB 상한
      prep = prepare(llm_input, file_metas=file_metas, ...)
      ...
  ```
- `prepare()`: `file_meta` 인자를 `file_metas: list[FileMeta]`로 받고, 각 FileMeta에 대해 `keying.make_file_checksum()` 호출하여 checksum 리스트 생성. 기존 keying 로직 그대로 연결.
- `inline_files` 인자 제거, 파일 처리 후 `call_input` 구성 로직을 `resolve_files()` 호출로 단순화.

### 7.2 `resolver.py` (재설계)

Phase 2 라이브러리 버전(`Part.from_file(path)` 로컬 경로 사용)을 **완전히 교체**. `resolve_files`는 **부분 섞기 결과**를 반환.

```python
@dataclass
class ResolvedFiles:
    inline_parts: list[Part]       # §6.1 결과 (inline 분류 파일)
    uri_parts: list[Part]          # §6.2 결과 (Files API 분류 파일)
    cached_content: str | None     # §6.2 분기 A에서만 세팅

async def resolve_files(
    file_metas: list[FileMeta],
    llm_input: dict,                           # 합계 계산용 — classify_files 전달
    *,
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
) -> ResolvedFiles:
    # Step 1: §4.1 분류 (비디오 선제 분리 → 합계 체크 → 작은 것부터 정렬)
    inline_pool, files_api_pool = classify_files(file_metas, llm_input)
    
    # Step 2: §4.4 승급 정책 — 모든 파일이 inline인데 context_cache_ttl 지정됐으면 전원 Files API로
    if context_cache_ttl is not None and not files_api_pool and inline_pool:
        logger.warning(
            "context_cache_ttl=%d 지정 — 모든 파일 Files API 경로로 승급",
            context_cache_ttl,
        )
        files_api_pool = inline_pool
        inline_pool = []
    
    # Step 3: 각 풀 처리
    inline_parts = await _process_inline(inline_pool)           # §6.1
    uri_parts, cached_content = await _process_files_api(       # §6.2
        files_api_pool,
        checksums=[make_file_checksum(m) for m in files_api_pool],
        model=model,
        system_instruction=system_instruction,
        context_cache_ttl=context_cache_ttl,
    )
    return ResolvedFiles(inline_parts, uri_parts, cached_content)
```

**호출자(runner)가 최종 합침**:
```python
call_input["contents"] = resolved.inline_parts + resolved.uri_parts + original_text_contents
if resolved.cached_content:
    call_input["cached_content"] = resolved.cached_content
```

### 7.3 `uploader.py` / `splitter.py` — 그대로 재사용

- `upload_pdf_parts(path, target_mb)` — path 기반, 변경 없음
- `create_context_cache(file_refs, ...)` — 변경 없음
- `split_pdf(path, target_mb)` — 변경 없음
- 호출 측에서 bytes → tempfile → path로 변환 후 호출
- Phase B에서 `ensure_files_api_capacity()` 호출 추가

### 7.4 `keying.py`

- 신규: `make_file_checksum(meta: FileMeta) -> str` (문서 ①에서 결정)
- 기존: `build_llm_input_subset`의 `file_checksums` 필드가 FileMeta에서 추출된 checksum 리스트를 받도록 연결

### 7.5 `cache_store.py` (수정)

**기존 키 유지**
- `file_cache:files_api:{checksum}` — value에 `size`, `uris`, `split` 등 기존 필드 유지
- `file_cache:context_cache:{hash_key}` — value에 `file_checksums: list[str]` 필드 유지 (§8.5 연쇄 삭제에서 사용)

**역인덱스 도입하지 않음**. Files API 용량 관리(§8)는 **Redis SCAN + TTL 기반**으로 처리 (역인덱스 개념: `docs/concepts.md` 참고).

**신규 함수 (§8 용량 관리용)**
```python
def scan_files_api_keys() -> Iterator[str]:
    """file_cache:files_api:* 패턴 SCAN. checksum 이터레이터 반환.
    
    Redis SCAN 사용 (KEYS는 운영상 금지). prefix 제거 후 checksum만 yield.
    """

def get_ttl_files_api(checksum: str) -> int:
    """files_api:{checksum} 키의 남은 TTL(초).
    -1: TTL 없음, -2: 키 없음. FIFO 정렬 기준으로 사용.
    """

def find_context_caches_with_checksum(checksum: str) -> list[tuple[str, dict]]:
    """이 checksum이 file_checksums 리스트에 포함된 context_cache 엔트리 모두 반환.
    
    반환: [(hash_key, doc), ...]
    SCAN으로 context_cache:* 전체 훑어 각 value의 file_checksums 체크 (§8.5 연쇄 삭제용).
    """
```

**기존 함수 유지**: `save_files_api`, `find_files_api`, `delete_files_api`, `save_context_cache`, `find_context_cache`, `delete_context_cache`, `make_context_cache_key`. 시그니처·동작 변경 없음.

### 7.6 신규: file_manager HTTP 클라이언트

```python
# core/providers.py (신규 항목)
_file_manager_client: httpx.AsyncClient | None = None

def configure_handlers():
    ...
    _file_manager_client = httpx.AsyncClient(
        base_url=settings.file_manager_url,
        timeout=settings.file_manager_timeout,
    )

async def fetch_file_bytes(file_path: str) -> bytes:
    resp = await _file_manager_client.post(
        "/files/fetch",
        json={"file_path": file_path},
    )
    resp.raise_for_status()
    return resp.content
```

### 7.7 신규: `validators.py` (mime 화이트리스트)

```python
# service/app/logic/file_handler/validators.py
from app.core.errors import UnsupportedFileTypeError

SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    # 이미지 (공식)
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
    # 오디오 (공식)
    "audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
    # 문서 (비전 지원)
    "application/pdf",
    # 문서 (텍스트 추출만)
    "text/plain", "text/html", "text/csv", "text/markdown", "text/xml",
    "application/json", "application/xml",
    # 비디오 (보수적)
    "video/mp4", "video/mpeg", "video/mov", "video/avi",
    "video/x-flv", "video/webm", "video/3gpp",
})

def validate_mime_types(file_metas: list[FileMeta]) -> None:
    """Gemini 지원 외 mime 타입이 있으면 UnsupportedFileTypeError."""
    for m in file_metas:
        if m.mime_type not in SUPPORTED_MIME_TYPES:
            raise UnsupportedFileTypeError(
                f"지원하지 않는 파일 타입: mime_type={m.mime_type}, "
                f"file_name={m.file_name}"
            )


MAX_FILE_SIZE: int = 2 * 1024 ** 3  # 2 GB — Gemini Files API 파일당 상한

def validate_file_sizes(file_metas: list[FileMeta]) -> None:
    """2GB 초과 파일은 Files API도 불가 → FileTooLargeError."""
    for m in file_metas:
        if m.file_size > MAX_FILE_SIZE:
            raise FileTooLargeError(
                f"파일 크기가 2GB를 초과합니다: "
                f"file_name={m.file_name}, size={m.file_size}"
            )
```

**호출 위치**: `runner.llm_request_async` / `llm_request_stream_async` **진입 직후** (prepare / MDB 조회 **이전**). `validate_mime_types` → `validate_file_sizes` 순차 호출.

**fail-fast 이유**: 잘못된 입력이면 cache_key 계산·MDB 조회조차 낭비. 가장 앞에서 걸러내 비용·지연 최소화.

**상수 업데이트**: Gemini가 새 포맷 지원하면 이 파일의 `SUPPORTED_MIME_TYPES`만 수정.

## 8. Files API 용량 관리 (Phase B — 후속 phase)

> ⚠️ 이 섹션의 기능은 **후속 phase로 분리**한다. 1차 구현(Phase A — §11)에서는 포함하지 않는다.
> Phase A에서도 `files_api:{checksum}` value에 `size`가 저장되는지만 확인 필요 (§7.5 기존 `save_files_api`가 이미 저장 중).

### 8.1 목적
Gemini Files API 프로젝트 할당 **20GB 상한**을 넘지 않도록 llm-request가 자동 정리.

### 8.2 임계값
- **19 GB** (1GB 안전 마진 — 동시 업로드 경쟁, size_bytes 부정확성 대비)

### 8.3 트리거 시점
- `upload_file()` / `upload_pdf_parts()` 호출 **직전**
- Files API 경로의 C 분기(신규 업로드)에서만 실행

### 8.4 흐름 — Redis SCAN + TTL 기반 FIFO

Gemini 쪽 `list_files()`에 의존하지 않고 **Redis를 시작점**으로 한다. Redis TTL이 동일(47h)이므로 남은 TTL이 적을수록 먼저 저장된 것 = FIFO 효과.

```
upload_file(tmp_path, mime, size) 호출 직전:
  │
  ▼
① 현재 Redis 용량 추정
   for checksum in scan_files_api_keys():
       doc = find_files_api(checksum)
       total += doc["size"]  # value에 이미 저장된 필드
  │
  ▼
② 용량 체크
   if total + required_size <= 19GB: return  # 여유 충분
  │  (공간 부족)
  ▼
③ SCAN + TTL 기반 정렬
   entries = []
   for checksum in scan_files_api_keys():
       ttl = get_ttl_files_api(checksum)
       doc = find_files_api(checksum)
       entries.append((ttl, checksum, doc))
   entries.sort(key=lambda e: e[0])   # TTL 적은 순 = 오래된 것부터
   need_to_free = (total + required_size) - 19GB
  │
  ▼
④ 오래된 것부터 삭제 루프
   freed = 0
   for ttl, checksum, doc in entries:
       if freed >= need_to_free: break
       try:
           # (a) 연쇄 삭제: 이 checksum이 포함된 context_cache 먼저 처리 — §8.5
           for hash_key, cc_doc in find_context_caches_with_checksum(checksum):
               llm_handler.delete_cached_content(cc_doc["cached_content_name"])
               delete_context_cache(hash_key)
           
           # (b) Gemini Files API 파트 삭제
           for uri in doc["uris"]:
               name = _uri_to_name(uri)  # URI → Gemini File name
               llm_handler.delete_file(name)
           
           # (c) Redis files_api 키 삭제
           delete_files_api(checksum)
           
           freed += doc["size"]
       except Exception:
           logger.exception("용량 관리 삭제 실패: %s", checksum); continue
  │
  ▼
   upload_file() 실행
```

### 8.5 context_cache 연쇄 삭제

`files_api:{checksum}`이 삭제되면 **그 checksum이 포함된 `context_cache:{hash_key}`도 같이 삭제**해야 한다. 그렇지 않으면 Gemini context cache가 stale URI 기반으로 남아 참조 시 실패.

**연결 방법**
- Redis `context_cache` 값에는 이미 `file_checksums: list[str]` 저장 중 ([cache_store.py](../service/app/logic/file_handler/cache_store.py) `save_context_cache` 호출부)
- `find_context_caches_with_checksum(checksum)` 이 SCAN으로 매칭되는 엔트리 반환 (§7.5)

**순서**
1. 먼저 **context_cache 삭제** (Gemini `delete_cached_content` + Redis 키)
2. 이어서 **files_api 삭제** (Gemini `delete_file` + Redis 키)

**순서 근거**: context_cache가 남아 있는 상태에서 Files API URI를 먼저 지우면 context cache가 깨진 URI를 참조. 반대로 context_cache 먼저 지우면 그 시점부터 cache 참조는 무효지만 Files API는 직접 사용 가능.

**최적화 (구현 시)**: 용량 관리 트리거 시 `find_context_caches_with_checksum`를 매 checksum마다 호출하면 SCAN O(N²). 실무적으로는 **한 번의 SCAN으로 context_cache 전체를 메모리에 올린 뒤**, 각 파일 삭제 루프에서 메모리 매칭으로 연결 — 단순 최적화.

### 8.6 Split PDF 처리

한 checksum이 복수 URI(`split: True`, `uris: [uri1, uri2, ...]`) 가지는 경우:
- `files_api:{checksum}` 키 하나에 파트 URI들이 묶여 있으므로, **한 번의 삭제로 파트 전체 Gemini 삭제 + Redis 키 삭제** (§8.4 `for uri in doc["uris"]` 루프)
- 부분 삭제 문제 없음

### 8.7 동시성 / 에러 처리

| 상황 | 대응 |
|---|---|
| 여러 agent 동시 용량 체크 → 중복 삭제 | "없다고 가정" 범위 (문서 ① §9). `delete_file` 멱등성(이미 삭제된 건 404)으로 방어 |
| SCAN 중 Redis 연결 실패 | 업로드 중단 + 에러 전파. 용량 관리 실패 상태로 업로드 강행 안 함 |
| `delete_file()` 실패 | 로그 + skip 후 다음 checksum으로. `freed` 누적 안 됨 → 한계까지 돌다 부족 시 `FileUploadError` |
| `delete_cached_content()` 실패 (§8.5 연쇄) | 경고 로그 + Files API 삭제는 계속 진행. context_cache Redis 키는 삭제 시도. stale Gemini context cache는 TTL로 자연 만료 |
| `list_files` 등 Gemini 목록 조회 | **사용하지 않음** (SCAN 기반) → list_files 연동 불필요 |

### 8.8 구현 위치
- `service/app/logic/file_handler/capacity.py` — 신규 모듈
- `ensure_files_api_capacity(required_size: int) -> None` 함수
- `uploader.upload_file` / `upload_pdf_parts` 업로드 호출 직전 invoke
- llm_handler 쪽 `delete_file`, `delete_cached_content` 래핑 확인 필요 (기존에 있는지 / 없으면 추가)
- 테스트: mock 기반으로 SCAN 순서, TTL 정렬, 연쇄 삭제, 실패 케이스

## 9. 에러 처리 (일반)

| 상황 | 대응 |
|---|---|
| 지원하지 않는 mime 타입 (§2.4 화이트리스트 미포함) | `UnsupportedFileTypeError` → HTTP 422. `resolve_files()` 진입 직후 차단하여 LLM 호출 비용 낭비 방지 |
| 개별 파일 2GB 초과 (§4.6) | `FileTooLargeError` → HTTP 422. Gemini Files API 파일당 상한. mime 검증 직후 차단 |
| 오디오 9.5시간 초과 | **사전 체크 불가** — 파일 bytes로 재생 길이 산출 불가. 업로드 후 Gemini가 거부 → 런타임 에러로 상위 전파 |
| Gemini 404 (URI 만료) | 해당 `files_api:{checksum}` 키 삭제 → 재다운로드·재업로드 재시도. URI → checksum 역추적은 불필요 (해당 요청 처리 과정에서 checksum을 이미 알고 있음) |
| `create_context_cache` 실패 | 경고 로그 + Files API URI 그대로 사용해 LLM 호출 진행 (기존 resolver 패턴 유지) |
| `delete_cached_content` 실패 (§8.5 연쇄 삭제) | 경고 로그 + Files API 삭제는 계속 진행. stale Gemini context cache는 TTL로 자연 만료 |
| file_manager 404 (파일 없음) | 문서 ①에 따라 "발생하지 않는다고 가정". 발생 시 503 + future work로 기록 |
| file_manager 타임아웃/연결 실패 | 504/503 + 기존 에러 계층(`FileUploadError` 또는 신규 에러) 통해 라우터에서 HTTP 응답 |
| tempfile 누수 | `with NamedTemporaryFile(delete=True)` 블록으로 자동 삭제. split 내부 tempfile은 기존 uploader의 finally에서 `os.unlink` |
| PDF split 후에도 파트가 50MB 초과 | 기존 splitter의 재귀(`_MAX_DEPTH=3`)로 처리. 초과 시 경고 후 그대로 업로드 → Gemini가 거부하면 에러 전파 |

## 10. Phase 3 폐기 항목 정리

| Phase 3 결정 | 폐기 이유 |
|---|---|
| SDK가 `≤20MB` inline base64로 사전 변환 후 body에 포함 | Service가 bytes 소스(file_manager) 결정 → SDK는 FileMeta만 보냄 |
| SDK가 `>20MB` 파일을 `/v1/files`로 사전 업로드 | `/v1/files` 엔드포인트 폐지 (문서 ①) |
| `_apply_inline_files` / `_apply_cached_files` (runner.py) | `resolve_files()` 단일 진입점으로 통합 |
| 개별 파일 크기 20MB 이진 분기 | **부분 섞기** — 파일별 개별 판정 + 합계 조정 (§4.1) |
| `InlineFileDTO(data_base64)` DTO | FileMeta로 통일 (문서 ①) |
| resolver.py의 `Part.from_file(path)` (로컬 경로 가정) | file_manager HTTP bytes 수급 + `Part.from_bytes` |

## 11. 로드맵 — phase 분리

### Phase A (1차 구현)
핵심 분기 + 캐시 활용. 운영 가능한 최소 구성.

- `classify_files(file_metas, llm_input)` 부분 섞기 분류 함수 — Step 1 비디오만 선제 분리 + Step 2 `_estimate_non_file_bytes` 합산 + Step 3 작은 것부터 정렬 (§4.1)
- inline 분류 파일 처리: bytes → `Part.from_bytes` (tempfile 불필요)
- Files API 분류 파일 처리: A/B/C miss 3단계 분기 + **PDF>50MB 업로드 단계 split 처리**
- `context_cache_ttl` 지정 + 전원 inline이면 Files API로 승급 + 경고 (§4.4)
- `files_api:{checksum}` value에 `size` 저장 확인 (Phase B 용량 관리 대비)
- Gemini 404 fallback (해당 checksum 키 삭제 + 재업로드)
- 테스트 작성

### Phase B (후속 — Files API 용량 관리)
20GB 한도 대응. Phase A 운영 중 용량 압박 확인 후 착수.

- `ensure_files_api_capacity(required_size)` 신규 모듈
- **Redis SCAN + TTL 기반 FIFO** 삭제 (§8.4)
- **context_cache 연쇄 삭제** (§8.5) — `find_context_caches_with_checksum` + `delete_cached_content`
- llm_handler의 `delete_file` / `delete_cached_content` 래핑 확인
- Split PDF 묶음 삭제 (하나의 checksum 키에 파트 URI 모두 포함 — 추가 로직 불필요)
- 테스트: SCAN 순서, TTL 정렬, 연쇄 삭제, 실패 케이스

## 12. TODO

### Phase A

- [ ] `resolver.py` 재설계 — `classify_files` + `_process_inline` + `_process_files_api` + `ResolvedFiles` 구조
- [ ] `runner.py`의 `_apply_inline_files`, `_apply_cached_files`, `_build_server_call_input` 제거
- [ ] `runner.prepare()`에서 FileMeta → checksum 리스트 계산 흐름 연결
- [ ] `runner` 호출 측에서 `inline_parts + uri_parts + 원래 텍스트` 합쳐 `call_input['contents']` 구성, `cached_content`는 별도 필드
- [ ] `core/providers.py`에 `file_manager_client` httpx 싱글톤 추가
- [ ] `fetch_file_bytes(file_path)` 헬퍼
- [ ] `classify_files(file_metas, llm_input)` — 부분 섞기 로직 (§4.1 Step 1: 비디오 선제, Step 2: 합계 한도, Step 3: 작은 것부터 정렬)
- [ ] `_estimate_non_file_bytes(llm_input)` 헬퍼 — `json.dumps` 기반 근사 byte 수 (§4.1 Step 2)
- [ ] 합계 한도 상수: inline 20MB (image/audio 포함) / 50MB (PDF·텍스트만)
- [ ] **PDF 50MB 초과는 classify_files에서 체크하지 않음** — Files API 업로드 단계(§6.2 C 분기, `splitter.split_pdf`)에서 처리
- [ ] inline 분류: `Part.from_bytes` 구현 (tempfile 불필요)
- [ ] Files API 분류: A/B/C 분기 구현 — `files_api_pool`의 checksum만으로 context_cache hash 계산
- [ ] `context_cache_ttl` 지정 + 전원 inline 상황에서 Files API 승급 + 경고 로그 (§4.4)
- [ ] `cache_store.py`: `scan_files_api_keys`, `get_ttl_files_api`, `find_context_caches_with_checksum` 추가 (Phase B 대비)
- [ ] `save_files_api` value에 `size` 필드가 저장되는지 확인 (기존 코드 확인 / 필요 시 추가)
- [ ] Gemini 404 fallback: 해당 `files_api:{checksum}` 키 삭제 → 재다운로드·재업로드
- [ ] `core/errors.py`에 `UnsupportedFileTypeError` (HTTP 422) 추가
- [ ] `core/errors.py`에 `FileTooLargeError` (HTTP 422) 추가
- [ ] `service/app/logic/file_handler/validators.py` 신설 — `SUPPORTED_MIME_TYPES` + `validate_mime_types()` + `MAX_FILE_SIZE` + `validate_file_sizes()`
- [ ] `runner.llm_request_async` / `llm_request_stream_async` 진입 직후 `validate_mime_types` → `validate_file_sizes` 순차 호출 (prepare·MDB 조회 이전, fail-fast)
- [ ] 테스트: 각 분류 케이스(섞인 조합), A/B/C 분기, 404 fallback, mime 화이트리스트 통과/거부, 2GB 경계, 승급 경고
- [ ] **(TBD)** Gemini Files API가 text/* 업로드 지원하는지 확인 — 미지원 시 "텍스트는 inline 전용, 50MB 초과 시 에러" 정책 추가

### Phase B (후속)

- [ ] `service/app/logic/file_handler/capacity.py` 신설
- [ ] `ensure_files_api_capacity(required_size)` 구현 — **Redis SCAN + TTL 기반 FIFO**
- [ ] llm_handler `delete_file` / `delete_cached_content` 래핑 상태 확인 (없으면 래퍼 추가)
- [ ] context_cache 연쇄 삭제 로직 — `find_context_caches_with_checksum` → `delete_cached_content` + `delete_context_cache`
- [ ] `uploader.upload_file` / `upload_pdf_parts` 호출 직전 `ensure_files_api_capacity` invoke
- [ ] 테스트: SCAN 순서, TTL 정렬, 연쇄 삭제, 임계값 경계, 실패 케이스

### 공통
- [ ] `docs/concepts.md`에 "역인덱스" 개념 소절 추가
- [ ] 문서 ①과 상호 링크
