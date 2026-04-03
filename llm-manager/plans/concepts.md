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
- 같은 파일로 여러 프롬프트 실행 시 토큰/비용 절약
- `cached_content` 값이 매번 달라짐 → 캐시 키 문제
- ESG 평가기가 이 패턴 사용

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
