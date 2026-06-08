# llm_handler Re-export

`llm_manager`만 설치해도 `llm_handler`의 모든 public API를 별도 import 없이 쓸 수 있다.
`llm_handler`를 직접 호출해야 하는 경우(예: 캐싱 흐름을 거치지 않고 LLM만 빠르게 호출)에 의존성 추가 없이 그대로 사용한다.

## 사용 패턴 3가지

### 방법 1 — flat import (sync)

```python
from llm_manager import call, stream, embed, configure, Part, LLMResponse

configure(base_url="http://192.7.9.45:8100")  # 기본값 내장, URL 다를 때만 필요

response = call("안녕하세요")
print(response.text)

for chunk in stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)

result = embed("텍스트", model="text-embedding-004")
```

### 방법 2 — `aio` 서브모듈 (async)

```python
from llm_manager import aio

aio.configure(base_url="http://192.7.9.45:8100")

response = await aio.call("안녕하세요")

async for chunk in aio.stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)

result = await aio.embed("텍스트")
```

`aio`는 `llm_handler.aio`를 그대로 re-export. `AsyncSession`, `configure`, `upload_file`, `create_cache` 등 sync와 동일 구성.

### 방법 3 — 모듈 접근

```python
from llm_manager import llm_handler

response = llm_handler.call("안녕하세요")              # sync
response = await llm_handler.aio.call("안녕하세요")   # async
llm_handler.configure(base_url="http://192.7.9.45:8100")
```

## re-export 목록

| 분류 | 이름 |
| --- | --- |
| 호출 | `call`, `stream`, `embed`, `session` |
| 파일 | `upload_file`, `delete_file`, `get_file`, `list_files` |
| Context Cache | `create_cache`, `delete_cache`, `get_cache`, `update_cache`, `list_caches` |
| 설정 | `configure` |
| 클래스 | `Session` |
| 모델 | `Part`, `LLMResponse`, `StreamChunk`, `FunctionCall`, `CacheRef`, `FileRef`, `TokenUsage`, `ContentsType` |
| 임베딩 모델 | `EmbedResponse`, `EmbeddingStatistics`, `EmbeddingValue` |
| 예외 | `SDKError`, `HandlerConnectionError`, `HandlerError` |
