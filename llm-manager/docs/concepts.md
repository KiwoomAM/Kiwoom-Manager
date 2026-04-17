# 개념 정리 노트

논의 과정에서 정리한 개념들을 기록한다.

---

## Checksum (체크섬)

파일 내용 전체를 해시 함수(SHA-256 등)에 통과시켜 나오는 **고정 길이 지문(fingerprint)**.

```python
import hashlib
hashlib.sha256(file_bytes).hexdigest()
# → "a3f2b8c1..." (64자 hex)
```

- 파일 내용이 1바이트라도 다르면 완전히 달라짐
- **모든 파일 타입**(PDF, 이미지, 동영상, 텍스트 등)에 적용 가능 — 파일은 결국 바이트 덩어리
- 용도: 파일 변경 감지, 동일 파일 식별, 캐시 키 생성

### `hashlib.file_digest()` (Python 3.11+)

파일의 해시를 구하는 가장 간단한 방법. 내부적으로 청크 단위로 읽어서 메모리에 전부 올리지 않음.

```python
# Python 3.11+ 한 줄 방식
with open("report.pdf", "rb") as f:
    digest = hashlib.file_digest(f, "sha256").hexdigest()
# → "a3f2b8c1d4e5..." (64자)
```

`file_digest`가 없으면 직접 청크 단위로 해야 함:
```python
h = hashlib.sha256()
with open("report.pdf", "rb") as f:
    while chunk := f.read(8192):
        h.update(chunk)
digest = h.hexdigest()
```

`file_digest()`가 이걸 한 줄로 해주는 편의 함수.

### 왜 해싱인가 — mtime 등 메타데이터 방식과의 비교

파일 변경 감지에 해싱 대신 mtime(파일 수정 시간) 같은 메타데이터를 쓰면 해싱 비용을 아낄 수 있지 않을까?

#### mtime이란

mtime = modification time = 파일 *내용*이 마지막으로 변경된 시간. 파일에 write가 일어날 때 OS가 자동 갱신.

| 이름 | 의미 | 언제 바뀌나 |
|------|------|------------|
| mtime | 내용 수정 시간 | 파일 내용을 쓸 때 |
| ctime | 메타데이터 변경 시간 | 권한, 이름, 내용 등 뭐든 바뀔 때 |
| atime | 마지막 접근 시간 | 파일을 읽기만 해도 바뀜 |

#### mtime의 한계

- `touch` 등으로 내용 안 바뀌어도 mtime만 변경 가능 → 불필요한 캐시 미스
- 같은 초 안에 덮어쓰기하면 mtime이 동일 → 내용 달라졌는데 잘못된 캐시 히트
- 복사 방식에 따라 mtime이 복사 시점으로 바뀔 수 있음 → 같은 파일인데 미스

현실적으로 이런 케이스가 자주 발생하지는 않는다. mtime 방식도 99%는 잘 동작함.

#### 다른 메타데이터 조합도 마찬가지

- 파일 크기 → 다른 내용이어도 크기 같을 수 있음
- 경로 + mtime → 경로 바뀌면 같은 파일인데 미스
- 파일명 + mtime + 크기 조합 해싱 → 어차피 해싱 연산이고, 내용을 안 보는 한 false positive/negative 불가피

#### 해싱 비용은 얼마나 드는가

SHA-256 해싱 속도는 대략 500MB~1GB/s (일반 서버 기준).

| 파일 크기 | 해싱 시간 |
|----------|----------|
| 1MB | ~1ms |
| 20MB | ~20ms |
| 100MB | ~100ms |
| 1GB | ~1초 |
| 2GB (Gemini 최대) | ~4초 |

#### 극단 비교: Gemini 2GB 제한 내

**소형 파일 다수 호출 (1MB, 100회/일)**

| | SHA-256 해싱 | mtime + size |
|--|-------------|--------------|
| 1회 비용 | ~1ms | ~0ms |
| 하루 총 오버헤드 | 100ms | 거의 0 |
| LLM 호출 대비 비율 | 0.02% | - |
| 캐시 오판 시 비용 | - | 낮음 (작은 파일이라 재호출해도 저렴) |

→ 어느 쪽이든 차이 없음.

**대형 파일 다수 호출 (2GB, 100회/일)**

| | SHA-256 해싱 | mtime + size |
|--|-------------|--------------|
| 1회 비용 | ~4초 | ~0ms |
| 하루 총 오버헤드 | ~400초 (6.7분) | 거의 0 |
| LLM 호출 대비 비율 | ~13% | - |
| 캐시 오판 시 비용 | - | 매우 높음 (수백만 토큰 재과금 or 잘못된 응답) |

2GB 극단에서도 해싱 4초를 아껴봐야 하루 6.7분이지만, 캐시 오판 1회 = 수십 달러 토큰 낭비 또는 잘못된 응답 반환.

#### 결론

- "내용이 같은가?"를 내용을 안 보고 판단하는 건 불가능
- 핵심은 "mtime이 자주 틀려서"가 아니라 "해싱이 충분히 싸니까 굳이 덜 정확한 방법을 쓸 이유가 없다"
- 파일이 커질수록 해싱 비용도 커지지만, 캐시 오판 비용은 더 크게 커짐
- 현재 llm_request 사용 패턴(대부분 수십 MB 이하)에서는 해싱 비용 무시 가능

#### 참고 자료

content hashing이 mtime보다 신뢰성 높다는 근거를 다루는 공식 자료들:

- **"Build Systems a la Carte" (2018, ICFP)** — Make(mtime) vs Bazel(해시) 형식 비교. content hashing의 "early cutoff" 증명. https://doi.org/10.1145/3236774
- **Docker Build Cache 공식 문서** — COPY/ADD 시 mtime이 아닌 파일 내용 checksum으로 캐시 무효화. https://docs.docker.com/build/cache/
- **Git Internals 공식 문서** — content-addressable filesystem 설계 철학. https://git-scm.com/book/en/v2/Git-Internals-Git-Objects
- **Bazel Remote Caching 공식 문서** — 입력의 SHA-256 해시로 캐시 키 생성. https://bazel.build/remote/caching

### 파일명은 캐시 키에 영향 없음

파일명은 캐시 키에 포함되지 않는다. 캐시 키는 파일 **내용**의 SHA-256 해시(checksum)로 결정되므로, 같은 내용의 파일이면 이름이 달라도 같은 캐시 키가 생성된다.

### MIME 타입 자동 감지

파일 종류(PDF, 이미지 등)를 자동으로 판별하는 두 가지 방법:

**1. `mimetypes.guess_type()` — 확장자 기반 (Python 내장)**
```python
mimetypes.guess_type("report.pdf")   # → ("application/pdf", None)
mimetypes.guess_type("image.png")    # → ("image/png", None)
```
파일명의 `.pdf`, `.png` 같은 확장자를 보고 판단.

**2. `filetype.guess()` — 매직 바이트 기반 (외부 라이브러리)**
```python
filetype.guess("report.pdf")  # → Type(mime='application/pdf', extension='pdf')
```
파일 앞부분 몇 바이트(매직 바이트)를 읽어서 실제 내용으로 판단. 확장자가 없거나 거짓일 때도 정확.

**우선순위**: `ext_mime or guess.mime or "application/octet-stream"`
1. 확장자로 감지 성공 → 사용
2. 실패 → 매직 바이트로 감지
3. 둘 다 실패 → `"application/octet-stream"` (알 수 없는 바이너리)

llm-handler의 `Part.from_file()`도 동일한 이중 감지 패턴 사용.

---

## SDK + Service 구조

Kiwoom-Rag의 handler 패키지들이 사용하는 아키텍처 패턴.

```
Agent 머신                                별도 서버
┌──────────────────────┐            ┌──────────────────────┐
│ SDK (라이브러리)       │    HTTP    │ Service (FastAPI)     │
│ - import해서 사용      │───────────→│ - 실제 백엔드 연결     │
│ - httpx로 HTTP 요청   │            │ - API 엔드포인트 제공  │
│ - agent 프로세스 내    │            │ - 별도 프로세스        │
└──────────────────────┘            └──────────────────────┘
```

### 핵심 포인트
- **SDK 코드는 agent 머신에서 실행**됨 (import하는 쪽의 프로세스)
- SDK는 내부에서 `httpx.Client`로 Service에 HTTP 요청을 보내는 얇은 래퍼
- Service 없이 SDK만으로는 동작 안 함 (순수 HTTP 클라이언트)
- SDK에서는 로컬 파일 접근 **가능**, Service에서는 **불가**

### 각 Handler 역할

| Handler | SDK가 호출하는 URL | Service가 연결하는 백엔드 | 기본 포트 |
|---------|-------------------|------------------------|----------|
| llm-handler | `LLM_HANDLER_URL` | Gemini API | 8100 |
| mdb-handler | `MDB_HANDLER_URL` | MongoDB | 8300 |
| winus-handler | `WINUS_HANDLER_URL` | Oracle DB | 8200 |

---

## 라이브러리 vs 서버화 (SDK+Service)

코드를 호출자(agent) 프로세스 안에서 직접 실행하는 **라이브러리 방식**과, 별도 프로세스로 분리하는 **서버화 방식**의 비교.

### 구조 비교

```
라이브러리 방식:
  Agent 프로세스
  ├── my_library (직접 실행)
  │   ├── → 외부 시스템 A
  │   ├── → 외부 시스템 B
  │   └── → 외부 시스템 C

서버화 방식:
  Agent 프로세스                        별도 서버
  └── SDK (HTTP 클라이언트) ──HTTP──→  Service (FastAPI)
                                       ├── → 외부 시스템 A
                                       ├── → 외부 시스템 B
                                       └── → 외부 시스템 C
```

### 비교표

| 관점 | 라이브러리 | 서버화 (SDK+Service) |
|------|----------|---------------------|
| **호출자 메모리** | 모든 의존성이 호출자 프로세스에 로드 | SDK만 로드 (가벼움) |
| **의존성 관리** | 호출자가 모든 하위 의존성 설치 | 호출자는 SDK만 (httpx, pydantic 정도) |
| **설정/시크릿** | 호출자가 모든 외부 시스템 접속 정보 보유 | 시크릿은 서버에만, 호출자는 서비스 URL만 |
| **관심사 분리** | 호출자가 내부 구현 세부사항에 노출 | 호출자는 API 인터페이스만 알면 됨 |
| **배포** | 호출자 업데이트 시 함께 업데이트 | 서비스 독립 배포, 호출자와 생명주기 분리 |
| **스케일링** | 호출자 수만큼 리소스 중복 | 서비스 1개가 여러 호출자 처리 |
| **모니터링** | 호출자 로그에 섞임 | 서비스 로그/메트릭 분리 |
| **호출 비용** | 함수 호출 (ns 단위) | HTTP 왕복 (ms 단위) |
| **구현 비용** | 낮음 (함수만 작성) | 높음 (FastAPI + SDK + 배포) |
| **파일 전달** | 로컬 접근 (비용 0) | 네트워크 전송 필요 |
| **디버깅** | 스택 트레이스 하나 | 네트워크 경계에서 에러 추적 필요 |

### 언제 서버화하는가

- 외부 시스템 접점이 **3개 이상**이고 호출자가 직접 관리하기 복잡할 때
- API 키/시크릿을 호출자에게 노출하고 싶지 않을 때
- 여러 호출자(agent)가 **같은 리소스를 공유**해야 할 때 (동시성 제어, 캐시 공유)
- 호출자와 **독립적으로 배포/업데이트**해야 할 때

### 언제 라이브러리로 충분한가

- 외부 접점이 1~2개이고 설정이 단순할 때
- 호출자가 1~2개뿐일 때
- 성능이 중요해서 HTTP 왕복을 피해야 할 때
- 로직 검증 단계 (서버화 전 프로토타입)

### 라이브러리 → 서버화 전환 전략

1. 라이브러리로 로직 구현 + 검증
2. 모듈 경계를 깔끔하게 유지 (나중에 SDK/Service로 분리할 부분 인지)
3. 검증 완료 후 서버화: 모듈을 SDK(호출자 측)와 Service(서버 측)로 재배치
4. 리팩토링 비용은 있지만, 검증된 로직을 옮기는 거라 리스크 낮음

---

## 서버 내부 레이어 — config, models(DTO), router, core

서버화할 때 코드를 역할별로 나누는 구조. Kiwoom-Rag의 기존 handler들(llm-handler, mdb-handler, api-handler)이 공통으로 사용하는 패턴.

### 디렉토리 구조

```
service/app/
├── main.py          # FastAPI 앱 생성, 미들웨어/에러핸들러 등록
├── config.py        # 환경변수 관리
├── core/            # 외부 연결 관리 + 에러 정의
│   ├── errors.py
│   └── provider.py  # DB/API 클라이언트 싱글톤
├── models/          # 요청/응답 데이터 형태 정의 (DTO)
│   ├── requests.py
│   └── responses.py
└── routers/         # HTTP 엔드포인트 정의
    └── feature.py
```

### 각 레이어의 역할과 필요성

| 레이어 | 역할 | 왜 필요한가 | 필수? |
|--------|------|------------|-------|
| **config.py** | 환경변수(DB URL, 포트 등) 관리 | 라이브러리일 때는 호출자가 설정했지만, 서버는 자기 환경을 스스로 관리해야 함 | 필수 |
| **models/** (DTO) | HTTP로 주고받는 JSON 형태 정의 | 라이브러리는 Python 객체를 직접 넘기지만, HTTP는 JSON 직렬화가 필요하므로 형태를 명확히 정의해야 함 | 필수 |
| **routers/** | HTTP 엔드포인트 (URL + 메서드) 정의 | 라이브러리의 함수 호출을 HTTP 요청으로 바꾸는 것. 이게 없으면 서버가 아님 | 필수 |
| **core/** | 외부 시스템 연결(DB, Redis 등) 싱글톤 관리 | 서버는 오래 살아있는 프로세스이므로 연결을 한 번만 만들고 재사용 | 필수 |
| **main.py** | FastAPI 앱 진입점, 미들웨어/에러핸들러 등록 | 서버 프로세스의 시작점 | 필수 |
| **service/** (별도 레이어) | 비즈니스 로직 분리 | 기존 handler들도 사용하지 않음. router에서 직접 비즈니스 로직 호출. 로직이 복잡할 때만 선택적으로 분리 | 선택 |

### DTO (Data Transfer Object)란

HTTP로 데이터를 주고받으려면 JSON 형태를 정의해야 한다. 이 "형태 정의"가 DTO.

```python
# 라이브러리: Python dict/dataclass를 직접 넘김
result = await llm_request_async({"contents": "분석해줘", "model": "gemini-2.5-flash"})

# 서버: JSON으로 직렬화 → HTTP 전송 → JSON 역직렬화
# 이 과정에서 "어떤 필드가 있고, 타입이 뭔지" 정의가 필요
class LLMRequestBody(BaseModel):      # 요청 DTO
    llm_input: dict
    file_checksums: list[FileChecksum] | None = None
    cache_mode: bool = True

class LLMRequestResponse(BaseModel):  # 응답 DTO
    text: str
    cache_hit: bool
    cache_key: str
```

Pydantic BaseModel을 사용하면 자동으로 JSON 변환 + 타입 검증 + OpenAPI 문서 생성.

### 기존 handler들의 실제 패턴

기존 handler들은 router 안에서 바로 비즈니스 로직을 실행한다 (별도 service 레이어 없음):

```python
# llm-handler의 routers/generate.py — router에서 직접 처리
@router.post("/v1/generate")
async def generate(request: GenerateRequest):
    config = _build_config(request)           # 변환 (router 내부 함수)
    response = await client.generate(config)  # 호출
    return GenerateResponse(...)              # 응답
```

llm-request도 이 패턴을 따르면 된다. runner.py의 prepare/finalize 로직이 이미 잘 분리되어 있으므로 router에서 기존 함수를 호출하는 형태.

---

## llm_request vs Handler 역할 분리

```
Agent → llm_request (캐싱 레이어, 라이브러리)
         ├→ 캐시 키 생성 + MongoDB 조회 → 히트면 바로 반환
         └→ llm-handler SDK → HTTP → llm-handler 서비스 → Gemini API
```

| 구분 | llm_request | llm-handler |
|------|------------|-------------|
| 형태 | 라이브러리 (패키지) | SDK + HTTP 서비스 |
| 실행 위치 | agent 머신 | 서비스는 별도 서버 |
| 역할 | LLM 응답 캐싱 + agent별 호출 수 조절(미래) | Gemini API 호출 + 전체 동시 호출 수 제한 |
| 균등 배분 | agent별 배분 (어떤 agent가 몇 개 쓰는지) | 인프라 보호 (전체 API rate limit) |

---

## 파일을 LLM에 전달하는 4가지 방식

### A1: Files API (`upload_file()` → `FileRef`)
```python
file_ref = upload_file("report.pdf")  # Gemini 서버에 업로드
call(["분석해줘", file_ref])           # contents에 FileRef 포함
```
- `contents`에 `FileRef(name="files/abc", uri="gs://...", ...)` 들어감
- 대용량 파일에 적합
- URI가 업로드마다 달라짐 → 캐시 키 문제

### A2: Inline (`Part.from_file()` / `Part.from_bytes()`)
```python
part = Part.from_file("report.pdf")   # 로컬에서 읽어 base64 인코딩
call(["분석해줘", part])               # contents에 Part 포함
```
- `contents`에 `Part(type="inline", data="base64...", mime_type="...")` 들어감
- `Part.from_file(path)`: 파일 경로 → 읽기 → base64
- `Part.from_bytes(data)`: bytes → base64 (결과는 동일)
- 소용량 파일에 적합, base64라 크기 33% 증가

### A3: URI 참조 (`Part.from_uri()`)
```python
part = Part.from_uri("https://example.com/chart.png", mime_type="image/png")
call(["분석해줘", part])
```
- `contents`에 `Part(type="file", file_uri="https://...", mime_type="...")` 들어감
- 공개 URL, pre-signed URL 등
- `mime_type` 필수 (URL에서 추론 불가)

### B1: Context Cache (`create_cache()`)
```python
file_ref = upload_file("report.pdf")
cache_name = create_cache([file_ref], system_prompt, ttl=1800)
call("분석해줘", cached_content=cache_name)  # contents는 텍스트만
```
- `contents`에는 텍스트만, 파일은 `cached_content`로 간접 참조
- Files API 위에 캐시 레이어 추가 (업로드 → 캐시 생성 → 캐시 이름으로 참조)
- 같은 파일로 여러 프롬프트 실행 시 **토큰 90% 할인**, 단 **저장 비용 $1/1M토큰/시간**
- 시간당 4회 이상 호출 시 이득, 미만이면 Files API만 쓰는 게 저렴
- `cached_content` 값이 매번 달라짐 → 캐시 키 문제
- ESG 평가기가 이 패턴 사용

---

## Gemini 파일 전달 비용 구조

### Files API는 무료

Files API 업로드/저장 자체는 **비용 0원**. 비용이 발생하는 건 LLM 호출 시 **토큰 과금**뿐.
- 업로드: 무료
- 48시간 저장: 무료
- LLM 호출 시 파일 토큰: 표준 요금 (할인 없음)

따라서 같은 파일을 여러 번 쓰더라도 Files API만 쓰면 **업로드 시간만 아끼는 것**이지 토큰 비용은 매번 동일.

### Context Caching 비용

| 항목 | 비용 |
|------|------|
| 캐시 생성 | 무료 |
| 캐시 토큰 사용 | **표준의 10%** (90% 할인) |
| 캐시 저장 | **$1 / 1M토큰 / 시간** (Flash 기준) |

**손익분기점**: 같은 파일로 **시간당 4회 이상** 호출 시 캐싱이 이득.
- 1M 토큰 파일, 1시간 TTL: 저장 $1, 호출당 절약 $0.27
- $1 / $0.27 ≈ 3.7회 → 4회부터 이득

4회 미만이면 Files API만 쓰는 게 더 저렴.

### 파일 크기별 Gemini 제한

| 파일 타입 | Inline 제한 | Files API 제한 |
|----------|------------|---------------|
| PDF | 50MB | 50MB (1000페이지) |
| 이미지 | 20MB | 2GB |
| 오디오 | 20MB | 2GB |
| 비디오 | 사실상 불가 | 2GB |
| 텍스트/코드 | 20MB | 2GB |

llm-request에서는 **20MB를 기준으로 inline/Files API 분기**:
- ≤20MB: inline (base64 변환, 업로드 없음)
- \>20MB: Files API (업로드 후 URI 참조)
- PDF >50MB: split → Files API (Gemini의 PDF 50MB 제한 때문)

### Inline vs Files API 오버헤드

| | Inline | Files API |
|--|--------|-----------|
| 업로드 시간 | 없음 (요청에 포함) | 네트워크 업로드 수 초 |
| 요청 크기 | base64 포함 (원본의 1.33배) | URI 참조만 (수십 바이트) |
| 토큰 비용 | 동일 | 동일 |
| 외부 상태 | 없음 | 48h 후 만료 |

작은 파일(수 MB)은 inline이 더 간단하고 빠르다.

---

## Gemini Context Cache vs llm_request MongoDB Cache

| | Gemini Context Cache | llm_request MongoDB Cache |
|--|---------------------|--------------------------|
| 목적 | Gemini API 토큰/비용 절약 | 동일 요청 반복 시 LLM 호출 스킵 |
| 저장 위치 | Gemini 서버 | MongoDB |
| 저장 내용 | 업로드된 파일 + system prompt | LLM 응답 결과 |
| 수명 | TTL 기반 (예: 30분) | 영구 (TTL 설정 가능) |
| 참조 방법 | `cached_content="cachedContents/xyz"` | cache_key (SHA-256) 자동 |
| 값 특성 | 매번 생성 시 달라짐 (volatile) | 입력 기반 결정적 (deterministic) |

---

## Claude Prompt Caching vs Gemini Context Caching

"파일을 캐시에 올려놓고 여러 질문을 반복"하는 패턴. Claude와 Gemini는 방식이 다르다.

### Claude API (Anthropic) — 자동 prefix 캐싱

`cache_control` 마커만 붙이면, **별도 캐시 생성 없이** prefix가 동일할 때 자동으로 캐시 히트.

```python
# system이 동일하면 → 두 번째 호출부터 자동 캐시 히트
system = [{
    "type": "text",
    "text": big_file_content,
    "cache_control": {"type": "ephemeral"}  # 이 마커만 붙이면 됨
}]

# 질문 1 — 캐시 생성 (1.25x 비용)
r1 = client.messages.create(model="claude-opus-4-6", system=system,
    messages=[{"role": "user", "content": "요약해줘"}])

# 질문 2 — 캐시 히트! (0.1x 비용) 다른 함수에서 호출해도 OK
r2 = client.messages.create(model="claude-opus-4-6", system=system,
    messages=[{"role": "user", "content": "버그 있어?"}])
```

**캐시 히트 조건** (이것만 동일하면 자동):
- 같은 **API key**
- 같은 **model**
- **prefix 바이트** 동일 (렌더링 순서: `tools` → `system` → `messages`)

다른 함수, 다른 파일, 심지어 다른 프로세스에서 호출해도 위 조건만 맞으면 히트.

**주의 — 캐시 무효화 원인**:
- system에 `datetime.now()`, UUID 같은 변하는 값 → 매번 바이트가 달라져 무효화
- `json.dumps()` 시 `sort_keys=True` 안 쓰면 key 순서 변동
- tools 목록 순서 변경 (tools가 system보다 먼저 렌더됨)

**TTL**: 기본 5분, `"ttl": "1h"`로 1시간까지. 비용은 write 1.25x, read 0.1x.

### Gemini API (Google) — 명시적 캐시 생성

캐시를 **직접 생성**하고, 반환된 이름으로 **명시적 참조**해야 함.

```python
# 1. 캐시 명시적 생성
file_ref = upload_file("report.pdf")
cache_name = create_cache([file_ref], system_prompt, ttl=1800)

# 2. 캐시 이름으로 참조
r1 = call("요약해줘", cached_content=cache_name)
r2 = call("버그 있어?", cached_content=cache_name)  # 같은 이름 → 히트
```

ESG 평가기가 이 패턴 사용 중.

### 비교

| | Claude Prompt Caching | Gemini Context Caching |
|--|----------------------|----------------------|
| 방식 | prefix match (자동) | 명시적 생성 + 이름 참조 |
| 설정 | `cache_control` 마커 | `create_cache()` 호출 |
| 캐시 히트 조건 | model + prefix 바이트 동일 | 같은 `cached_content` 이름 사용 |
| 관리 | TTL 자동 만료 | TTL + 수동 삭제 |
| 함수/프로세스 간 공유 | 자동 (조건만 맞으면) | 캐시 이름 공유 필요 |

---

## 외부 TTL과 로컬 캐시 TTL 동기화

외부 시스템(Gemini Files API 48시간, Context Cache 사용자 설정)의 TTL과 로컬 캐시(Redis)의 TTL을 맞추는 패턴.

### 문제

로컬 캐시(Redis)에 "이 파일의 URI는 X" 라고 저장해뒀는데, 외부(Google)에서 이미 만료 삭제한 상태면 → X로 호출 시 404.

### 해결: TTL 동기화

```python
# Files API: Google 48시간 → Redis TTL도 48시간
redis.set(key, value, ex=172800)

# Context Cache: 사용자가 1시간 설정 → Redis TTL도 1시간
redis.set(key, value, ex=3600)
```

Redis 키가 Google 만료와 동시에 자동 삭제되므로:
- Redis hit → Google에도 존재 (높은 확률)
- Redis miss → Google에도 없음 (만료됨)

### Fallback

클럭 차이 등으로 Redis에는 있지만 Google에서 만료된 극히 드문 경우:
1. Gemini 404 수신
2. Redis 키 삭제
3. 재업로드/재캐싱
4. Redis에 새 키 저장

application-level TTL(만료시각 저장 + 조회 시 비교)보다 **Redis native TTL이 깔끔**: 별도 만료 체크 로직 불필요, 안 읽히는 키도 자동 정리.

---

## 역인덱스 (Reverse Index)

키-밸류 저장소에서 **역방향 조회**를 빠르게 하기 위한 보조 키. Redis처럼 "키로 값 얻기"만 O(1)인 저장소에선 반대 방향(값→키)을 직접 풀 수 없어 별도 보조 키가 필요하다.

### 비유: 책의 목차 vs 색인

- **목차(정방향)**: "1장은 어디?" → 23페이지
- **책 뒤쪽 색인(역인덱스)**: "'갯벌'이란 단어 어디 나왔어?" → 45, 78, 102페이지

목차만으론 단어 위치 못 찾음 → 책 전체를 뒤져야 함. 색인은 그걸 빠르게 해주는 보조 데이터.

### Redis에서의 모습

정방향 (기본 키):
```
files_api:abc123   →   { uris: ["gs://.../xyz789"], size: 30MB }
      ↑                         ↑
   키=checksum              값에 uri 포함
```

이 형태로 `"abc123의 uri 뭐야?"`는 즉시 풀림 (O(1)).
그러나 `"xyz789라는 uri의 주인(checksum) 누구야?"`는 기본 키로는 불가능 → Redis 전체를 SCAN해서 각 value를 확인해야 함.

역인덱스 추가:
```
files_api_uri:xyz789   →   "abc123"
       ↑                       ↑
    키=uri                 값=checksum
```

이러면 uri만 알아도 즉시 checksum 얻음. 단점: **Redis 키 2배**, 저장·삭제마다 두 키를 동기화해야 함.

### 언제 쓰는가

조회의 **시작점**이 "원본 키가 아닌 쪽"이고 거기서 원본 키를 역추적해야 할 때.
예: 외부 시스템(Gemini Files API 등)이 uri 목록만 알려주는데, 우리 Redis는 checksum 기준으로 정렬돼 있을 때.

### 언제 필요 없는가

조회 시작점이 **원본 키 자체**인 경우. Redis SCAN으로 checksum을 직접 얻고 value에서 uri를 꺼내 외부 시스템에 전달하는 방향이면 역방향 조회가 일어나지 않음.

llm-request의 Files API 용량 관리가 이 경우: **Redis SCAN + TTL 기반 FIFO**로 오래된 checksum을 시작점으로 고르고, value의 uri로 Gemini `delete_file` 호출. 역인덱스 불필요.

### 트레이드오프

| 방식 | 역방향 조회 속도 | 저장 공간 | 동기화 부담 |
|---|---|---|---|
| 역인덱스 추가 | O(1) | 2배 | 저장·삭제 2회 |
| 역인덱스 없이 SCAN | O(N) | 기본 | 없음 |

역방향 조회 빈도가 높거나 키 개수가 크면 역인덱스. 빈도 낮거나 "시작점을 바꿔서 역조회 자체를 회피" 가능하면 생략.

### llm-request의 최종 선택

- **파일 캐시(`files_api`) 역인덱스 생략**. 용량 관리는 SCAN + TTL로 처리 ([inline-vs-files-api-strategy.md](inline-vs-files-api-strategy.md) §8).
- **장점**: Redis 키 개수 절반, 동기화 부담 없음, Phase A에서 저장 부담 없음.
- **감수하는 것**: 용량 관리 시 O(N) SCAN. 현재 예상 키 개수(수백~수천)에선 무시 가능.

---

## 싱글톤 패턴 — `_get_client()` 왜 매번 안 만드나

HTTP 클라이언트(`httpx.AsyncClient`)를 요청마다 새로 만들지 않고 하나만 유지하는 패턴.

```python
_instance: AsyncClient | None = None

def _get_client() -> AsyncClient:
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance
```

### 왜 매번 새로 만들면 안 되는가

**1. TCP 연결 비용 (커넥션 풀)**

`httpx.AsyncClient`는 내부에 **커넥션 풀**을 들고 있다. 한 번 연결해두면 다음 요청은 그 연결을 재사용.

매번 새로 만들면 → 매 요청마다 TCP 3-way handshake부터 다시 시작 → 느려짐.

```
커넥션 풀 있음:  요청 1 → [연결 생성] → 응답 / 요청 2 → [기존 연결 재사용] → 응답
커넥션 풀 없음:  요청 1 → [연결 생성] → 응답 / 요청 2 → [연결 생성] → 응답
```

**2. 리소스 누수**

`AsyncClient`는 명시적으로 `close()` 해줘야 소켓이 닫힌다. 매번 새로 만들고 닫지 않으면 닫히지 않은 소켓이 쌓임.

### 참고: 환경변수로 URL 변경

`_resolve_base_url()`은 환경변수를 우선한다. 코드 수정 없이 배포 환경 전환 가능:

```bash
export LLM_REQUEST_URL="http://192.7.9.45:8101"
```

### 이 3개는 SDK 보일러플레이트

`_instance` + `_get_client()` + `_resolve_base_url()`은 **서버화하는 모든 SDK에 필수로 들어가는 설정 묶음**. "HTTP 클라이언트를 어디에, 어떻게 만드나"를 담당하며, 사용자 인자와는 무관하다. 사용자 인자(`llm_input` 등)는 `client.request()` 호출 시 별도로 전달된다.

---

## `# noqa` — 린터 경고 무시 주석

린터(ruff, pylint 등)가 "이 줄은 경고 건너뛰어" 처리하는 주석. `# noqa: <규칙코드>` 형식.

```python
import llm_handler as llm_handler  # noqa: PLC0414
import llm_handler.aio              # noqa: F401
from llm_request import aio as aio  # noqa: PLC0414
```

### 자주 나오는 규칙 코드

| 코드 | 의미 | 왜 무시하는가 |
|------|------|--------------|
| `F401` | "import했는데 코드에서 안 씀" | 서브모듈 로드 보장용 (`import llm_handler.aio`는 `llm_handler.aio`가 메모리에 로드되도록 보장하는 목적) |
| `PLC0414` | "같은 이름으로 re-import (의미 없음)" | `as same_name` 형태는 **외부에 모듈 객체를 노출**하는 의도. `import x as x`는 `__all__`에 없어도 re-export 의미 |

### 핵심 원칙

`# noqa`가 붙어 있다 = 린터가 뭔가 수상하다고 생각했지만, **의도적으로 한 것**이므로 무시해달라는 표시. 주석이 설명을 함께 달아주는 게 관례.

---

## HTTP body

HTTP 요청을 편지에 비유하면:
- 받는 주소 = URL
- 보내는 방식 = method (GET/POST 등)
- **편지 내용 = body**

API 서버끼리 통신할 때는 body를 JSON으로 변환해서 보내는 게 사실상 표준. `httpx`에서 `json=body`로 넘기면 자동 변환 + `Content-Type: application/json` 헤더도 자동으로 붙음.

---

## 엔드포인트 (Endpoint)

서버에 요청 보낼 때 **"어느 창구로 갈지"** 정하는 URL 경로.

```
http://192.7.9.45:8101  /v1/request
└─── 서버 주소 ──────┘  └─ 엔드포인트 ─┘
```

- 엔드포인트는 **서버가 정해서 열어놓는** 것
- SDK는 그 주소를 "알고 있는" 것뿐 (하드코딩)
- 서버가 주소를 바꾸면 SDK도 따라서 바꿔야 함

---

## `global` 키워드 (Python)

함수 안에서 **모듈 전역 변수를 수정**할 때 필요한 선언.

```python
_instance = None  # 모듈 전역 변수

def _get_client():
    global _instance       # "전역 변수를 건드릴 거야" 선언
    if _instance is None:
        _instance = AsyncClient(...)  # 전역 변수 수정
```

`global` 없이 함수 안에서 `_instance = ...` 하면 파이썬이 새 지역 변수를 만들어버려서 전역 변수가 바뀌지 않음.

읽기만 할 때는 `global` 불필요. **쓸 때만** 필요.

---

## TypedDict

dict인데 "어떤 키가 있고 타입이 뭔지" 명시하는 파이썬 타입.

```python
class RequestResult(TypedDict):
    result: dict[str, Any]
    meta: dict[str, Any]
```

- 런타임엔 그냥 dict. 타입 검사는 IDE/린터가 함
- SDK의 `models.py`에서 서버 응답 JSON 형태를 정의할 때 사용

---

## SDK 파일 구조 역할 분리

서버화된 SDK의 전형적인 파일 구조와 역할:

| 파일 | 역할 | 언제 열어보나 |
|------|------|--------------|
| `__init__.py` | 사용자 공개 API (입출력 시그니처) | 새 API 추가/수정 |
| `_client.py` | HTTP 전송 + 에러 처리 | HTTP 로직 수정 |
| `models.py` | 서버 응답 JSON 형태 정의 (내부용) | 응답 형식 바뀔 때 |
| `exceptions.py` | 에러 계층 구조 | 새 에러 타입 필요할 때 |
| `aio.py` | 다른 패키지 re-export (편의용) | 거의 안 열음 |

`__init__.py` vs `_client.py` 분리 이유: "사용자 인터페이스"와 "HTTP 인터페이스"를 분리해서 둘 중 하나가 바뀌어도 다른 쪽을 건드리지 않아도 됨.

---

## 에러 계층 구조 (Exception Hierarchy)

SDK에서 에러를 종류별로 계층화하는 패턴.

```
SDKError (기본)
  ├─ ServiceConnectionError  ← 통로 문제 (서버에 못 닿음, status_code=0)
  └─ ServiceError            ← 서버 내부 문제 (400/500 응답)
```

**왜 나누나**: 대응 방법이 다르기 때문.
- `ServiceConnectionError` → 재시도 가능 (서버가 잠깐 꺼진 것일 수 있음)
- `ServiceError(400)` → 재시도 무의미 (내가 잘못 보낸 것)
- `ServiceError(500)` → 잠시 후 재시도

계층화하면 호출하는 쪽에서 `except SDKError`로 한꺼번에 잡거나, 종류별로 나눠서 잡을 수 있음.

---

## JSON ↔ 파이썬 객체 변환 — 왜 JSON인가

HTTP는 **텍스트만 전송 가능**. 파이썬 객체는 네트워크로 못 보냄.

```
SDK (파이썬)                  네트워크           서버 (파이썬)
dict 객체
  → JSON 문자열  ──────────────────────→  FastAPI 자동 변환
                                           → LLMRequestBody 객체
                                           → 로직 처리
                                           → LLMRequestResponse 객체
  ← JSON 문자열  ←──────────────────────  FastAPI 자동 변환
dict 객체
  ← resp.json()
```

- **JSON** = 어느 언어든 읽을 수 있는 공통 텍스트 형식
- **파이썬 객체** = 해당 프로세스 안에서만 존재
- 네트워크 경계를 넘을 때 반드시 JSON으로 변환, 받는 쪽에서 다시 객체로 변환
- FastAPI가 이 변환을 자동으로 해줘서 코드에서 안 보이는 것뿐

---

## FastAPI 앱 구조 — main.py 역할

서버 시작할 때 **딱 한 번** 실행되는 설정 모음. 오케스트레이터가 아니라 **서버 부팅 스크립트**.

```
logging 설정 → FastAPI 앱 생성 → 라우터 등록 → 에러 핸들러 등록 → 미들웨어 등록
```

| 구성 요소 | 역할 |
|----------|------|
| `lifespan` | 서버 시작/종료 시 실행 (DB 연결 초기화 등) |
| `app.include_router(...)` | 라우터 등록 — 이 줄 없으면 엔드포인트 존재 안 함 |
| `@app.exception_handler` | 에러 발생 시 정해진 JSON 형태로 응답 |
| `@app.middleware("http")` | 모든 요청/응답 통과하는 공통 처리 |

### API 경로 버전 (`v1`)

`/v1/request`의 `v1`은 FastAPI 자동이 아니라 **개발자가 직접 정한 관례**. 나중에 v2 만들어도 v1 유지 가능. 경로는 `routers/request.py`에서 정의하고, `app.include_router()`로 한꺼번에 등록.

---

## 미들웨어 (Middleware)

"사이에 끼어있다"는 공통 개념. 맥락마다 의미가 다름.

### 1. 웹 프레임워크 미들웨어

모든 요청이 실제 처리되기 전/후에 **무조건 통과하는 공통 처리**.

```
요청 → [미들웨어] → 라우터 → 로직 → [미들웨어] → 응답
```

양파껍질 구조 — 안쪽으로 들어갔다가 역순으로 나옴. **네트워크 비용 없음** (같은 프로세스 안).

추가 방법 두 가지:

```python
# 1. 데코레이터 — 커스텀 로직 (request_id 생성, 로깅 등)
@app.middleware("http")
async def my_middleware(request, call_next):
    response = await call_next(request)
    return response

# 2. add_middleware — 남이 만든 범용 미들웨어 (CORS, GZip 등)
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

### 2. 기업용 미들웨어

시스템들 사이를 연결해주는 **별도 서버/소프트웨어**. Oracle Middleware, IBM MQ, Apache Kafka 등. **네트워크 비용 발생**. 사내 시스템 간 프로시저 호출 얘기가 나오면 이 의미일 가능성 높음.

### 3. OS 미들웨어

앱과 하드웨어 사이 레이어 (드라이버 등).

### `app.mount()`는 미들웨어가 아님

특정 경로에 별도 앱 자체를 붙이는 것. 이름이 비슷해서 헷갈리지만 다른 개념:

```python
app.mount("/static", StaticFiles(directory="static"))
# /static/* 요청은 라우터 안 거치고 파일 서버로 직접 감
```

---

## Dockerfile vs docker-compose.yml vs requirements.txt

Docker로 서비스를 배포할 때 사용하는 세 파일의 역할.

### Dockerfile — "이 서버 하나를 어떻게 만드는가"

하나의 서비스를 컨테이너 이미지로 만드는 레시피.

```dockerfile
FROM python:3.11-slim              # 기반 이미지
WORKDIR /app
COPY requirements.txt .            # 의존성 목록 복사
RUN pip install -r requirements.txt # 설치
COPY service/app/ ./app/           # 코드 복사
EXPOSE 8101                        # 포트 선언
CMD ["uvicorn", "app.main:app"]    # 실행 명령
```

결과: **이미지 1개** (예: `kiwoom-llm-request:latest`)

### docker-compose.yml — "여러 서버를 어떻게 같이 띄우는가"

여러 서비스를 한번에 관리하는 설정. Kiwoom-Rag 루트에 위치.

```yaml
services:
  llm-request:
    build:
      dockerfile: packages/llm-request/service/Dockerfile
    ports:
      - "8101:8101"
    environment:                              # ← config.py 기본값을 덮어씌움
      - MDB_HANDLER_URL=http://mdb-handler:8300
      - LLM_HANDLER_URL=http://llm-handler:8100
```

- Dockerfile을 참조해서 빌드
- 환경변수, 포트, 네트워크 설정
- `docker compose up -d` 한 번으로 전체 실행

### requirements.txt — "어떤 패키지를 설치하는가"

Dockerfile 안에서 `pip install`에 사용되는 의존성 목록.

- `uv export`로 생성 (버전 고정 + 해시 포함)
- 또는 직접 작성 (최소 버전만 명시)

### 세 파일의 관계

```
docker-compose.yml (루트)
  └── 서비스: llm-request
        ├── build: Dockerfile 사용
        │     └── pip install → requirements.txt
        ├── environment: MDB_HANDLER_URL=...  ← config.py 기본값 덮어씌움
        └── ports: 8101:8101
```

### config.py의 기본값과 환경변수

```python
# config.py
class Settings(BaseSettings):
    mdb_handler_url: str = "http://localhost:8300"  # 기본값 (fallback)
```

pydantic-settings가 자동으로 우선순위대로 읽는다:

1. **환경변수** (docker-compose.yml의 environment) → 최우선
2. **.env 파일** → 환경변수 없으면 여기서
3. **config.py 기본값** → 둘 다 없으면 이걸 사용

실제 운영에서 config.py의 기본값이 쓰이는 경우는 거의 없다. 로컬 개발 시 아무것도 설정 안 했을 때의 편의용.

---

## uv workspace

Python 패키지 관리 도구 `uv`의 workspace 기능. 여러 패키지를 하나의 프로젝트로 관리한다.

### 왜 쓰는가

llm-request처럼 SDK와 Service가 분리된 프로젝트에서:
- `uv sync` 한 번으로 sdk + service 의존성 전부 설치
- `uv.lock`으로 버전 고정 (재현 가능한 빌드)
- pip보다 10~100배 빠른 설치

### 구성

```toml
# 루트 pyproject.toml
[tool.uv.workspace]
members = ["sdk", "service"]       # 하위 패키지 목록

[tool.uv.sources]
kiwoom-llm-request = { workspace = true }          # SDK
kiwoom-llm-request-service = { workspace = true }   # Service
```

루트 pyproject.toml은 **workspace 관리용**일 뿐, 자체 코드는 없다 (`packages = []`).

### 주요 명령어

```bash
cd packages/llm-request/    # workspace 루트
uv sync                     # 전체 의존성 설치
uv run pytest tests/ -v     # workspace 컨텍스트에서 테스트
uv run uvicorn app.main:app --port 8101  # 서비스 실행
uv export --no-dev -o requirements.txt   # Docker용 requirements 생성
```

### Kiwoom-Rag에서의 사용

llm-handler, winus-handler가 이미 동일한 uv workspace 패턴을 사용 중.

---

## Redis 인프라 구조 (Predixy + Sentinel)

앱이 사용하는 Redis URL(`redis://192.7.9.45:7617/0`)은 Redis에 직접 연결하는 것이 아니라 **Predixy 프록시**를 통해 접속한다.

### 구조

```
앱 (port 7617) → Predixy (프록시) → Sentinel (모니터링) → Redis (port 6379)
```

| 컨테이너 | 역할 | 포트 |
|----------|------|------|
| `kiwoom-chatbot-redis` | Redis 본체 | 6379 |
| `kiwoom-redis-sentinel` | Master 모니터링 / failover | 26379 (내부) |
| `kiwoom-predixy` | Redis 프록시 (외부 진입점) | **7617** (외부 노출) |

### 트러블슈팅: "Timeout reading from socket" 에러

- Redis, Sentinel이 정상이어도 **Predixy가 Sentinel과의 연결을 잃으면** 타임아웃 발생
- Predixy 컨테이너는 healthy로 보이지만 내부적으로 연결이 끊어진 상태일 수 있음
- **해결**: `docker restart kiwoom-predixy`
- **확인 순서**:
  1. `docker ps -a | grep predixy` — 컨테이너 상태 확인
  2. `docker exec kiwoom-redis-sentinel redis-cli -p 26379 sentinel get-master-addr-by-name mymaster` — Sentinel이 master를 아는지 확인
  3. `docker exec kiwoom-chatbot-redis redis-cli info memory` — Redis 메모리 확인
  4. Predixy 재시작으로 대부분 해결됨

---

## Cache Stampede / Thundering Herd / Dogpile

세 용어 모두 **같은 현상**을 부르는 이름이다: 캐시가 비어있는 동안 동일한 요청 N개가 동시에 백엔드로 몰려 중복 작업이 발생하는 문제.

### 시나리오 (llm-request 기준)

에이전트가 같은 PDF + 같은 프롬프트로 3개 요청을 동시에 보낸 상황:

```
t=0ms   req A, B, C 모두 도착
t=1ms   A, B, C 각자 done_check → MDB 비어있음 → 모두 cache miss
t=2ms   A, B, C 각자 Redis files_api:{checksum} 조회 → 모두 miss
t=3ms   A, B, C 각자 upload_file() 호출 → Gemini 업로드 3회 ⚠️
t=10s   A, B, C 각자 create_cache() → context_cache 3개 ⚠️
t=11s   A, B, C 각자 llm_handler.call() → LLM 호출 3회 ⚠️
```

**피해**: Gemini 업로드 비용 3배, context_cache 3개(48h 중복 저장), 토큰 비용 3배.

### 왜 발생하는가

캐시 조회와 캐시 저장 사이에 **간극**이 있기 때문. 그 사이에 들어온 요청들은 모두 "비어있음"을 보고 각자 실제 작업을 시작한다.

### 용어 출처

- **cache stampede**: 캐시가 비어지는 순간 백엔드가 몰매 맞는 모습 → 가축 떼의 폭주(stampede) 비유
- **thundering herd**: 잠자던 프로세스들이 동시에 깨어나 경쟁 → 천둥소리에 놀란 가축 떼 비유 (유닉스 `accept()` 경쟁에서 유래)
- **dogpile**: 같은 뜻, Python 진영에서 자주 씀 (`dogpile.cache` 라이브러리 존재)

---

## Single-flight (= Request Coalescing)

Cache stampede의 **해법 패턴**. "같은 작업은 한 번만 날린다(flight)"는 의미.

### 핵심 아이디어

같은 key에 대한 작업이 **진행 중이면, 뒤따라온 요청들은 첫 번째의 결과를 공유**한다. 첫 번째가 실패하면 실패도 공유한다.

```python
# 관용 구현 (Python asyncio, 약 20줄)
_in_flight: dict[str, asyncio.Future] = {}

async def run(key: str, factory):
    if fut := _in_flight.get(key):
        return await fut                 # 팔로워: 리더의 Future await
    fut = asyncio.get_running_loop().create_future()
    _in_flight[key] = fut
    try:
        result = await factory()
        fut.set_result(result)
        return result
    except BaseException as e:
        fut.set_exception(e)
        raise
    finally:
        _in_flight.pop(key, None)
```

### 용어 구분 — single-flight vs 비슷한 것들

| 용어 | 정확도 | 설명 |
|------|--------|------|
| **single-flight** ⭐ | **정식 명칭** | Go의 `golang.org/x/sync/singleflight` 가 업계 표준으로 만듦 |
| request coalescing | 동의어 | CDN/프록시 문맥에서 자주 씀 |
| request collapsing | 동의어 | Netflix Hystrix 용어 |
| deduplication / dedup | 상위 개념 | 일반 용어 (중복 제거). 주로 **데이터** dedup(스토리지)에서 씀. "request deduplication" 이라고 하면 이 패턴을 포함 |
| memoization | 다름 | 결과 캐싱 일반. single-flight는 "**진행 중인 호출**까지 공유"하는 특수 케이스 |

### Sync vs Lock vs Single-flight — 비교

세 개념은 다르다. 특히 **Lock과 헷갈리기 쉬움**.

| | 정의 | 같은 key 요청 3개 | 다른 key 요청 3개 |
|---|---|---|---|
| **Sync / Sequential** | 모든 요청을 한 줄로 세워 하나씩 처리 | 3번 실행, 순차 | 3번 실행, **순차 (느림)** |
| **Lock (mutex)** | 공유 자원 접근 시만 한 명씩 통과 | 3번 실행, 순차 (각자 자기 일 끝까지) | 다른 자원이면 병렬 |
| **Single-flight** | 같은 일은 **1번만** 실행하고 결과 공유 | **1번 실행**, 나머지는 await | 3번 실행, **병렬** |

비유:
- **Sync** = 계산대 1개 — 주문이 같든 다르든 한 줄로
- **Lock** = 창고 열쇠 1개 — 꺼낼 때만 한 명씩, 하지만 각자 자기 커피 끓임 (같은 커피라도 2번 끓임)
- **Single-flight** = 같은 주문 묶어서 1번만 조리 — A가 커피 시키면 B도 그 커피 받음

**결정적 차이**: Lock은 팔로워도 **자기 차례에 실행**함. Single-flight은 팔로워가 **실행 자체를 건너뜀** (Future await만).

### 요청 단위가 아니라 "작업 단위" dedup

Single-flight의 강력함은 **각 sub-operation이 자체 key와 자체 registry를 가질 수 있다**는 것. 바깥 요청이 완전히 달라도, 내부 sub-operation의 key만 같으면 그 단계에서 dedup 된다.

예: llm-request에선 3계층으로 설계 가능

```
llm_request_async(req)
└─ llm_call_registry.run(cache_key=전체해시)          ← ① LLM 레벨
   └─ resolve_files(...)
      └─ context_cache_registry.run(hash_key=파일+모델+system)  ← ② context_cache 레벨
         └─ files_api_registry.run(file_checksum=파일내용)        ← ③ 업로드 레벨
            └─ upload_file()
```

시나리오 — **질문은 다 다른데 파일은 같은** 3개 요청:

| | Req A | Req B | Req C |
|---|---|---|---|
| 입력 | PDF + "요약" | PDF + "핵심 3개" | PDF + "번역" |
| ① cache_key | `q1` | `q2` | `q3` |
| ② hash_key | `k1` | `k1` | `k1` |
| ③ file_checksum | `f1` | `f1` | `f1` |

→ ①에선 dedup X (질문 다름), 하지만 ②에서 context_cache **1번만 생성**, ③에서 업로드 **1번만 실행**. LLM 호출만 각자 3번 수행.

**요지**: "요청 전체가 같냐?"가 아니라 **"각 작업 단위가 같냐?"** 가 기준.

### 시간축별 동작 — in-flight + persistent cache 협업

Single-flight 혼자로는 완전하지 않다. 실행이 끝나면 in-flight dict에서 사라지므로. **persistent cache(Redis/MDB)와 한 쌍**으로 동작해야 한다.

```
시간 ─────────────────────────────────────►
       A 실행          Redis/MDB 저장 기간        TTL 만료
     ┌──────────┐ ┌──────────────────────────┐ ┌────────
     │single-flight│ │    persistent cache       │ │  miss
     │ dedup       │ │        dedup              │ │  (재실행)
     └──────────┘ └──────────────────────────┘ └────────
```

| 시점 | 동작 |
|------|------|
| A 실행 중 (수 초) | 새 요청은 A의 Future await — **single-flight** |
| A 완료 후 ~ TTL 내 | 새 요청은 Redis/MDB 조회 HIT — **persistent cache** |
| TTL 만료 후 | 새 요청이 새 리더가 되어 재실행 |

**핵심**: 시간차는 상관 없음. "A가 아직 뛰고 있냐?"만 확인. 끝난 뒤에 온 요청도 걱정 없음 — persistent cache가 받아줌.

### 라이브러리

| 언어 | 라이브러리 |
|------|------------|
| Go | [`golang.org/x/sync/singleflight`](https://pkg.go.dev/golang.org/x/sync/singleflight) (원조) |
| Python | 표준 없음. [`aiocache`](https://github.com/aio-libs/aiocache) 에 내장, [`dogpile.cache`](https://github.com/sqlalchemy/dogpile.cache) (SQLAlchemy 팀), [`async-lru`](https://github.com/aio-libs/async-lru) |

Python 진영에선 **직접 구현**이 관용적. 20줄 패턴이면 충분하고, 동작이 중요해서 투명하게 두는 걸 선호.

### 교육과정에서의 위치

학부 CS 커리큘럼에 **"single-flight"라는 이름으로 배우는 과목은 거의 없다**. 다만 관련 기반 개념은 여러 수업에서 만난다:

- **운영체제**: race condition, mutex, synchronization — *기반*
- **동시성 프로그래밍 / 병렬 컴퓨팅**: Future, Promise, async await — *도구*
- **분산 시스템**: thundering herd, cache coherence — *문제 정의를 다루는 경우 있음*
- **시스템 디자인 인터뷰**: cache stampede 해법으로 출제됨 — *실무 지향*

정리하면:
- **이름(single-flight)을 몰라도 개념은 익숙할 수 있음** (Future/Promise를 배웠다면)
- **백엔드 실무에서 거의 필수로 만나는 패턴** — 캐시가 있는 시스템이면 언젠가 부딪힘
- Go 커뮤니티가 이 이름을 널리 퍼뜨린 게 2017년경, 업계 용어로 정착한 지 10년 미만
- 학부보단 **대학원 분산시스템 수업** 혹은 **실무/인터뷰 준비 과정**에서 접할 확률이 높음

상사 등에게 설명할 땐:
> "동시성 상황에서 **single-flight 패턴** (= request coalescing)을 적용해 **cache stampede**를 방지합니다. Go 표준 `singleflight` 가 그 방식이고, Python은 20줄 관용 구현으로 대체합니다."

---

## 비동기(async)와 이벤트 루프 — sync I/O가 전체를 멈추는 이유

FastAPI 서비스는 async 기반이다. 이 환경에서 **sync I/O 한 번이 의외로 비싸다**.

### 이벤트 루프의 실체

uvicorn 워커 1개 = 프로세스 1개 = **이벤트 루프 1개**. 모든 요청이 이 한 루프 위에서 돌아간다 — 요청마다 스레드/프로세스가 갈라지지 않는다.

비유: 식당에 요리사 1명. 손님 10명이 와도 요리사는 1명.

### `await`는 "자발적 양보"

- `await aget_file()` → 요리사가 "이거 기다리는 동안 다른 주문 받을게" 하고 자리 양보
- `get_file()` (sync) → 요리사가 기둥처럼 멈춤. 그동안 다른 주문은 아무도 못 받음

즉 **async = 자동 병렬이 아님**. `await`를 만났을 때만 이벤트 루프가 다른 코루틴을 실행할 기회를 갖는다.

### sync 호출이 "밀린다" 이상으로 나쁜 이유

sync I/O가 100ms 걸린다면 — 그 100ms 동안:

- **새로 도착한 요청**: 시작조차 못 함
- **이미 `await`로 대기 중이던 다른 요청들**: LLM 응답이 도착해도 깨어나지 못함 (이벤트 루프가 점유당해서)

즉 요청 10개 동시 도착 시:
- async 경로: ~100ms에 10개 다 끝
- sync 호출 끼어듬: 직렬화 — 첫 100ms, 두 번째 200ms, ... 열 번째 1000ms

### 자동으로 갈라지는 경우 — 두 가지뿐

1. **uvicorn `--workers N`** — 워커 N개면 이벤트 루프도 N개. 식당을 N개로 복제하는 셈
2. **FastAPI 라우터를 `def`로 정의** (async 아님) — FastAPI가 자동으로 스레드풀에 던져줌

현재 llm-request는 라우터 모두 `async def`라 2번 해당 없음. 워커를 늘리지 않는 이상 sync I/O는 그대로 이벤트 루프를 막는다.

### 완화책

1. **`await` 가능한 async 버전 쓰기** — 가장 표준
2. **`asyncio.to_thread(sync_func, *args)`** — sync 함수를 별도 스레드로 빼서 이벤트 루프 보호
3. **`uvicorn --workers N`** — 워커 여러 개로 블로킹 영향 분산

### 실무 교훈

file-manager 같은 외부 HTTP 호출에서 async vs sync는 **트래픽 많을 때 체감 차이가 크다**. 라이브러리가 async 버전을 제공하면 `await` 쓰는 게 기본이다.

이 정도로 말하면 충분히 소통된다.
