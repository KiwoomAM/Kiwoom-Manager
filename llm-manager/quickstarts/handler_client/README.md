# handler_client SDK Quickstarts

`llm_manager._internal.handler_client` (handler 생성·파일·캐시 클라이언트 SDK) 의 실행 가능한 예제.
**내부 SDK 예제** — 매니저(캐싱) API 예제는 [`../llm_manager/`](../llm_manager/) 참고.

> import: `from llm_manager._internal.handler_client import ...` (또는 `from llm_manager._internal import handler_client as llm_handler`).
> 각 노트북 첫 셀의 `llm_handler.configure(base_url=...)` 주석을 해제하거나 `LLM_HANDLER_URL` env 로 Handler URL 을 설정하세요.

| # | 노트북 | 내용 |
|---|--------|------|
| 01 | [Basic Usage](01_basic_usage.ipynb) | `call()`, 시스템 프롬프트, 온도/토큰, 멀티턴, 스트리밍, Thinking |
| 02 | [구조화된 출력](02_structured_output.ipynb) | JSON dict, Pydantic 모델, Enum 분류 |
| 03 | [Tool Calling](03_tool_calling.ipynb) | Function Declaration, Multi-turn, Built-in Tools |
| 04 | [파일 기반 질의 & 관리](04_files.ipynb) | `upload_file`, `get_file`, `list_files`, `delete_file`, `Part` |
| 05 | [에러 핸들링](05_error_handling.ipynb) | `HandlerError`, `error_type` 구분 |
| 06 | [Context Cache](06_context_cache.ipynb) | `create_cache`, `get_cache`, `update_cache`, `delete_cache` |
| 07 | [응답 객체](07_response_objects.ipynb) | `LLMResponse`, `to_dict()`, `finish_reason` |
| 08 | [텍스트 임베딩](08_embeddings.ipynb) | `embed()`, 배치, task_type, 차원 축소, 코사인 유사도 |
