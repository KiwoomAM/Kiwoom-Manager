"""handler_client — handler 서버 동기 클라이언트 (llm_manager 내부).

llm-handler 서버로의 HTTP 클라이언트 SDK. llm_manager 가 흡수해 내부화했으며,
공개 표면은 `llm_manager` 가 큐레이션한다 (생성 primitive 만 노출).

사용법(공개 경로):
    from llm_manager import call, stream, Part

    response = call("안녕하세요")
    print(response.text)

    response = call(["분석해줘", Part.from_uri("https://...", "application/pdf")])
"""

from collections.abc import Iterator

from ._common import ContentsType, _done_payload_var
from ._config import get_settings
from ._session import Session
from ._sync_client import SyncClient
from .exceptions import HandlerConnectionError, HandlerError, SDKError
from .models import (
    CacheRef,
    CodeBlock,
    CodeExecutionResultPart,
    CodeResult,
    Content,
    ContentPart,
    EmbeddingStatistics,
    EmbeddingValue,
    EmbedResponse,
    ExecutableCodePart,
    FileRef,
    FunctionCall,
    FunctionCallPart,
    FunctionResponse,
    Grounding,
    GroundingChunk,
    GroundingSegment,
    GroundingSupport,
    LLMResponse,
    Part,
    StreamChunk,
    TokenUsage,
    ToolCallInfo,
    ToolCallPart,
    ToolResponseInfo,
    ToolResponsePart,
)

__all__ = [
    # API
    "call",
    "stream",
    "embed",
    # 세션
    "Session",
    "session",
    # stream done event payload (raw stream 사용자용)
    "last_assistant_message",
    # 유틸리티
    "upload_file",
    "delete_file",
    "get_file",
    "list_files",
    "create_cache",
    "delete_cache",
    "get_cache",
    "list_caches",
    "update_cache",
    "configure",
    # 모델 — 응답
    "CacheRef",
    "CodeBlock",
    "CodeResult",
    "Content",
    "ContentPart",
    "ContentsType",
    "EmbeddingStatistics",
    "EmbeddingValue",
    "EmbedResponse",
    "FileRef",
    "FunctionCall",
    "Grounding",
    "GroundingChunk",
    "GroundingSegment",
    "GroundingSupport",
    "LLMResponse",
    "StreamChunk",
    "TokenUsage",
    "ToolCallInfo",
    "ToolResponseInfo",
    # 모델 — 입력 / Part
    "CodeExecutionResultPart",
    "ExecutableCodePart",
    "FunctionCallPart",
    "FunctionResponse",
    "Part",
    "ToolCallPart",
    "ToolResponsePart",
    # 예외
    "SDKError",
    "HandlerConnectionError",
    "HandlerError",
]

_instance: SyncClient | None = None


def _get_client() -> SyncClient:
    """싱글톤 SyncClient 반환."""
    global _instance
    if _instance is None:
        settings = get_settings()
        _instance = SyncClient(base_url=settings.handler_url, timeout=settings.timeout)
    return _instance


def configure(base_url: str | None = None, timeout: float | None = None) -> None:
    """SDK 클라이언트를 수동 설정.

    Args:
        base_url: Handler 서비스 URL (기본: 환경변수 또는 localhost:8100)
        timeout: HTTP 타임아웃 (초)
    """
    global _instance
    settings = get_settings()
    url = base_url or settings.handler_url
    t = timeout if timeout is not None else settings.timeout
    _instance = SyncClient(base_url=url, timeout=t)
    if timeout is not None:
        # 사용자 지정 timeout 기록 → lazy 초기화(ensure_configured)가 600 으로 덮어쓰지 않게.
        from llm_manager._internal import _config
        _config._user_timeout = timeout


def last_assistant_message() -> Content | None:
    """직전 ``stream()`` 의 ``assistant_message`` 를 반환 (없으면 ``None``).

    Raw ``stream()`` 사용자가 한 turn 누적 결과 (role=model + Part list) 를
    SDK internal (``_done_payload_var``) 직접 접근 없이 얻기 위한 헬퍼.
    ``Session.stream()`` 사용자는 ``pseudo_response.assistant_message`` 로
    자동 복원되므로 호출 불필요.

    Returns:
        직전 ``stream()`` 종료 시점에 handler 가 SSE ``done`` event 로 보낸
        assistant_message (``Content(role="model", content=list[Part])``).
        Stream 미호출 또는 handler 가 done event 미발송 (빈 응답 등) 시 ``None``.

    Notes:
        - 같은 thread 컨텍스트에서 호출 필수. 내부 저장소 ``_done_payload_var``
          는 ``ContextVar`` 라 thread 별 격리되어 동시 stream 호출 간섭 없음.
        - 새 ``stream()`` 진입 시 ``None`` 으로 reset 되므로, 반드시 직전 stream
          loop 종료 후 (다음 stream 호출 전) 에 읽을 것.
    """
    payload = _done_payload_var.get()
    if not isinstance(payload, dict):
        return None
    am_dict = payload.get("assistant_message")
    if not am_dict:
        return None
    return Content.model_validate(am_dict)


def call(
    contents: ContentsType | None = None,
    *,
    messages: list[Content] | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    seed: int | None = None,
    output: str | None = None,
    schema: type | dict | None = None,
    thinking: str | None = None,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
    history: list[dict] | None = None,
    cached_content: str | None = None,
    task_name: str | None = None,
    media_resolution: str | None = None,
) -> LLMResponse:
    """통합 LLM 호출 — 단일 응답.

    호출 형태 (mutually exclusive):
        1. call(contents=...)               — 이번 새 user 발화 (history 와 결합 가능)
        2. call(contents=..., history=...)  — user 발화 + 이전 dict 형식 history
        3. call(messages=[Content, ...])    — 전체 대화 명시 (advanced, type-safe)
    """
    return _get_client().call(
        contents,
        messages=messages,
        system=system,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        output=output,
        schema=schema,
        thinking=thinking,
        tools=tools,
        tool_config=tool_config,
        history=history,
        cached_content=cached_content,
        task_name=task_name,
        media_resolution=media_resolution,
    )


def stream(
    contents: ContentsType | None = None,
    *,
    messages: list[Content] | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    seed: int | None = None,
    output: str | None = None,
    schema: type | dict | None = None,
    thinking: str | None = None,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
    history: list[dict] | None = None,
    cached_content: str | None = None,
    task_name: str | None = None,
    media_resolution: str | None = None,
) -> Iterator[StreamChunk]:
    """통합 스트리밍 LLM 호출 — 호출 형태는 call() 과 동일."""
    return _get_client().stream(
        contents,
        messages=messages,
        system=system,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        output=output,
        schema=schema,
        thinking=thinking,
        tools=tools,
        tool_config=tool_config,
        history=history,
        cached_content=cached_content,
        task_name=task_name,
        media_resolution=media_resolution,
    )


def embed(
    contents: str | list[str],
    *,
    model: str | None = None,
    task_type: str | None = None,
    title: str | None = None,
    output_dimensionality: int | None = None,
    task_name: str | None = None,
) -> EmbedResponse:
    """텍스트 임베딩 생성."""
    return _get_client().embed(
        contents,
        model=model,
        task_type=task_type,
        title=title,
        output_dimensionality=output_dimensionality,
        task_name=task_name,
    )


def session(
    *,
    system: str | None = None,
    model: str | None = None,
) -> Session:
    """멀티턴 세션 생성."""
    return Session(_get_client(), system=system, model=model)


def upload_file(file_path: str, display_name: str | None = None, mime_type: str | None = None) -> FileRef:
    """파일 업로드."""
    return _get_client().upload_file(file_path, display_name, mime_type)


def create_cache(
    contents: ContentsType,
    *,
    model: str,
    system: str | None = None,
    display_name: str | None = None,
    ttl: int = 3600,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> CacheRef:
    """Context Cache 생성.

    Args:
        contents: 캐싱할 콘텐츠 (텍스트, 또는 텍스트/파일/Part 혼합 리스트)
        model: 사용할 모델명
        system: 시스템 지시사항
        display_name: 캐시 표시 이름 (최대 128자)
        ttl: TTL 초 단위 (기본 1시간)
        tools: 캐시 사용 시 자동 적용할 도구 정의
        tool_config: function calling 모드 ("auto", "any", "none")
    """
    return _get_client().create_cache(
        contents,
        model=model,
        system=system,
        display_name=display_name,
        ttl=ttl,
        tools=tools,
        tool_config=tool_config,
    )


def delete_cache(name: str) -> None:
    """Context Cache 삭제."""
    _get_client().delete_cache(name)


def list_caches() -> list[CacheRef]:
    """캐시 목록 조회."""
    return _get_client().list_caches()


def get_cache(name: str) -> CacheRef:
    """캐시 상세 조회."""
    return _get_client().get_cache(name)


def update_cache(name: str, ttl: int) -> CacheRef:
    """캐시 TTL 업데이트."""
    return _get_client().update_cache(name, ttl)


def delete_file(name: str) -> None:
    """파일 삭제 (404는 무시 — 멱등성)."""
    _get_client().delete_file(name)


def get_file(name: str) -> FileRef | None:
    """파일 조회. 존재하지 않으면 None 반환."""
    return _get_client().get_file(name)


def list_files() -> list[FileRef]:
    """파일 목록 조회."""
    return _get_client().list_files()
