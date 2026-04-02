1# LLM 호출 실패 결과 MongoDB 저장

## 목적

LLM 호출(`llm-handler`)이 오류로 실패했을 때, 해당 에러 정보를 MongoDB에 저장한다.
단, 에러 문서는 캐시 조회에서 제외되어야 한다 (같은 입력으로 재호출 시 에러 캐시가 반환되면 안 됨).

## 설계

### `status` 필드 도입

모든 저장 문서에 `status` 필드를 추가하여 성공/실패를 구분한다.

| status | 의미 | 캐시 조회 대상 |
|--------|------|---------------|
| `"success"` | 정상 응답 | O |
| `"error"` | 호출 실패 | X |

### 저장되는 에러 문서 구조

```json
{
  "_id": "<cache_key>",
  "status": "error",
  "canonical_json": "<정규화된 입력 JSON>",
  "error": {
    "type": "HandlerError",
    "message": "[provider] 503 Service Unavailable",
    "status_code": 503,
    "error_type": "provider"
  },
  "metadata": { "service_name": "...", "model": "..." },
  "requested_at": "2026-04-01T12:00:00+00:00",
  "created_at": "2026-04-01T12:00:05+00:00",
  "elapsed_ms": 5000
}
```

### 기존 성공 문서 구조 변경

```json
{
  "_id": "<cache_key>",
  "status": "success",
  "canonical_json": "...",
  "result": { "text": "...", "model": "...", ... },
  "metadata": { ... },
  "created_at": "...",
  "requested_at": "...",
  "elapsed_ms": 123
}
```

## 파일별 변경 사항

### 1. `llm_request/store.py`

#### `save_result()` — `"status": "success"` 필드 추가

```python
doc: dict[str, Any] = {
    "_id": cache_key,
    "status": "success",          # 추가
    "canonical_json": canonical_json,
    "result": result_dict,
    "metadata": meta,
    "created_at": now.isoformat(),
}
```

#### `save_error()` — 신규 함수

에러 정보를 `"status": "error"` 문서로 저장한다.

```python
def save_error(
    cache_key: str,
    canonical_json: str,
    error: Exception,
    meta: dict,
    collection: str,
    *,
    database: str | None = None,
    requested_at: datetime | None = None,
) -> None:
    """LLM 호출 실패 정보를 캐시 문서로 저장한다."""
    now = datetime.now(UTC)

    error_detail: dict[str, Any] = {
        "type": type(error).__name__,
        "message": str(error),
    }
    if hasattr(error, "status_code"):
        error_detail["status_code"] = error.status_code
    if hasattr(error, "error_type"):
        error_detail["error_type"] = error.error_type

    doc: dict[str, Any] = {
        "_id": cache_key,
        "status": "error",
        "canonical_json": canonical_json,
        "error": error_detail,
        "metadata": meta,
        "created_at": now.isoformat(),
    }
    if requested_at is not None:
        doc["requested_at"] = requested_at.isoformat()
        doc["elapsed_ms"] = round((now - requested_at).total_seconds() * 1000)

    cache_set(doc, collection, database=database)
```

#### `cache_get()` — 에러 문서 제외 조건 추가

```python
def cache_get(cache_key, collection, database=None):
    col = _get_collection(collection, database=database)
    return col.find_one({"_id": cache_key, "status": {"$ne": "error"}})
```

### 2. `llm_request/runner.py`

4개 public 함수 모두 LLM 호출부를 try/except로 감싸고, 실패 시 `save_error()` 호출 후 re-raise.

#### `llm_request()` (sync)

```python
def llm_request(llm_input, *, cache_mode=True, save_mode=True, ...):
    prep = _prepare(...)
    if prep.cached_output:
        return prep.cached_output
    try:
        res = sync_call(**prep.llm_input)
    except Exception as exc:
        _save_error_if_needed(prep, exc, database=database)
        raise
    return _finalize(prep, res, database=database)
```

#### `llm_request_async()` (async) — 동일 패턴

```python
async def llm_request_async(llm_input, ...):
    prep = _prepare(...)
    if prep.cached_output:
        return prep.cached_output
    try:
        res = await aio_call(**prep.llm_input)
    except Exception as exc:
        _save_error_if_needed(prep, exc, database=database)
        raise
    return _finalize(prep, res, database=database)
```

#### `llm_request_stream()` / `llm_request_stream_async()` — 제너레이터 내부 try/except

```python
def _stream() -> Iterator[StreamChunk]:
    accumulated: list[StreamChunk] = []
    try:
        for chunk in sync_stream(**prep.llm_input):
            accumulated.append(chunk)
            yield chunk
    except Exception as exc:
        _save_error_if_needed(prep, exc, database=database)
        raise
    _finalize_stream(prep, accumulated, database=database)
```

#### `_save_error_if_needed()` — 내부 헬퍼

```python
def _save_error_if_needed(
    prep: _Prepared, exc: Exception, *, database: str | None = None,
) -> None:
    """save_mode가 켜져 있고 dc가 있으면 에러를 MongoDB에 저장한다."""
    if not prep.save_mode or prep.dc is None:
        return
    try:
        save_error(
            prep.dc.cache_key, prep.dc.canonical_json,
            exc, prep.meta, prep.dc.collection,
            database=database, requested_at=prep.requested_at,
        )
    except Exception:
        logger.exception(
            "에러 결과 저장 실패: cache_key=%s",
            prep.dc.cache_key[:16] + "...",
        )
```

## 하위 호환성

- 기존에 저장된 문서에는 `status` 필드가 없음
- `cache_get()`의 `"status": {"$ne": "error"}` 조건은 `status` 필드가 없는 문서도 통과시킴 (`$ne`는 필드 부재 시 해당 값이 아닌 것으로 판단)
- 따라서 기존 캐시 문서는 마이그레이션 없이 정상 동작

## 에러 저장과 캐시 키 충돌

동일 입력으로 첫 번째 호출이 실패(에러 저장) → 두 번째 호출이 성공하는 경우:
- `cache_set()`은 `replace_one(..., upsert=True)`이므로 에러 문서가 성공 문서로 **덮어쓰기**됨
- 의도한 동작: 성공 결과가 에러를 대체하므로 이후 캐시 히트 정상 작동
