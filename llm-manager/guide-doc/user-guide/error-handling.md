# Error Handling

`llm-manager` SDK는 모든 에러를 두 가지 예외로 통일해서 던진다.

## SDK 예외 계층

| 예외 | 발생 조건 |
| --- | --- |
| `ServiceConnectionError` | Service 자체에 연결 실패 (네트워크 오류, 타임아웃) |
| `ServiceError` | Service가 4xx/5xx 응답을 반환. `status_code` + `error_type`으로 원인 구분 |

```python
from llm_manager import llm_request_async, ServiceConnectionError, ServiceError

try:
    response, meta = await llm_request_async(llm_input)
except ServiceConnectionError as e:
    # llm-manager Service에 연결 못 함 — 재시도 또는 알림
    ...
except ServiceError as e:
    # Service는 응답했지만 4xx/5xx
    print(e.status_code)   # 422 / 500 / 502 등
    print(e.error_type)    # 'validation' / 'cache' / 'handler' 등 (§2 참고)
    print(e.message)       # 사람 읽기용 에러 메시지
```

`ServiceError`의 분기 처리는 보통 `error_type`으로 한다 ([§2](#service-에러-타입-error_type) 참고).

## Service 에러 타입 (`error_type`)

`ServiceError.error_type`으로 분기 처리한다.

| `error_type` | HTTP | 발생 조건 |
| --- | --- | --- |
| `validation` | 422 | 요청 스키마 검증 실패 (FastAPI body validation 또는 내부 검증) |
| `unsupported_file_type` | 422 | Gemini 지원 외 MIME 타입 |
| `file_too_large` | 422 | 개별 파일 2GB 초과 |
| `file` | 502 | Files API 업로드 실패 |
| `file_resolve` | 502 | stored_files resolve 단계 실패 (file-manager에서 파일 못 찾음) |
| `cache` | 502 | MDB / Redis 캐시 조회·저장 실패 |
| `handler` | 500 | 그 외 미처리 예외의 fallback (LLM 호출 실패 포함) |

> 4xx (`422`)는 호출자 입력 문제 — 보통 재시도해도 같은 결과.
> 5xx (`502` / `500`)는 외부 서비스 또는 내부 문제 — 일시적이면 재시도로 회복 가능.

각 `error_type`이 실제로 어떤 로그 패턴과 함께 발생하는지, 시나리오별 트러블슈팅은 [debugging-guide.md](../../debugging-guide.md)의 §3 (예외 클래스 레퍼런스) + §4 (에러 시나리오 FAQ) 참고.

## 에러 응답 형식

Service는 4xx/5xx 응답 시 표준 JSON으로 답한다.

```json
{
  "error": "에러 메시지",
  "detail": "상세 내용 (선택)",
  "error_type": "validation | cache | file | handler | ...",
  "request_id": "uuid"
}
```

SDK가 이 응답을 파싱해 `ServiceError(message=error, status_code=<HTTP>, error_type=error_type)`로 던진다.

### `request_id`로 서버 로그 매칭

`request_id`는 Service 미들웨어가 매 요청마다 부여하는 UUID. 모든 서버 로그에 같은 ID가 박혀있어, 클라이언트가 받은 에러를 서버 로그와 1:1로 매칭할 수 있다.

```bash
# 예: ServiceError로 받은 request_id "a1b2c3d4-..."로 서버 로그 추출
grep "a1b2c3d4-" service.log
```

서버 로그 포맷, `[route]` / `[dedup]` 패턴, 각 단계 로그 의미는 [debugging-guide.md](../../debugging-guide.md) §2 (단계별 상세) 참고.

## LLM 오류는 캐시되지 않음

LLM 호출 중 예외가 발생하면 저장 단계로 진입하지 않아 캐시 문서가 생성되지 않는다.
