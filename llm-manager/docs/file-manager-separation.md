# file_manager 분리 설계

> Phase 3(llm-request 서버화) 중단. file_manager 패키지를 외부로 분리하는 새 구조로 대체.
> 파일 실체 관리는 별도 패키지(`packages/file_manager`)에 위임하고,
> llm-request는 LLM 응답 캐싱/호출에만 집중한다.

## 1. 배경

Phase 3는 llm-request SDK가 다음을 모두 담당했다:
- 로컬 파일 checksum 계산
- Redis 파일 캐시 조회 (Files API URI 재사용)
- `≤20MB` inline / `>20MB` Files API 업로드 분기
- `/v1/files` 엔드포인트로 큰 파일 서버 전송

운영 과정에서 **파일 실체 관리와 LLM 응답 캐싱이 한 패키지에 묶여 있어** 책임이 과도하게 넓어지는 문제가 발생.
별도 `file_manager` 패키지가 파일 저장/중복 제거를 전담하도록 분리하기로 결정.

이 문서는 **file_manager 외부 분리에 따른 llm-request 인터페이스 변경**만 다룬다.
받은 파일을 Gemini에 어떻게 전달할지(inline vs Files API) 전략은 별도 문서 참조:
`inline-vs-files-api-strategy.md` (작성 예정).

## 2. 설계 범위

| 흐름 | 주체 | 설계 담당 |
|---|---|---|
| ① agent ↔ file_manager | 파일 등록/조회 | **범위 밖** (file_manager 담당자) |
| ② llm-request Service ↔ file_manager | 파일 bytes 요청 | 범위 안 |
| llm-request 내부 개편 (DTO, SDK, 엔드포인트) | - | 범위 안 |

agent 입장에서는 llm-request에 **파일 bytes를 직접 보내는 경로가 사라진다**.
file_manager가 반환한 metadata(FileMeta)만 llm-request에 넘긴다.

## 3. 외부 의존 인터페이스 가정

file_manager 담당자와 협의된 스펙. 실제 인터페이스가 최종 확정되면 맞춰서 수정한다.

### 3.1 FileMeta (file_manager SDK 원산지)

file_manager는 파일마다 metadata 번들을 제공한다. **checksum은 llm-request가 자체 계산**한다(3.2 참고).

> **file_manager 내부 키 구조 (참고)**: file_manager DB는 내부적으로 checksum을 만들지 않고
> `(file_name, file_mtime, file_size)` 3필드 튜플을 키로 metadata를 인덱싱한다.
> llm-request는 이 사실에 직접 의존하지 않지만, 두 시스템의 식별 방식이 다르다는 점을 인지하면 설계 의도가 명확해진다.
> 자세한 캐시 계층 관계는 [inline-vs-files-api-strategy.md §3](inline-vs-files-api-strategy.md) 참고.

```python
# file_manager.sdk.models (가정)
class FileMeta(BaseModel):
    file_name: str
    file_mtime: str        # 타입 TBD — 담당자 협의 후 확정 (문자열 or ns 정수)
    file_size: int
    file_path: str         # file_server 절대경로
    mime_type: str
    extension: str
```

llm-request는 `from file_manager import FileMeta`로 import (원산지 c-1).

**각 필드 용도**

| 필드 | 용도 |
|---|---|
| `file_name`, `file_mtime`, `file_size` | checksum 해싱 입력 (3.2) |
| `file_path` | Service가 `POST /files/fetch`로 bytes 요청 시 사용 |
| `mime_type`, `file_size` | 파일 전달 전략(inline vs Files API) 분기 — 문서 ② 참고 |
| `extension` | 보조 정보 (현재 직접 사용처 없음, 향후 대비 보존) |

**c-1 선택 이유 및 후보 비교**

| 전략 | 구현 | 의존성 | 장점 | 단점 |
|---|---|---|---|---|
| **c-1 (선택)** | file_manager SDK가 정의, llm-request가 import | llm-request → file_manager | 단일 원산지, DRY, 스키마 수정 1회 | 패키지 결합 |
| c-3 | 두 패키지가 각자 동일 필드로 선언 | 없음 | 결합도 낮음 | 수정 동기화 부담, 오타 위험 |

초기 스키마 유동기에는 c-1이 안전 (필드 추가/변경이 잦아도 한쪽만 수정하면 양쪽 반영).

### 3.2 checksum 계산 — llm-request 자체 수행 (메타 해시)

file_manager는 checksum을 제공하지 않는다. llm-request는 `keying.py`를 재사용해
**FileMeta의 3개 필드로 자체 해싱**한다.

```python
# keying.py 신규 함수 (예시)
def make_file_checksum(meta: FileMeta) -> str:
    file_meta = {
        "file_name":  meta.file_name,
        "file_mtime": meta.file_mtime,
        "file_size":  meta.file_size,
    }
    canonical = canonicalize_llm_input(file_meta)
    return make_cache_key(canonical)   # sha256 hex
```

이 checksum은:
- Redis `files_api:{checksum}` / `context_cache:{...}` 키로 사용
- LLM 응답 cache_key 구성 요소 (`keying.py` 기존 로직과 연결)

**후보 비교**

| 방식 | 계산 입력 | 신뢰성 | 비용 | 채택? |
|---|---|---|---|---|
| **메타 해시 (선택)** | `sha256(file_name + file_mtime + file_size)` | 현업 패턴상 충돌 희귀 (가정) | 거의 0 | ✅ |
| 내용 해시 | `sha256(file_bytes)` | 완벽 (이론적으로도) | 2GB 파일 약 4초, 대용량 다수 시 부담 | ❌ (운영 비용) |

**트레이드오프 수용**
- `touch` 등으로 mtime만 바뀌면 cache miss
- 같은 내용이 다른 파일명/mtime이면 중복 저장
- 운영 정책상 감수. 기존 `concepts.md`의 "내용 해싱이 더 안전"이라는 일반론과 다른 선택

### 3.3 file_manager Service — bytes 조회 API

```
POST {file_manager_url}/files/fetch
Body:  { "file_path": "/abs/path/on/file_server.pdf" }
200:   bytes (application/octet-stream)
404:   파일 없음 (현재는 "발생하지 않는다"고 가정, future work)
```

**후보 비교**

| 방식 | 형태 | 장점 | 단점 |
|---|---|---|---|
| B1 | `GET /files?path=/abs/x.pdf` | URL 캐시 가능 | 절대경로 URL encoding 지저분, 특수문자 취약 |
| **B2 (선택)** | `POST /files/fetch {file_path}` | FastAPI 스키마 검증 자연스러움, 특수문자 안전 | GET 캐시 이점 없음 (바이너리라 불필요) |

## 4. 새 아키텍처

```
※ 전제: agent는 이미 file_manager.resolve()를 호출하여 FileMeta 리스트를 보유한 상태.
        agent ↔ file_manager 사이의 흐름 ①은 이 그림에서 생략.

Agent                     llm-request Service                 file_manager Service
┌──────────────┐          ┌────────────────────┐              ┌─────────────────┐
│ file_manager │          │                    │              │                 │
│ SDK.resolve()│─────────→│                    │              │                 │
│              │          │                    │              │                 │
│ FileMeta[]   │          │                    │              │                 │
│  + llm_input │ ─HTTP──→ │ /v1/request        │              │                 │
│              │          │                    │              │                 │
│              │          │ ┌──────────────┐   │              │                 │
│              │          │ │ checksum 계산 │   │              │                 │
│              │          │ │ (keying.py)  │   │              │                 │
│              │          │ └──────────────┘   │              │                 │
│              │          │   ↓                │              │                 │
│              │          │ ┌──────────────┐   │              │                 │
│              │          │ │ MDB 캐시 조회 │   │              │                 │
│              │          │ │ (LLM 응답)    │   │              │                 │
│              │          │ └──────────────┘   │              │                 │
│              │          │   ↓ MISS           │              │                 │
│              │          │ ┌──────────────┐   │              │                 │
│              │          │ │ 파일 처리 전략 │   │              │                 │
│              │          │ │ (문서 ② 참조) │   │ POST         │                 │
│              │          │ │              │   │ /files/fetch │                 │
│              │          │ │ 필요 시 bytes │───┼─────────────→│ file_path 조회  │
│              │          │ │ 요청          │   │              │                 │
│              │          │ │              │←──┼──bytes───────│                 │
│              │          │ └──────────────┘   │              │                 │
│              │          │   ↓                │              │                 │
│              │          │ llm-handler 호출   │              │                 │
│              │←─결과────│                    │              │                 │
└──────────────┘          └────────────────────┘              └─────────────────┘
```

**agent는 bytes를 보내지 않는다**. bytes는 오직 Service ↔ file_manager 구간에서만 흐른다.

## 5. 입력 DTO 변경

### 5.1 기존 (Phase 3)

```python
class LLMRequestBody(BaseModel):
    llm_input: dict
    file_checksums: list[FileChecksumDTO] | None = None   # 큰 파일용
    inline_files: list[InlineFileDTO] | None = None       # 작은 파일 base64
    ...
```

### 5.2 신규

```python
class LLMRequestBody(BaseModel):
    llm_input: dict
    file_metas: list[FileMeta] | None = None              # 통일
    ...
```

- `file_checksums`, `inline_files` 2필드 체계 → `file_metas` 1필드로 통일
- `/v1/done-check`의 `DoneCheckBody`도 동일 변경
- `/v1/fetch`의 `FetchBody`는 `cache_key` 입력이라 영향 없음. 단 응답 meta 포맷은 새 스키마 반영

### 5.3 삭제 대상

- `InlineFileDTO` (base64 직접 전송 경로 제거)
- `FileChecksumDTO` (file_manager의 `FileMeta`로 대체)
- `POST /v1/files` 엔드포인트 (파일 업로드는 file_manager 전담)

**트레이드오프**: 기존 SDK 사용자와의 호환성 포기. Phase 3 SDK는 배포 전이라 실제 피해 없음.

## 6. SDK 얇아짐

### 6.1 제거되는 로직

| 기존 위치 | 기능 | 처리 방안 |
|---|---|---|
| `sdk/llm_request/checksum.py` | 로컬 파일 checksum 계산 | 파일 전체 삭제 (Service의 keying.py가 FileMeta로 계산) |
| `sdk/llm_request/_client.py` | `/v1/files` 업로드 | 메서드 삭제 |
| `sdk/llm_request/_client.py` | Redis 파일 캐시 로컬 조회 | 메서드 삭제 (Server로 이전) |

### 6.2 남는 역할

SDK는 **순수 HTTP 클라이언트**로 축소:
- request body 구성 (`file_metas` 배열 포함)
- `httpx.AsyncClient`로 Service 호출
- 응답 → Pydantic 모델 파싱

### 6.3 "전부 Server" 트레이드오프

본 프로젝트는 SDK가 로컬 자원 접근할 필요가 사라져(file_manager가 전담) "전부 Server" 쪽이 자연스럽다.
다만 감수해야 할 항목들:

| 항목 | 내용 | 본 프로젝트 영향 |
|---|---|---|
| 네트워크 왕복 | HTTP 1~2회 추가 | LLM 호출 지연(초 단위) 대비 무시 가능 |
| 네트워크 지연 | 동일 DC 수 ms, 원격 수십 ms | 동 위 |
| SPOF | Service 다운 시 전 agent 실패 | 고가용(복수 인스턴스) 운영으로 대응. 다른 handler들과 동일 |
| 디버깅 경계 | 스택 트레이스가 HTTP에서 끊김 | 기존 `request_id` 로깅 미들웨어로 추적 가능 |
| 배포 복잡도 | Service를 별도 프로세스로 운영 | kiwoom 환경 이미 handler 3개 운영 중 → 부담 작음 |
| 테스트 부담 | 단위는 mock, 통합은 실제 Service 필요 | 기존 handler 테스트 패턴 답습 |

**대안 옵션 비교**

| 방식 | 설명 | 판단 |
|---|---|---|
| 전부 라이브러리 | agent가 Redis/MDB/llm-handler 직접 연결 | 시크릿 노출, 의존성 복잡 → ❌ |
| 일부만 SDK (중간) | 로컬 파일 접근만 SDK, 나머지 Server | 새 구조에선 **SDK가 로컬 파일을 볼 일이 없음** → 중간의 명분 소멸 |
| **전부 Server (선택)** | SDK는 HTTP 래퍼 | 일관성, 시크릿 집중, kiwoom 환경과 동일 패턴 |

**전제 조건**: Service 운영(가용성, 로깅, 장애 복구)이 제대로 돼야 agent들이 안정적. 기존 handler 운영 표준을 따른다.

## 7. config.py 변경

```python
class Settings(BaseSettings):
    ...
    file_manager_url: str = "http://localhost:8102"   # 신규 추가 (포트는 가정)
```

환경변수 `FILE_MANAGER_URL`로 덮어쓰기 가능.

## 8. 영향받는 코드 목록

### 8.1 삭제

- `service/app/routers/request.py`: `POST /v1/files` 핸들러
- `service/app/models/requests.py`: `InlineFileDTO`, `FileChecksumDTO`
- `sdk/llm_request/checksum.py`: 전체 파일
- `sdk/llm_request/_client.py`: `upload_files()` 메서드

### 8.2 수정

- `service/app/models/requests.py`: `LLMRequestBody`, `DoneCheckBody`의 파일 필드를 `file_metas: list[FileMeta]`로 교체
- `service/app/config.py`: `file_manager_url`, `file_manager_timeout` 추가
- `service/app/core/providers.py`: file_manager HTTP 클라이언트 초기화 (httpx.AsyncClient 싱글톤) + `fetch_file_bytes(file_path)` 헬퍼
- `service/app/core/errors.py`: **`UnsupportedFileTypeError` (HTTP 422), `FileTooLargeError` (HTTP 422) 신규 추가** (상세: 문서 ② §4.5·§4.6)
- `service/app/logic/keying.py`: **`make_file_checksum(meta: FileMeta)` 신규 함수 추가** — FileMeta 3필드로 개별 파일 checksum 생성. 기존 cache_key 구성 로직은 이 checksum을 소비하도록 연결
- **신규: `service/app/logic/file_handler/validators.py`** — mime 화이트리스트 + 2GB 초과 검증 (`SUPPORTED_MIME_TYPES`, `validate_mime_types`, `MAX_FILE_SIZE`, `validate_file_sizes`). 상세: 문서 ② §2.4·§4.5·§4.6·§7.7
- **`service/app/logic/file_handler/resolver.py` 재설계** — `classify_files(file_metas, llm_input)` + `_estimate_non_file_bytes(llm_input)` + `ResolvedFiles` 반환 구조. 상세: 문서 ② §4.1·§7.2
- `service/app/logic/runner.py`: `_apply_inline_files`/`_apply_cached_files` 제거, 파일 bytes 획득 경로를 file_manager로 전환, FileMeta 입력 받아 keying 호출. `llm_request_async` 진입 직후 `validate_mime_types` → `validate_file_sizes` fail-fast 호출
- `sdk/llm_request/__init__.py`: public API의 `file_paths` 인자를 `file_metas: list[FileMeta]`로 교체
- `sdk/llm_request/_client.py`: request body 구성 변경
- 테스트: `file_paths` / `file_checksums` 사용 테스트는 `file_metas` 기반으로 재작성, validators / classify_files 케이스 추가

### 8.3 유지

- `service/app/core/errors.py`
- `service/app/logic/done_check.py`, `store.py`, `meta.py`
- `service/app/logic/file_handler/` (내부 로직은 문서 ②에서 개편)
- `service/app/logic/orchestrator.py` (배치 처리 구조 유지, 입력만 변경)

## 9. 에러 처리 (범위 축소)

| 상황 | 대응 |
|---|---|
| 지원하지 않는 mime 타입 | `UnsupportedFileTypeError` → HTTP 422. `llm_request_async` 진입 직후 fail-fast (상세: 문서 ② §4.5) |
| 개별 파일 2GB 초과 | `FileTooLargeError` → HTTP 422. mime 검증 직후 fail-fast (상세: 문서 ② §4.6) |
| 한도 초과로 Gemini가 inline 요청 거부 | 기본적으로 §① 범위 밖. ② §4.1 합계 계산으로 사전 방어. 그럼에도 발생 시 런타임 에러 전파 |
| file_manager 접속 실패 | 503 + "file_manager unavailable" |
| file_manager 404 (파일 없음) | 현재 설계에서는 **발생하지 않는다고 가정** |
| file_manager 타임아웃 | 504 + "file_manager timeout" |

**"없다고 가정"의 의미**
- 파일이 file_manager MDB엔 있지만 file_server 실체가 사라진 경우는 운영상 배제
- 동시성으로 인한 정합성 깨짐도 같은 가정 범주
- 이후 운영 중 실제 발생이 확인되면 복구 전략을 별도 task로 설계
- README의 "Future Work" 섹션에 기록 예정

## 10. Phase 3 결정사항 중 폐기되는 것

| Phase 3 결정 | 폐기 이유 |
|---|---|
| SDK에서 checksum 계산 | file_manager가 metadata만 제공 → Service의 keying.py가 계산 |
| SDK에서 Redis 파일 캐시 조회 | Server로 이전 (시크릿 집중 관리) |
| `≤20MB` inline / `>20MB` Files API (개별 파일 크기 분기) | 요청 전체 크기 + 파일 타입 기준으로 재검토 (문서 ②) |
| `/v1/files` 엔드포인트 | file_manager가 파일 업로드 전담 |
| 내용 해시 (`sha256(file_bytes)`) | 메타 해시로 교체 |

## 11. TODO (구현 시)

- [ ] `FileMeta` import 경로 확정 (file_manager SDK 배포 전까지 stub으로 개발 진행)
- [ ] `FileMeta.file_mtime` 타입 확정 (문자열 vs ns 정수) — 담당자 협의
- [ ] `config.py`에 `file_manager_url`, `file_manager_timeout` 추가
- [ ] `providers.py`에 file_manager HTTP 클라이언트 싱글톤 등록 + `fetch_file_bytes(file_path)` 헬퍼
- [ ] `LLMRequestBody`, `DoneCheckBody` → `file_metas: list[FileMeta]` 필드로 교체
- [ ] `InlineFileDTO`, `FileChecksumDTO` 삭제
- [ ] `POST /v1/files` 라우터 및 핸들러 삭제
- [ ] `sdk/llm_request/checksum.py` 삭제
- [ ] `sdk/llm_request/_client.py`의 `upload_files()` 삭제
- [ ] SDK public API(`llm_request_async` 등)의 인자 `file_paths` → `file_metas`로 변경
- [ ] `keying.py`에 `make_file_checksum(FileMeta)` 추가
- [ ] cache_key 구성 로직이 `file_metas`에서 checksum을 추출하도록 연결
- [ ] `core/errors.py`에 `UnsupportedFileTypeError`, `FileTooLargeError` (HTTP 422) 추가
- [ ] 신규 `file_handler/validators.py` — `SUPPORTED_MIME_TYPES` + `validate_mime_types` + `MAX_FILE_SIZE` + `validate_file_sizes` (상세: 문서 ② §7.7)
- [ ] 신규 `file_handler/resolver.py`의 `classify_files(file_metas, llm_input)` + `_estimate_non_file_bytes(llm_input)` (상세: 문서 ② §4.1)
- [ ] `runner.py`에서 `llm_request_async` / `llm_request_stream_async` 진입 직후 `validate_mime_types` → `validate_file_sizes` fail-fast 호출 (prepare·MDB 조회 이전)
- [ ] 테스트 전면 수정 (`file_paths`/`file_checksums` → `file_metas`, validators, classify_files 케이스 추가)
- [ ] README의 Future Work에 file_manager 404/동시성/정합성 항목 추가
- [ ] 문서 ② (`inline-vs-files-api-strategy.md`) 작성 완료 후 이 문서와 상호 링크
