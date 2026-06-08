"""llm_manager.aio — 비동기 공개 표면.

llm_manager 만 설치해도 핸들러 비동기 생성 primitive(call/stream/embed/session)를 쓸 수 있다.
캐시/파일 생명주기 ops(create_cache/upload_file/configure 등)는 매니저의 결정 역할이라
노출하지 않는다 — 캐시 만료 등은 `llm_manager.delete_cache` 같은 매니저 API 를 쓴다.

사용법:
    from llm_manager.aio import call, stream, Part
    response = await call("안녕하세요")

    # 또는
    from llm_manager import aio
    response = await aio.call("안녕하세요")
"""

from llm_manager._internal.handler_client.aio import (
    AsyncSession,
    CacheRef,
    ContentsType,
    EmbeddingStatistics,
    EmbeddingValue,
    EmbedResponse,
    FileRef,
    FunctionCall,
    HandlerConnectionError,
    HandlerError,
    LLMResponse,
    Part,
    SDKError,
    StreamChunk,
    TokenUsage,
    call,
    embed,
    session,
    stream,
)

__all__ = [
    # 생성 primitive
    "call",
    "stream",
    "embed",
    "session",
    "AsyncSession",
    # 모델
    "CacheRef",
    "ContentsType",
    "EmbeddingStatistics",
    "EmbeddingValue",
    "EmbedResponse",
    "FileRef",
    "FunctionCall",
    "LLMResponse",
    "Part",
    "StreamChunk",
    "TokenUsage",
    # 예외
    "SDKError",
    "HandlerConnectionError",
    "HandlerError",
]
