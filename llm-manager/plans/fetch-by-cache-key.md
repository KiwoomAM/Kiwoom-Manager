# Plan: cache_key로 직접 캐시 결과 조회 함수 추가 ✅ 구현 완료

## Context
client가 이미 `done_check`으로 받은 `cache_key`를 가지고 있을 때, 다시 `llm_input` → canonicalize → hash 과정 없이 **cache_key만으로** MongoDB에서 결과를 조회하고, 원래 LLM 호출 결과와 동일한 형식(`LLMResponse` 또는 `StreamChunk`)으로 반환하는 함수가 필요.

## 변경 파일

### 1. `llm_request/store.py` — `fetch_by_cache_key` 추가

import 추가: `LLMResponse`, `StreamChunk` (from llm_handler.models), `Iterator` (from collections.abc)

```python
FETCH_COLLECTION = "agent_llm"

def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
    as_stream: bool = False,
) -> tuple[LLMResponse, dict[str, Any]] | tuple[Iterator[StreamChunk], dict[str, Any]]:
    """cache_key로 MongoDB에서 캐시된 LLM 결과를 조회하여 반환한다.

    Args:
        cache_key: done_check에서 받은 SHA-256 캐시 키.
        database: DB명 (None이면 kw_ai).
        as_stream: True면 StreamChunk Iterator로 반환.

    Returns:
        (LLMResponse, meta) 또는 (Iterator[StreamChunk], meta).

    Raises:
        KeyError: cache_key에 해당하는 문서가 없을 때.
        ValueError: result 역직렬화 실패 시.
    """
    doc = cache_get(cache_key, FETCH_COLLECTION, database=database)

    if doc is None:
        logger.error("캐시 조회 실패: cache_key=%s 에 해당하는 문서 없음", cache_key[:16] + "...")
        raise KeyError(f"cache_key '{cache_key[:16]}...' 에 해당하는 캐시 문서가 없습니다.")

    cached_raw = doc.get("result", {})
    try:
        response = LLMResponse.model_validate(cached_raw)
    except Exception as e:
        logger.error("캐시 결과 역직렬화 실패: cache_key=%s", cache_key[:16] + "...", exc_info=True)
        raise ValueError(f"cache_key '{cache_key[:16]}...' 결과 역직렬화 실패") from e

    meta: dict[str, Any] = {
        "source": "cache",
        "cache_hit": True,
        "cache_key": cache_key,
        "metadata": doc.get("metadata", {}),
        "collection": FETCH_COLLECTION,
    }

    if as_stream:
        def _iter() -> Iterator[StreamChunk]:
            yield StreamChunk(
                text=response.text,
                finish_reason=response.finish_reason,
                usage=response.usage,
                function_calls=response.function_calls,
            )
        return _iter(), meta

    return response, meta
```

### 2. `llm_request/runner.py` — meta 키 `"meta"` → `"metadata"` 통일

`_build_live_meta` 함수 (line 130-139)에서 `"meta"` 키를 `"metadata"`로 변경:

```python
# before
"meta": prep.meta,

# after
"metadata": prep.meta,
```

### 3. `llm_request/__init__.py` — export 추가

- `from .store import fetch_by_cache_key` 추가
- `__all__`에 `"fetch_by_cache_key"` 추가

## Verification
- 기존 테스트 실행하여 깨지지 않는지 확인
- `fetch_by_cache_key` 동작 확인: 존재하는 key / 없는 key / as_stream 모드
- `_build_live_meta`의 `"meta"` → `"metadata"` 변경 후 기존 코드에서 `"meta"` 키로 접근하는 곳 없는지 확인
