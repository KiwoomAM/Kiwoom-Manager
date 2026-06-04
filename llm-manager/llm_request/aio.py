"""llm_request.aio — llm_handler.aio re-export.

llm_request만 설치해도 llm_handler의 비동기 API를 직접 사용할 수 있다.

사용법:
    from llm_request.aio import call, stream, Part
    response = await call("안녕하세요")

    # 또는
    from llm_request import aio
    response = await aio.call("안녕하세요")
"""

from llm_handler.aio import (
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
    configure,
    create_cache,
    embed,
    list_caches,
    session,
    stream,
    upload_file,
)

__all__ = [
    # API
    "call",
    "stream",
    "embed",
    # 세션
    "AsyncSession",
    "session",
    # 유틸리티
    "upload_file",
    "create_cache",
    "list_caches",
    "configure",
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
