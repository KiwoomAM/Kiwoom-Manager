# llm-request 디버깅 가이드

요청이 SDK에서 서비스로 들어가 LLM 호출 → MDB 저장 → 응답까지 오는 동안 **어디서 어떤 로그와 예외가 나올 수 있는지** 순서대로 정리한 설명서.

각 항목은 다음 템플릿을 따른다:

```markdown
> `파일:라인` [LEVEL] `함수명()`
> ```
> 메시지 패턴
> ```
- **언제**: 트리거 조건
- **의미**: 정상 / 경고 / 에러
- **원인** (에러일 때): 후보
- **확인할 것**: 명령어, 환경변수, 선행 로그
```

마커 의미
- 🟢 정상 흐름 로그
- 🟡 경고 — 무해하거나 자동 복구되지만 자주 보이면 점검
- 🔴 에러 — 요청 실패 또는 서비스 장애로 이어짐

---

## 1. 요청 흐름 한눈에 보기

```
[SDK]                          [Service]
llm_request_async()
  │ checksum 계산
  │ Redis 파일 캐시 조회 (대용량만)
  │ inline / files API 분기
  └──POST──→ /v1/request
                │
                ▼
          request_logging_middleware          ← main.py
                │
                ▼
          routers/request.py                  ← 얇은 변환
                │
                ▼
          llm_request_async                   ← runner.py (오케스트레이션)
                │ 1) prepare()                 → keying.py (cache_key 생성)
                │                              → done_check.py (MDB 조회)
                │                              → standby 획득 (dedup.py)
                │ 2) resolve_files()           → file_processing/resolver.py
                │                               ├ validators.py
                │                               ├ uploader.py + splitter.py
                │                               └ cache_store.py (Redis)
                │ 3) aio_call()                → providers.py (llm-handler)
                │ 4) finalize()                → store.py (MDB 저장)
                └──response──→ SDK
```

---

## 2. 단계별 상세

### 2.1 SDK 클라이언트 호출

**역할**: httpx로 서비스에 HTTP 요청을 보내고, 실패 시 예외를 던짐.
**관련 파일**: [_client.py](sdk/llm_request/_client.py), [exceptions.py](sdk/llm_request/exceptions.py)

#### 🔴 서비스 연결 실패 (단발 요청)

> `_client.py:45` [RAISE] `_request()`
> ```python
> raise ServiceConnectionError(f"llm-request 서비스 연결 실패: {e}")
> ```

- **언제**: httpx `ConnectError` 등 네트워크 단절
- **의미**: 서비스가 기동되지 않았거나 네트워크가 끊김
- **원인 후보**: 서비스 다운, 포트(8101) 오설정, 방화벽, 호스트명 오설정
- **확인할 것**: `curl http://<host>:8101/health`, 서비스 stdout 로그, SDK 생성 시 `base_url`

#### 🔴 서비스 요청 타임아웃

> `_client.py:47` [RAISE] `_request()`
> ```python
> raise ServiceConnectionError(f"llm-request 서비스 요청 타임아웃: {e}")
> ```

- **언제**: httpx `TimeoutException`
- **의미**: 서비스가 응답을 제 시간에 돌려주지 못함 (LLM 호출 지연 등)
- **확인할 것**: `timings={"http_timeout": 1200}`으로 타임아웃 상향, 서비스 쪽 장시간 로그, LLM provider 지연

#### 🔴 서비스 에러 응답

> `_client.py:57` [RAISE] `_request()`
> ```python
> raise ServiceError(error_msg, status_code=response.status_code, error_type=error_type)
> ```

- **언제**: HTTP 4xx/5xx 응답. body에서 `error`, `error_type` 필드 추출
- **의미**: 서비스가 예외를 정상 응답으로 반환한 상태. `error_type`이 key
- **확인할 것**: `error_type` → §3 예외 레퍼런스 표 매핑. 서비스 로그에서 같은 `request_id`의 `미처리 예외` 또는 핸들러 로그

#### 🔴 스트림 요청 실패 (4종)

> `_client.py:140, 142, 148, 154` [RAISE] `request_stream()`
> ```python
> raise ServiceConnectionError(f"llm-request 서비스 연결 실패: {e}")      # 140
> raise ServiceConnectionError(f"llm-request 서비스 요청 타임아웃: {e}")  # 142
> err = ServiceError(..., error_type=err_body.get("error_type"))         # 148 — body 있음 (raise 는 L156)
> err = ServiceError(resp.text, status_code=resp.status_code)            # 154 — body 없음 (raise 는 L156)
> ```

- **언제**: 스트림 엔드포인트(`/v1/request/stream`)에서 동일 패턴. Fix G 이후 `httpx.stream()` context 를 수동으로 `__aenter__` / `__aexit__` 관리하기 때문에 연결/타임아웃 에러는 `__aenter__` 시점에 잡아 raise
- **의미**: 단발 요청과 같음. body 있는 4xx/5xx 는 `err` 객체로 만들어 `__aexit__` 후 L156 에서 `raise err`

---

### 2.2 서비스 진입 (main.py / middleware)

**역할**: 서비스 lifecycle 관리 + 모든 요청에 로깅 미들웨어 부여.
**관련 파일**: [main.py](service/app/main.py)

#### 🟢 기동/종료

> `main.py:36, 41` [INFO] `lifespan()`
> ```
> llm-request service 시작
> llm-request service 종료
> ```

- **언제**: 서비스 프로세스 기동/종료 시 각 한 번
- **주의**: 기동 직전 [providers.py](service/app/core/providers.py)의 Redis 연결 확인이 실패하면 이 로그 전에 터짐 (§2.7 참조). 종료 직전 (L39-40) 싱글톤 `FileManagerHandler().close()` 로 httpx 커넥션 풀 정리

#### 🟢 요청 단위 로그

> `main.py:104-111` [INFO] `request_logging_middleware()`
> ```
> %s %s → %s (%.0fms) [%s]
> # 예: POST /v1/request → 200 (1240ms) [req-abc123]
> ```

- **언제**: 모든 HTTP 요청 종료 시 (성공/실패 공통)
- **확인할 것**: 응답 status, latency, `request_id`. 같은 `request_id`로 다른 로그 찾기

#### 🔴 미처리 예외

> `main.py:93` [EXCEPTION] `request_logging_middleware()`
> ```
> 미처리 예외: request_id=%s
> # traceback 동반 (exc_info=True)
> ```

- **언제**: 라우터/로직 어디선가 `LLMRequestServiceError`로 감싸지지 않은 예외가 올라옴
- **의미**: 진짜 버그 신호. 일반 에러는 errors.py의 클래스로 감싸져 4xx/5xx로 내려가야 함
- **확인할 것**: 같은 `request_id`의 선행 로그, traceback

---

### 2.3 캐시 키 생성 (keying.py)

**역할**: `llm_input`과 부속 파라미터를 결정적 JSON으로 정규화 → sha256으로 키 산출.
**관련 파일**: [keying.py](service/app/logic/keying.py)

#### 🟡 정규화 fallback 시도

> `keying.py:177-181` [WARNING] `canonicalize_llm_input()`
> ```
> 캐시 키 정규화 실패: llm_input type=%s — str() fallback 시도
> # exc_info=True
> ```

- **언제**: 내부 `_normalize()`가 예외 (직렬화 불가 객체 등)
- **의미**: 1차 실패 → `str()` fallback 시도. 자주 뜨면 입력 타입 리뷰 필요

#### 🔴 캐시 키 정규화 완전 실패

> `keying.py:188-192` [ERROR] `canonicalize_llm_input()`
> ```
> 캐시 키 정규화 완전 실패: llm_input type=%s, keys=%s
> # exc_info=True
> ```
> `keying.py:193-195` [RAISE]
> ```python
> raise ValueError(f"캐시 키 정규화 완전 실패: llm_input type={type(llm_input).__name__}")
> ```

- **언제**: str() fallback까지 실패
- **의미**: 요청이 처리 불가. 호출부 입력 타입 문제
- **확인할 것**: `llm_input`의 타입/키 구조, `_normalize()` 지원 타입

#### 🟡 debug=True일 때 빈/유효하지 않은 canonical

> `keying.py:234` [WARNING] `make_cache_key()`
> ```
> 캐시 키 생성: 값이 비어있거나 유효하지 않습니다. len=%d
> ```

- **언제**: `debug=True`로 호출 + canonical_json이 비어있거나 이상함
- **확인할 것**: 디버그 모드에서만 뜸. 프로덕션 무관

---

### 2.4 dedup / standby

**역할**: 동일 키로 동시 요청이 몰릴 때 leader 하나만 실제 작업, 나머지는 대기 (Cache Stampede 방지).
**관련 파일**: [dedup.py](service/app/logic/file_processing/dedup.py), [runner.py](service/app/logic/runner.py)

#### 🔴 Redis 미연결

> `dedup.py:70, 106` [RAISE] `try_acquire_standby()`, `wait_for_standby_release()`
> ```python
> raise RuntimeError("Redis 미연결 — standby 불가")
> ```

- **언제**: Redis 클라이언트가 `None`
- **의미**: providers.py에서 기동을 막고 있어야 함 (§2.7). 여기 도달 = 기동 후 Redis 단절
- **확인할 것**: `redis-cli ping`, `REDIS_URL`, 서비스 기동 로그

#### 🔴 알 수 없는 standby prefix

> `dedup.py:48` [RAISE] `_make_standby_key()`
> ```python
> raise ValueError(f"Unknown standby prefix: {prefix}")
> ```

- **언제**: prefix가 허용 목록 밖. 정상 흐름에선 발생 안 함 = 코드 버그

#### 🟡 standby 대기 timeout

> `dedup.py:133-138` [WARNING] `wait_for_standby_release()`
> ```
> standby 대기 timeout(%d초) 초과 — 호출자가 작업 시작. standby_key=%s,
> 중복 발생 가능. 빈도 높으면 standby_ttl_seconds 늘리세요.
> ```

- **언제**: leader가 TTL 안에 끝내지 못해 waiter가 대기 포기
- **의미**: waiter가 직접 작업 수행 (중복 작업 가능). 동일 현상의 상위 버전이 [runner.py:482, 624](service/app/logic/runner.py#L482), [resolver.py:364, 406](service/app/logic/file_processing/resolver.py#L364)에서 재등장
- **확인할 것**: 빈도 많으면 `standby_ttl_seconds` 상향, LLM/업로드 실제 지연 여부

#### 🟡 release 실패 (무시)

> `dedup.py:154-160` [EXCEPTION] `release_standby()`
> ```
> standby release 실패 (무시, TTL로 자연 정리): prefix=%s, key=%s
> ```

- **의미**: 무해. 키는 TTL 만료로 자연 삭제
- **확인할 것**: 대량 반복 시 Redis 상태

---

### 2.5 파일 검증 (validators.py)

**역할**: 업로드 가능 여부 사전 검증. Fix C 이후 validate → register 순서로 실행되어 미지원/초과 파일은 file-manager 업로드 전에 차단.
**관련 파일**: [validators.py](service/app/logic/file_processing/validators.py)

#### 🔴 지원하지 않는 mime 타입

> `validators.py:37-40` [RAISE] `validate_mime_types()`
> ```python
> raise UnsupportedFileTypeError(
>     f"지원하지 않는 파일 타입: mime_type={fi.mime_type}, file_name={fi.file_name}"
> )
> ```

- **언제**: 파일 메타 중 하나가 Gemini 허용 mime 외
- **응답**: 422, `error_type="unsupported_file_type"`

#### 🔴 파일 2GB 초과

> `validators.py:52-55` [RAISE] `validate_file_sizes()`
> ```python
> raise FileTooLargeError(
>     f"파일 크기가 2GB를 초과합니다: file_name={fi.file_name}, size={fi.file_size}"
> )
> ```

- **응답**: 422, `error_type="file_too_large"`

---

### 2.6 파일 처리 — Files API / Context Cache

**역할**: 파일 크기/개수에 따라 inline / Files API / split / context cache 분기.
**관련 파일**: [resolver.py](service/app/logic/file_processing/resolver.py), [uploader.py](service/app/logic/file_processing/uploader.py), [splitter.py](service/app/logic/file_processing/splitter.py), [cache_store.py](service/app/logic/file_processing/cache_store.py)

#### 🟢 파일별 경로 결정 로그

> `resolver.py` [INFO] — 파일 1개당 1줄 출력
> ```
> [route] file=image.png mode=inline
> [route] file=medium.pdf mode=files_api source=cached uris=1
> [route] file=large.pdf mode=files_api source=live parts=2 duration_ms=12000
> [route] mode=context_cache source=live name=cachedContents/abc
> [route] mode=context_cache source=cached name=cachedContents/abc
> [route] mode=context_cache source=none reason=no_files_api_files
> ```

- `mode=inline` — `_process_inline()`: inline 처리 파일마다 출력
- `mode=files_api source=cached` — `_reuse_files_api_uris()`: Redis URI 재사용. "왜 빨리 끝났지?" 의문 즉시 해소
- `mode=files_api source=live` — `_upload_and_cache_file()`: 신규 업로드 완료. `parts>1`이면 PDF 분할 경로, `duration_ms`로 업로드 소요 시간 확인
- `mode=context_cache source=live` — `_create_and_save_context_cache()`: Context Cache 신규 생성
- `mode=context_cache source=cached` — `_process_files_api()` 분기 A(Redis hit) 또는 standby 대기 후 hit
- `mode=context_cache source=none` — `context_cache_ttl` 지정됐지만 files_api 파일 없음 (조건 미충족)

#### 🔴 Files API 업로드 진입 가드 — `fi.data` 회수 후 재사용

> `resolver.py:181-187` [RAISE] `_upload_and_cache_file()`
> ```python
> if not fi.data:
>     raise FileUploadError(
>         f"Files API 업로드 실패: bytes 가 이미 회수된 FileInput 재사용 "
>         f"(file_name={fi.file_name}). 자동 복구 미지원 — 호출자가 새 요청으로 재시도 필요"
>     )
> ```

- **응답**: 502, `error_type="file"`
- **언제**: 메모리 정리 후(`fi.data = b""`) 동일 FileInput 객체로 재업로드 시도. 거의 유일한 발생 경로는 Gemini 404 자동 복구 흐름
- **확인할 것**: 진입 가드는 의도된 차단 — silent corruption 방지. 사용자는 새 요청으로 재시도. 상세 [`docs/16.memory-cleanup-plan.md`](docs/16.memory-cleanup-plan.md) §6

#### 🔴 Files API 업로드 실패

> `resolver.py:204-209` [EXCEPTION → RAISE]
> ```
> Files API 업로드 실패: file_name=%s, mime=%s
> ```
> ```python
> raise FileUploadError(f"Files API 업로드 실패: file_name={fi.file_name}, {e}") from e
> ```

- **응답**: 502, `error_type="file"`
- **원인 후보**: Gemini 인증, 파일 손상, 네트워크, provider rate limit
- **확인할 것**: llm-handler traceback, 해당 파일 실제 크기/mime

#### 🔴 Redis files_api 저장 실패

> `resolver.py:226-228` [EXCEPTION → RAISE]
> ```python
> raise CacheError(f"Redis files_api 저장 실패: {e}") from e
> ```

- **응답**: 502, `error_type="cache"`

#### 🔴 Context cache 생성 실패

> `resolver.py:280-281` [EXCEPTION → RAISE]
> ```python
> raise FileUploadError(f"Context cache 생성 실패: {e}") from e
> ```

- **원인 후보**: Gemini API 오류, 인자 불일치. model=None 인 경우는 Fix K 에 의해 resolver 에서 `ValueError` 로 사전 차단됨

#### 🔴 Redis context_cache 저장/조회 실패

> `resolver.py:279-280, 322-323, 337-338` [EXCEPTION → RAISE]
> ```python
> raise CacheError(f"Redis context_cache 저장 실패: {e}") from e
> raise CacheError(f"Redis context_cache 조회 실패: {e}") from e
> raise CacheError(f"Redis files_api 조회 실패: {e}") from e
> ```

- **응답**: 502, `error_type="cache"`
- **확인할 것**: Redis 상태, 키 prefix 구조

#### 🟡 files_api / context_cache standby timeout (Y' Hybrid)

> `resolver.py:364, 406` [WARNING] `_process_files_api()`
> ```
> files_api standby timeout → 자기가 업로드 (중복 가능): checksum=%s
> context_cache standby timeout → 자기가 생성 (중복 가능): hash_key=%s
> ```

- **의미**: §2.4 설명과 동일. 이 파일 단계에서 발생
- **확인할 것**: 업로드/context_cache가 실제로 오래 걸리는지, TTL 상향 검토

#### 🟡 Gemini 404 — Redis invalidate 후 진입 가드 raise

> `resolver.py:477-479` [WARNING] `resolve_files_after_404()`
> ```
> Gemini 404 자동 복구: Redis invalidate 후 재업로드 (checksums=%d)
> ```

- **언제**: LLM 호출 중 Gemini가 Files API URI 404 반환 → runner가 이 함수 호출
- **의미**: Redis 캐시는 invalidate 되지만, 메모리 정리(`fi.data = b""`)로 재업로드는 진입 가드에서 `FileUploadError` raise. 자동 복구 폐지 — 호출자는 새 요청으로 재시도. 상세 [`docs/16.memory-cleanup-plan.md`](docs/16.memory-cleanup-plan.md) §6
- **확인할 것**: Redis TTL < Gemini TTL 이슈가 반복되면 `FILES_API_TTL_FLOOR` 조정

#### 🟡 404 복구 중 Redis 삭제 실패 (무시)

> `resolver.py:486-490, 496-500` [EXCEPTION]
> ```
> Redis files_api 삭제 실패 (계속 진행): checksum=%s
> Redis context_cache 삭제 실패 (계속 진행): hash_key=%s
> ```

- **의미**: 복구 흐름 유지. 무해

#### 🟡 context_cache_ttl로 전 파일 Files API 승급

> `resolver.py:524-527` [WARNING] `resolve_files()`
> ```
> context_cache_ttl=%d 지정 — 모든 파일 Files API 경로로 승급
> ```

- **언제**: `context_cache_ttl` 옵션 사용 시 inline 파일도 Files API로 업로드
- **의미**: 의도적. 초과 토큰 비용을 context cache로 상쇄하려는 용도

#### 🟢 PDF 파트 업로드 진행도

> `uploader.py:49` [INFO] `upload_pdf_parts()`
> ```
> PDF 파트 업로드 %d/%d: %s
> ```

#### 🟡 create_cache 재시도

> `uploader.py:101-108` [WARNING] `create_context_cache()`
> ```
> create_cache 실패 (시도 %d/%d): %s → %d초 후 재시도
> ```

- **언제**: Gemini `create_cache` 일시 오류
- **확인할 것**: 재시도 후에도 실패하면 위 "Context cache 생성 실패"로 이어짐

#### 🟡 PDF splitter 경고들

> `splitter.py:76-82, 106-111, 144` [WARNING]
> ```
> 재분할 한도(%d) 초과: %s (%.1fMB) — 현재 크기로 진행
> 페이지 %d의 추정 크기(%.1fMB)가 목표(%dMB)를 초과합니다
> 파트 %d이 %.1fMB로 초과 → 재분할
> ```

- **의미**: 분할이 이상적인 크기에 수렴하지 못함. 대부분 통과
- **확인할 것**: 동일 파일에서 재분할 한도 초과가 계속되면 입력 PDF 구조 확인

#### 🟢 분할/병합 진행도

> `splitter.py:134-141, 181-186` [INFO]
> ```
> 분할 %d/%d: p.%d-%d (%.1fMB)
> 병합: 파트 %d+%d → %.1fMB
> ```

#### 🔴 split_pdf 실패 — 임시 파일 정리

> `splitter.py:194-205` [EXCEPTION + WARNING]
> ```
> split_pdf 실패 — 임시 파일 정리 (created=%d, result=%d)
> 임시 파일 정리 실패: %s
> ```

- **언제**: PDF 분할/병합 도중 `part_doc.save()` / `doc_m.save()` / 재귀 호출 등에서 raise
- **의미**: 만들어진 임시 파일을 일괄 unlink 후 raise — leak 0. `FileNotFoundError`(set 합집합 특성상 정상)는 silent, 그 외 `OSError`(디스크 풀 / readonly fs / 권한)는 `logger.warning` 으로 신호 보존
- **확인할 것**: WARNING 이 동반되면 `/tmp` 상태(디스크 풀 / 권한 / readonly fs) 점검

#### 🟡 FileRef expiration 파싱 실패 / floor 적용

> `cache_store.py:138, 147-152` [WARNING] `compute_files_api_ttl()`
> ```
> FileRef.expiration_time 파싱 실패: %s — 폴백 TTL 사용
> compute_files_api_ttl: 잔여시간 %d초 < floor(%d초) — floor 적용.
> Gemini 만료 임박, 404 자동 복구 경로 가동 가능성.
> ```

- **의미**: Redis TTL이 Gemini 실제 만료 시각과 어긋남 → 404 경로 잠재 가능성
- **확인할 것**: 자주 뜨면 llm-handler의 expiration_time 포맷 확인

---

### 2.7 Provider / Redis 기동 (providers.py)

**관련 파일**: [providers.py](service/app/core/providers.py)

#### 🔴 Redis 연결 실패 (기동 차단)

> `providers.py:26-29` [RAISE] `_ensure_redis_connected()`
> ```python
> raise RuntimeError(
>     "Redis 연결 실패 — llm-request를 시작할 수 없습니다. "
>     "REDIS_URL 설정 및 Redis 서버 상태를 확인하세요."
> )
> ```

- **언제**: 서비스 기동 시 Redis ping 실패
- **의미**: 서비스 기동 자체가 중단. main.py:36 `"llm-request service 시작"` 로그도 못 나옴
- **확인할 것**: `REDIS_URL`, Redis 서버 상태

#### 🟢 startup standby flush

> `providers.py:50` [INFO] `_flush_stale_standbys()`
> ```
> startup standby flush: %d stale keys 정리
> ```

- **의미**: 이전 실행의 stale standby 키 정리. 정상

---

### 2.8 LLM 호출 (runner.py)

**역할**: prepare → files → call → finalize의 실제 실행. Fix G 이후 `llm_request_stream_async` 는 `(AsyncIterator[StreamChunk], meta)` 튜플을 반환.
**관련 파일**: [runner.py](service/app/logic/runner.py)

#### 🟢 agent_llm dedup 역할 로그

> `runner.py` [INFO/WARNING] `llm_request_async()`, `llm_request_stream_async()`
> ```
> [dedup] role=leader key=abc12345
> [dedup] role=waiter wait_ms=2134 leader_completed=true
> [dedup] role=standby_timeout wait_ms=10000
> ```

- `role=leader` — standby 획득. 이 요청이 실제 LLM 호출 담당
- `role=waiter` — leader 완료 후 MDB에서 결과 수신. `wait_ms`로 대기 시간 확인
- `role=standby_timeout` — leader가 TTL 안에 끝내지 못해 자기가 직접 LLM 호출 (중복 가능)

#### 🟡 캐시 결과 역직렬화 실패 (prepare / standby 대기 중)

> `runner.py:125-129` [WARNING] `prepare()`
> ```
> 캐시 결과 역직렬화 실패: cache_key=%s — 캐시 무시하고 LLM 재호출
> ```
> `runner.py:371-375` [WARNING] `_recheck_agent_llm_cache()`
> ```
> standby 대기 중 캐시 역직렬화 실패: cache_key=%s
> ```

- **의미**: MDB에서 꺼낸 캐시 문서가 현재 스키마와 호환 안 됨 → LLM 재호출로 우회
- **확인할 것**: 스키마 변경 후 옛 문서 섞여있음. 빈번하면 마이그레이션 검토

#### 🟢 MDB 저장 완료

> `runner.py:216-220` [INFO] `finalize()`
> ```
> MDB 저장 완료: database=%s, collection=%s, cache_key=%s
> ```

#### 🔴 MDB 저장 실패 (흐름은 계속)

> `runner.py:223-228` [EXCEPTION] `finalize()`
> ```
> MDB 저장 실패: database=%s, collection=%s, cache_key=%s
> ```

- **의미**: 예외 로그만 남기고 **요청은 성공 응답**. 사용자는 결과를 받음 (캐시만 누락)
- **확인할 것**: MongoDB 상태. 자주 뜨면 다음 호출에서 cache miss 반복

#### 🟡 스트림 청크 0개

> `runner.py:247-252` [WARNING] `_finalize_stream()`
> ```
> 스트림 완료 시 청크가 0개: cache_key=%s — 빈 결과는 저장하지 않음
> ```

- **의미**: LLM이 빈 스트림 반환. 캐시 미저장 (의도적)
- **확인할 것**: provider 쪽 이상, 프롬프트 필터링

#### 🔴 스트림 결과 저장 실패 (흐름은 계속)

> `runner.py:275-279` [EXCEPTION] `_finalize_stream()`
> ```
> 스트림 결과 저장 실패: cache_key=%s — 캐시 미저장
> ```

#### 🟡 Gemini 404 감지 — Redis invalidate 후 진입 가드 raise

> `runner.py:416` [WARNING] `_do_llm_call()`
> ```
> Gemini 404 감지 — 자동 복구 시도: %s
> ```
> `runner.py:552` [WARNING] `_do_llm_stream()`
> ```
> Gemini 404 감지 — stream 자동 복구 시도: %s
> ```

- **의미**: `resolve_files_after_404()` 호출되어 Redis invalidate 까지 진행되지만, 메모리 정리(`fi.data = b""`) 이후 재업로드는 진입 가드에서 `FileUploadError` raise. 자동 복구 폐지 — 호출자가 새 요청으로 재시도 (상세 [`docs/16.memory-cleanup-plan.md`](docs/16.memory-cleanup-plan.md) §6)
- **확인할 것**: 반복 발생 시 `FILES_API_TTL_FLOOR` 조정 (Redis TTL < Gemini TTL 어긋남이 원인)

#### 🟡 스트림 중 비-404 예외 — partial 응답 폐기

> `runner.py:572` [WARNING] `_do_llm_stream()`
> ```
> 스트림 중 비-404 예외 — partial 응답 폐기, 캐시 미저장: %s
> ```

- **언제**: stream 도중 rate limit / 네트워크 단절 / provider 오류 등 (404 외)
- **의미**: 사용자에게는 partial chunk 가 이미 yield 됐으나 불완전 응답을 캐시에 남기지 않기 위해 finalize 호출하지 않음. 동일 요청 재호출 시 LLM 재과금
- **확인할 것**: 자주 뜨면 provider 안정성 / 네트워크 상태 점검

#### 🟡 agent_llm standby timeout

> `runner.py:482, 624` [WARNING] `llm_request_async()`, `llm_request_stream_async()`
> ```
> [dedup] role=standby_timeout wait_ms=%d
> ```

- **의미**: §2.4와 같은 Y' Hybrid. LLM 호출 단계에서 발생
- **확인할 것**: LLM 실제 지연, `standby_ttl_seconds`

---

### 2.9 결과 저장 (store.py)

**관련 파일**: [store.py](service/app/logic/store.py)

#### 🔴 cache_set: _id 누락

> `store.py:55` [RAISE] `cache_set()`
> ```python
> raise ValueError("cache_set: doc에 _id가 없습니다.")
> ```

- **의미**: 호출부 버그. 저장할 문서에 `_id`가 없음

#### 🔴 fetch_by_cache_key: 문서 없음

> `store.py:125-128` [ERROR] `fetch_by_cache_key()`
> ```
> 캐시 조회 실패: cache_key=%s 에 해당하는 문서 없음
> ```
> `store.py:129` [RAISE]
> ```python
> raise KeyError(f"cache_key '{cache_key[:16]}...' 에 해당하는 캐시 문서가 없습니다.")
> ```

- **언제**: `/v1/fetch` 엔드포인트로 cache_key 주고 문서 요청 시 없음
- **확인할 것**: 먼저 해당 키로 저장된 적 있는지 (MDB 직접 조회)

#### 🔴 fetch_by_cache_key: 역직렬화 실패

> `store.py:134-138` [ERROR], `store.py:139` [RAISE]
> ```
> 캐시 결과 역직렬화 실패: cache_key=%s
> ```
> ```python
> raise ValueError(f"cache_key '{cache_key[:16]}...' 결과 역직렬화 실패") from e
> ```

- **의미**: 저장 당시 스키마와 현재 스키마 불일치

---

### 2.10 캐시 조회 (done_check.py)

**관련 파일**: [done_check.py](service/app/logic/done_check.py)

#### 🟡 캐시 조회 실패 → fallback

> `done_check.py:44-49` [EXCEPTION] `done_check()`
> ```
> 캐시 조회 실패: cache_key=%s, collection=%s — LLM 직접 호출로 대체
> ```

- **의미**: MDB 조회가 예외 → None 반환해서 LLM을 직접 호출
- **확인할 것**: MDB 연결, 자주 뜨면 캐시 효과 상실

---

## 3. 예외 클래스 레퍼런스

### 3.1 Service — [errors.py](service/app/core/errors.py)

| 클래스 | HTTP | error_type | 주요 발생 위치 |
|--------|------|-----------|---------------|
| `LLMRequestServiceError` | 500 | handler | 기본. 직접 던지는 경우는 거의 없음 |
| `CacheError` | 502 | cache | resolver.py:235/299/346/361 — Redis 실패 |
| `FileUploadError` | 502 | file | resolver.py:183(진입 가드 — `fi.data == b""` 재사용) / 208(Files API 업로드 실패) / 281(Context cache 생성 실패) |
| `ValidationError` | 422 | validation | 검증 계열 기본 |
| `UnsupportedFileTypeError` | 422 | unsupported_file_type | validators.py:37 |
| `FileTooLargeError` | 422 | file_too_large | validators.py:52 |

### 3.2 SDK — [exceptions.py](sdk/llm_request/exceptions.py)

| 클래스 | 언제 던져지나 |
|--------|-------------|
| `SDKError` | 기본 (직접 던지지 않음) |
| `ServiceConnectionError` | `_client.py:45/47/140/142` — 네트워크/타임아웃 |
| `ServiceError` | `_client.py:57/148/154` — HTTP 4xx/5xx. `status_code`, `error_type` 속성으로 §3.1 표와 매핑 |

### 3.3 표준 예외 재사용

| 예외 | 위치 | 의미 |
|------|------|------|
| `ValueError` | keying.py:193, store.py:55/139 | 입력/상태 검증 실패 |
| `RuntimeError` | providers.py:26, dedup.py:70/106 | Redis 미연결 |
| `KeyError` | store.py:129 | 캐시 문서 없음 |

---

## 4. 에러 시나리오 FAQ

### "Gemini 404가 나와요"
- 흐름: 대용량 파일의 Files API URI가 Gemini에서 만료 (Redis TTL < Gemini TTL)
- 로그: `Gemini 404 감지 — 자동 복구 시도` ([runner.py:416, 552](service/app/logic/runner.py#L416)) → `Gemini 404 자동 복구` ([resolver.py:477](service/app/logic/file_processing/resolver.py#L477)) → 진입 가드에서 `FileUploadError` raise ([resolver.py:183](service/app/logic/file_processing/resolver.py#L183))
- 자동 복구는 폐지 — 메모리 정리(`fi.data = b""`)와 양립 불가. 사용자에게 502 raise 됨 → 새 요청으로 재시도 필요. 상세 [`docs/16.memory-cleanup-plan.md`](docs/16.memory-cleanup-plan.md) §6
- 반복되면: `FILES_API_TTL_FLOOR` 조정, [cache_store.py:147](service/app/logic/file_processing/cache_store.py#L147) 경고 체크

### "SDK에서 ServiceError error_type=file"
- Service 쪽 선행 로그: [resolver.py:183 진입 가드 raise (`fi.data == b""` 재사용)](service/app/logic/file_processing/resolver.py#L183), [resolver.py:208 Files API 업로드 실패](service/app/logic/file_processing/resolver.py#L208), [resolver.py:281 Context cache 생성 실패](service/app/logic/file_processing/resolver.py#L281)
- 더 앞: [uploader.py:101 create_cache 재시도](service/app/logic/file_processing/uploader.py#L101)
- 실제 원인: llm-handler traceback 또는 provider 쪽 상태. 진입 가드 케이스는 404 재시도로 인한 동일 FileInput 재사용 시나리오

### "SDK에서 ServiceError error_type=cache"
- Service 쪽 선행 로그: [resolver.py:235/299/346/361](service/app/logic/file_processing/resolver.py#L235) Redis CRUD 실패
- 원인: Redis 장애. `redis-cli ping` 확인
- 기동 차단도 같이 뜰 수 있음: [providers.py:26 Redis 연결 실패](service/app/core/providers.py#L26)

### "SDK에서 ServiceConnectionError"
- Service 자체에 도달 못 함. Service 로그에 같은 request_id 흔적 없음
- 확인: 서비스 기동 여부 (`llm-request service 시작` 로그), 포트, 타임아웃 설정

### "MDB 캐시가 동작을 안 해요 (매번 LLM 호출됨)"
- 후보 로그:
  - [done_check.py:44 캐시 조회 실패](service/app/logic/done_check.py#L44) — MDB 연결 이슈
  - [runner.py:125 캐시 결과 역직렬화 실패](service/app/logic/runner.py#L125) — 스키마 불일치
  - [runner.py:223 MDB 저장 실패](service/app/logic/runner.py#L223) — 쓰기 실패해서 다음 호출이 miss
  - [runner.py:241 스트림 청크 0개](service/app/logic/runner.py#L241) — 빈 스트림은 원래 미저장

### "같은 요청이 동시에 들어왔는데 둘 다 LLM 호출됨"
- 기대: leader/waiter 구조로 한 번만 호출
- 의심 로그: `standby timeout` 계열 ([dedup.py:133](service/app/logic/file_processing/dedup.py#L133), [runner.py:482/624](service/app/logic/runner.py#L482), [resolver.py:364/406](service/app/logic/file_processing/resolver.py#L364))
- 의미: leader가 TTL 안에 끝내지 못함. `standby_ttl_seconds` 상향 검토

### "서비스가 안 뜹니다"
- 가장 먼저 볼 것: Redis ([providers.py:26 Redis 연결 실패](service/app/core/providers.py#L26))
- `llm-request service 시작` 로그 유무로 기동 성공/실패 구분
- 중간에 뜨다 죽으면 stdout에 `미처리 예외: request_id=...` traceback 있는지

### "미처리 예외 로그가 뜹니다"
- 위치: [main.py:93](service/app/main.py#L93)
- 의미: `LLMRequestServiceError`로 감싸지지 않은 예외 = 버그 신호
- 후속: 같은 `request_id`의 다른 로그를 따라가고, traceback을 errors.py 계층으로 매핑

### "응답 `meta.file_meta` 가 없는데?"

`stored_files` 인자가 없거나 빈 list 면 `meta.file_meta` 키 자체 부재 (MongoDB doc 의 `file_meta` 동작과 거울). **의도된 동작.**

→ 안전 접근: `meta.file_meta or []` 또는 `getattr(meta, "file_meta", None)`.

---

## stored_files 트러블슈팅

### "stored_files 호출이 `ServiceError(error_type='file_resolve')` 로 실패"

원인:
- file-manager 의 `get_file(file_path)` 가 404 (파일이 정말로 없거나 stale)
- 추가로 `check_cache(file_name/file_size/file_mtime)` 도 miss → 서버가 `FileResolveError` (502, error_type='file_resolve') raise
- SDK 가 `ServiceError` 로 caller 에게 전파

→ 복구: agent 가 file-manager 에 다시 등록 (`register_or_get`) 후 새 `file_path` 로 재호출.

```python
from llm_request import ServiceError, StoredFile, llm_request_async
from file_manager import FileManagerHandler

try:
    response, meta = await llm_request_async(llm_input, stored_files=refs)
except ServiceError as e:
    if e.error_type == "file_resolve":
        fm = FileManagerHandler()
        fresh = [
            StoredFile(**(await fm.register_or_get(bytes_data, fm.build_metadata(name))))
            for ...
        ]
        response, meta = await llm_request_async(llm_input, stored_files=fresh)
```

### "응답 `resolved_via='fallback'` 이 떴어요"

원인: `file_path` 가 stale (file-manager 측에서 같은 파일 재업로드 등으로 새 path 부여됨). `check_cache` 가 새 path 발견하여 정상 fetch.

→ 액션: 자기 DB 의 `file_path` 를 응답의 새 값으로 갱신. 다음 호출부터 `resolved_via='primary'` 로 돌아옴.

```python
for i, fm in enumerate(meta.file_meta):
    if fm["resolved_via"] == "fallback":
        db.update(eval_id, {f"file_meta.{i}.file_path": fm["file_path"]})
```

서버 로그에 `stale file_path: input=..., resolved=..., file_name=...` 라인이 WARNING 으로 남음.

### "StoredFile 생성 시 ValidationError"

원인: `extra="forbid"` 정책 — 6 필드 외 키 (예: 응답에 박힌 `resolved_via`) 가 들어가면 raise.

→ 해결: 응답 dict 에서 6 필드만 픽킹.

```python
ref = StoredFile(**{k: saved[k] for k in StoredFile.model_fields})
```

### "동시 요청 N 개인데 file-manager 가 N 회 fetch"

기대: **files_api 경로** 한정으로 leader 1 명만 fetch. inline 경로는 standby 없어 N 회 fetch 가 정상.

먼저 분류 확인:
- 작은 파일 (이미지/오디오 ≤20MB, PDF/text ≤50MB) → inline → N 회 fetch 가 의도된 동작
- 위 한도 초과 / 비디오 → files_api → leader 1 명만 fetch 기대

files_api 인데 N 회 fetch 가 발생하는 경우 원인:
- Redis 연결 끊김 → standby 획득 자체 실패 → 모두 자기가 fetch
- `standby_ttl_seconds` 너무 짧아 leader release 전에 만료 → waiter 가 timeout 후 자기 fetch (Y' Hybrid)
- standby key prefix 불일치 — `file_cache:files_api_standby:{checksum}` 확인

→ 확인:
- Redis 로그 — 위 prefix 키 존재 여부 / TTL
- `[dedup] role=standby_timeout` 로그 빈도 ([dedup.py:133](service/app/logic/file_processing/dedup.py#L133), [resolver.py:364/406](service/app/logic/file_processing/resolver.py#L364))
- `standby_ttl_seconds` 를 LLM 응답 시간 + file-manager fetch 시간 합보다 길게 설정

### "stored_files 호출인데 first call 에서 5xx"

원인: leader 분기에서 `fetch_with_fallback(fm, fi)` 가 file-manager 를 호출하는데 file-manager 자체가 다운되었거나 네트워크 단절. lazy fetch 가 leader 분기에서 발생하므로 5xx 가 그대로 LLM 호출 측까지 전파.

→ 확인:
- [file_resolver.py::fetch_with_fallback](service/app/logic/file_resolver.py) 의 `FMServiceError` 메시지
- `_process_inline` / `_upload_and_cache_file` 에서 lazy fetch 호출 위치
- file-manager 측 (`192.7.9.45:8600`) 헬스체크

### file-manager 통합 트러블슈팅

| 증상 | 의심 |
|------|------|
| `ServiceError(error_type='file_resolve')` 빈발 | file-manager 측 dedup / TTL 정책 확인. file_path stale 빈도 높음 |
| `ServiceError(status_code=5xx)` 그대로 전파 | file-manager 자체 장애 → 그쪽 로그 확인 (`192.7.9.45:8600`) |
| `check_cache` 만 hit, `get_file` 은 매번 404 | path 발급은 되는데 fetch 가 안 됨 — file-manager 의 storage 파일 누락 |

---

## 5. 참고

- 로그 구현 이력: [docs/14.observability-backlog.md](docs/14.observability-backlog.md)
- 개념 설명: [docs/00.concepts.md](docs/00.concepts.md)
- 설계 문서: [docs/05.file-handler-phase3-implementation.md](docs/05.file-handler-phase3-implementation.md)
