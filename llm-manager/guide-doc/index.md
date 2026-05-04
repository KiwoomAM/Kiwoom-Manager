# kiwoom-llm-request

> Agent에서 LLM을 호출할 때 **중복 호출 없이 이전 응답을 재사용**하는 캐싱 레이어.

같은 질문 + 같은 파일이면 LLM을 다시 부르지 않고 MongoDB에서 즉시 반환한다.
파일 처리(크기·타입에 따른 inline / Files API 전환, PDF 50MB 초과 시 자동 분할)는
Google 공식 문서 기준으로 내부에서 처리되므로, 사용자는 파일을 넘기는 방법만 알면 된다.

## 핵심 특징

| | |
|---|---|
| **LLM 응답 캐시 (MongoDB)** | 동일 입력은 저장된 응답을 반환, LLM 재호출 없음 |
| **Files API URI 캐시 (Redis)** | 같은 파일 재업로드 방지 (TTL 47h) |
| **Context Cache (Gemini)** | 반복 사용 파일의 입력 토큰 절감 (선택, [자세히](user-guide/caching.md)) |
| **파일 처리 자동화** | 크기·타입에 따라 inline / Files API 자동 전환, PDF 50MB 초과 시 자동 분할 |
| **SDK ↔ Service 분리** | Agent는 가벼운 SDK만 설치(`httpx` 의존), MDB·Redis·LLM 호출은 Service가 담당 |
| **에러 가시성** | 실패 단계·원인을 캐시에 기록해 재현 가능 ([자세히](user-guide/error-handling.md)) |

## 설치

```bash
pip install -e ./packages/llm-request/sdk
```

SDK는 `LLM_REQUEST_URL` 하나만 보고 Service에 연결한다.

```python
import os
os.environ["LLM_REQUEST_URL"] = "http://192.7.9.45:8101"  # 기본값, 생략 가능
```

> **사전조건**: Service(8101)가 떠 있어야 한다. Service 환경변수·구동은 [developer-guide/02-setup.md](developer-guide/02-setup.md).

## Quick Start — 텍스트만

FastAPI 핸들러나 Jupyter 등 **이미 async 컨텍스트 안에서** 호출한다.

```python
from llm_request import llm_request_async

response, meta = await llm_request_async(
    {"contents": "대한민국의 수도는?", "model": "gemini-2.5-flash"},
)
print(response.text)
print(meta.source)   # 'live' (LLM 호출) 또는 'cache' (이전 응답 재사용)
```

같은 입력으로 다시 호출하면 `meta.source`가 `'cache'`로 바뀐다.

## Quick Start — 파일 첨부

`FileInput`을 구성해서 `files=` 인자로 넘긴다. 헬퍼로 한 번에 만든다:

```python
from pathlib import Path
import mimetypes
from llm_request import llm_request_async, FileInput

def make_file_input(p: Path) -> FileInput:
    return FileInput(
        file_name=p.name,
        file_size=p.stat().st_size,
        file_mtime=int(p.stat().st_mtime),
        mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        extension=p.suffix.lstrip("."),
        data=p.read_bytes(),
    )

files = [make_file_input(Path("report.pdf")), make_file_input(Path("data.csv"))]

response, meta = await llm_request_async(
    {"contents": "두 파일을 비교 분석해줘", "model": "gemini-2.5-flash"},
    files=files,
)
print(response.text)
print(meta.source)
```

> **⚠️ `file_name`, `file_size`, `file_mtime`은 실제 파일에서 추출한 값과 반드시 일치해야 한다.**
> 이 세 값은 캐시 키 생성에 직접 사용된다. 잘못된 값을 넣으면 캐시 미스가 반복 발생한다.

`FileInput` 필드 상세는 [api-reference.md](api-reference.md)를 참고.

> **⚠️ 장시간 실행되는 프로세스는 종료 전 `close()` 호출**
>
> SDK는 httpx `AsyncClient` 싱글톤을 유지한다. graceful shutdown을 위해 진입점에서 `await close()` 호출 권장. 누락 시 `ResourceWarning: unclosed <httpx.AsyncClient>` 로그가 뜸.
>
> ```python
> from llm_request import close
> await close()  # 종료 직전
> ```

## 더 알아보기

### User Guide — 동작 원리와 사용법

- [Caching](user-guide/caching.md) — 3계층 캐시 동작, MDB/Redis/Context Cache
- [Dedup](user-guide/dedup.md) — 동시 동일 요청 중복 처리 방지 (cache stampede)
- [File handling](user-guide/file-handling.md) — inline / Files API 전환, PDF 분할
- [Error handling](user-guide/error-handling.md) — 실패 단계 기록, 재시도 정책
- [Observability](user-guide/observability.md) — 로그·메트릭으로 무엇이 보이는가
- [llm_handler re-export](user-guide/llm-handler-reexport.md) — `kiwoom-llm-handler` 타입 재노출

### API Reference

- [api-reference.md](api-reference.md) — SDK 공개 API (`llm_request_async`, `FileInput`, `RequestMeta` …) + Service HTTP API

### Developer Guide — 후임 인수용

- [01. Architecture](developer-guide/01-architecture.md) — 현재 구조 + 디렉토리 투어
- [02. Setup & Testing](developer-guide/02-setup-and-testing.md) — env vars / 로컬 구동 / 테스트 / 디버깅
- [03. Design decisions](developer-guide/03-design-decisions.md) — Phase 전략, async, 캐시 키 등
- [04. Contributing](developer-guide/04-contributing.md) — 협업 지침

### More

- [Glossary](glossary.md) — 용어 사전
- [Changelog](changelog.md) — 마이그레이션·향후 계획

## 패키지 메타

| 항목 | 값 |
|---|---|
| 패키지명 | `kiwoom-llm-request` |
| 버전 | `0.2.0` |
| Python | `>=3.11` |
| 구성 | **SDK** (에이전트 설치) + **Service** (FastAPI, port 8101) |
| SDK 의존성 | `httpx>=0.27.0`, `kiwoom-llm-handler>=0.1.0` |
| Service 의존성 | `kiwoom-llm-handler`, `kiwoom-mdb-handler`, `kiwoom-redis-handler`, `kiwoom-file-manager`, `PyMuPDF` |
| 통신 방식 | SDK → HTTP(httpx) → Service(8101). MDB·Redis·LLM 호출은 Service가 일괄 담당 |
