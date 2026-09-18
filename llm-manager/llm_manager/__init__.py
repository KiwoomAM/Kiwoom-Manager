"""llm-manager SDK — LLM 응답 캐싱 manager (순수 SDK, 서버 없음).

mdb·redis·file manager 와 llm-handler 를 조합해 LLM 응답을 캐싱하는 manager. 과거에는
별도 FastAPI 서버(service/)를 httpx 로 호출했으나, 이제 로직을 `_internal/` 로
흡수해 같은 프로세스에서 직접 실행한다. public API 시그니처는 불변.

사용법:
    from pathlib import Path
    from file_manager import FileManagerHandler
    from llm_manager import llm_request_async, StoredFile

    fm = FileManagerHandler()
    p = Path("report.pdf")
    sf_dict = await fm.register_or_get(
        bytes_data=p.read_bytes(),
        metadata=fm.build_metadata(
            file_name=p.name,
            file_mtime=int(p.stat().st_mtime),
            file_size=p.stat().st_size,
        ),
    )
    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        stored_files=[StoredFile(**sf_dict)],
        database="kw_ai_dev",   # MongoDB database 선택 (kw_ai | kw_ai_dev)
    )
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Iterator
from typing import Any, Protocol, TypedDict

from llm_manager import aio as aio  # noqa: PLC0414  (llm_manager.aio 서브모듈 노출)
from llm_manager._internal import chat, runner
from llm_manager._internal._config import ensure_configured
from llm_manager._internal.context_cache_lifecycle import expire_caches, extend_caches
from llm_manager._internal.done_check import done_check as _done_check
from llm_manager._internal.file_processing import preprocess

# handler_client(흡수한 HTTP 클라이언트)는 llm_manager 내부. 공개 표면은 매니저가 큐레이션한다:
#   - 생성 primitive (매니저가 캐싱 의견 없음): call/stream/embed/session
#   - 모델·타입·예외 (항상 공개)
# 캐시/파일 생명주기 ops (create_cache/upload_file 등)는 매니저의 결정 역할이라
# 공개하지 않는다 — raw 노출 시 매니저의 Redis 추적·keying·grace 만료와 desync 됨.
# 단, configure(base_url/timeout) 는 HTTP 클라이언트 설정용이라 공개한다 (캐시 상태 무관).
from llm_manager._internal.handler_client import (
    CacheRef,
    Content,
    ContentsType,
    EmbeddingStatistics,
    EmbeddingValue,
    EmbedResponse,
    FileRef,
    FunctionCall,
    FunctionResponse,
    HandlerConnectionError,
    HandlerError,
    LLMResponse,
    Part,
    Session,
    StreamChunk,
    TokenUsage,
    call,
    configure,
    embed,
    session,
    stream,
)
from llm_manager._internal.handler_client._common import (
    STREAM_CONTAINER_FIELDS as _STREAM_CONTAINER_FIELDS,
)
from llm_manager._internal.handler_client._common import (  # 내부 상수 — 밑줄로 받아 공개 표면을 안 넓힌다
    STREAM_LIST_FIELDS as _STREAM_LIST_FIELDS,
)
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.store import fetch_by_cache_key as _fetch_by_cache_key
from llm_manager.exceptions import (
    CacheError,
    CacheKeyNotFoundError,
    FileResolveError,
    FileTooLargeError,
    FileUploadError,
    LLMManagerError,
    PdfPageTooLargeError,
    UnsupportedFileTypeError,
    ValidationError,
)
from llm_manager.models import (
    ChatMessage,
    DoneCheckResult,
    ExpireFailure,
    ExpireResult,
    FileKeyingMeta,
    ResponseMeta,
    ResponseResult,
    StoredFile,
)

__all__ = [
    # llm_manager 자체 API
    "llm_request_async",
    "llm_request",
    "llm_request_stream_async",
    "llm_request_stream",
    "StreamIterator",
    "llm_chat_async",
    "llm_chat_stream_async",
    "file_preprocessing",
    "file_preprocessing_async",
    "done_check",
    "fetch_by_cache_key",
    "delete_context_cache",
    "delete_context_cache_async",
    "update_context_cache",
    "update_context_cache_async",
    "close",
    "Timings",
    # 공개 타입
    "FileKeyingMeta",
    "StoredFile",
    "ChatMessage",
    "ResponseMeta",
    "ResponseResult",
    "DoneCheckResult",
    "ExpireResult",
    "ExpireFailure",
    # 공개 예외
    "LLMManagerError",
    "CacheError",
    "FileUploadError",
    "ValidationError",
    "UnsupportedFileTypeError",
    "FileTooLargeError",
    "PdfPageTooLargeError",
    "CacheKeyNotFoundError",
    "FileResolveError",
    # 비동기 표면 (llm_manager.aio)
    "aio",
    # handler 생성 primitive 재노출 (매니저가 캐싱 의견 없는 stateless 호출)
    "call",
    "stream",
    "embed",
    "session",
    "Session",
    # handler 클라이언트 설정 (HTTP base_url/timeout — 캐시 상태 무관)
    "configure",
    # handler 모델·타입 (타입힌트/Part 생성용 — 항상 공개)
    "CacheRef",
    "Content",
    "ContentsType",
    "EmbeddingStatistics",
    "EmbeddingValue",
    "EmbedResponse",
    "FileRef",
    "FunctionCall",
    "FunctionResponse",
    "LLMResponse",
    "Part",
    "StreamChunk",
    "TokenUsage",
    # handler 예외
    "HandlerConnectionError",
    "HandlerError",
]


logger = logging.getLogger(__name__)


async def close() -> None:
    """하위호환 no-op. (httpx 클라이언트 제거됨 — 정리할 SDK 소유 커넥션 없음.)

    과거엔 SDK가 소유한 httpx AsyncClient 풀을 닫았다. 이제 백엔드 호출은 각
    클라이언트 SDK(mdb·redis·file manager + llm-handler)가 자체 풀을 관리하므로
    llm-manager 가 닫을 자원이 없다. agent 종료 전 호출해도 안전(무동작).
    """
    return None


# ── Timings ───────────────────────────────────────────────────


class Timings(TypedDict, total=False):
    """요청 하나의 시간 관련 설정 묶음.

    모든 키는 선택 — 생략 시 `_DEFAULT_TIMINGS` 적용.
    """

    http_timeout: float
    """(deprecated) 서버 시절 SDK→서버 HTTP 타임아웃. 서버 제거로 무의미 — 호환을 위해
    키만 유지한다. LLM 호출 타임아웃은 llm-handler 설정(600s)이 담당."""

    mdb_ttl: int | None
    """MDB 응답 캐시 수명 (초). None = 무기한. 기본 None."""

    context_cache_ttl: int | None
    """Gemini Context Cache 수명 (초). None = 생성 안 함 (opt-in). 기본 None."""

    standby_ttl: int
    """Cache stampede dedup standby TTL + waiter 대기 timeout (초). 기본 600."""


_DEFAULT_TIMINGS: Timings = {
    "http_timeout": 600.0,
    "mdb_ttl": None,
    "context_cache_ttl": None,
    "standby_ttl": 600,
}


def _resolve_timings(timings: Timings | None) -> Timings:
    """사용자 timings와 기본값 병합."""
    if not timings:
        return dict(_DEFAULT_TIMINGS)  # type: ignore[return-value]
    return {**_DEFAULT_TIMINGS, **timings}  # type: ignore[return-value]


# ── Public API ─────────────────────────────────────────────────


async def llm_request_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[LLMResponse, ResponseMeta]:
    """단일 LLM 요청 실행 (async).

    Args:
        stored_files: file-manager 에 등록된 파일 참조 리스트.
        database: MongoDB database 선택 (예: "kw_ai" | "kw_ai_dev"). None 이면 기본값.
        timings: 시간 관련 설정 묶음 (일부 키만 지정 가능).
        debug: True면 meta.route_reason 에 처리 경로 포함.
    """
    await asyncio.to_thread(ensure_configured)
    t = _resolve_timings(timings)
    response, meta = await runner.llm_request_async(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=t["mdb_ttl"],
        database=database,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
        debug=debug,
    )
    return response, ResponseMeta(**meta)


def llm_request(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[LLMResponse, ResponseMeta]:
    """단일 LLM 요청 실행 (sync). llm_request_async 의 동기 래퍼."""
    return asyncio.run(llm_request_async(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        database=database,
        timings=timings,
        debug=debug,
    ))


async def llm_request_stream_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[AsyncGenerator[dict[str, Any], None], dict[str, Any]]:
    """스트리밍 LLM 요청 (async).

    (이벤트 dict 스트림, meta) 튜플 반환. 각 이벤트는 항상 세 키를 갖는다 —
    {"type": "chunk", "text": ..., "finish_reason": ...}. 아래 키는 **그 청크에 값이 있을
    때만** 붙으므로, 쓰기 전에 있는지 확인해야 한다.

        usage             TokenUsage
        function_calls    모델이 요청한 client-side 함수 호출
        tool_calls        Gemini server-side tool 호출 흔적 (google_search 등)
        tool_responses    그 tool 의 응답
        code_blocks       code_execution 이 생성한 코드
        code_results      그 코드의 실행 결과
        grounding         google_search 인용 출처
        url_context       url_context 도구가 읽은 URL

    값은 모두 `model_dump()` 된 dict 이라 그대로 `json.dumps` 로 떨어진다.

    **리스트 5종(function_calls·tool_calls·tool_responses·code_blocks·code_results)은
    소비자가 이어붙여 누적해야 한다.** 청크마다 자기 몫만 실려 오기 때문이다 — 병렬 함수
    호출 N 개는 N 개 청크에 1 개씩 나뉘어 온다(llm-handler 계약). 덮어쓰기로 구현하면
    마지막 청크 것만 남아 **앞의 호출이 조용히 사라진다.** grounding·url_context 도 청크별로
    실려 오며, 이 계층은 어느 것도 병합하지 않는다.

    **캐시 히트면 스트림 전체가 이벤트 1 개다.** 저장해 둔 응답을 청크 하나로 되돌리므로
    `text`(전문)와 도구 필드가 **같은 이벤트에** 실린다. 라이브에서는 도구 필드가 text 없는
    청크로 따로 오는 것이 보통이라, `if ev.get("text"): 토큰 else: 도구` 로 짜면 캐시 히트마다
    함수 호출을 놓친다. 두 필드를 독립적으로 보라.

    **캐시 히트에서 두 부류의 결과가 갈린다.** 리스트 5 종은 저장 경로가 이어붙여 두므로
    캐시 히트 이벤트 하나가 라이브를 누적한 것과 같은 집합이 된다. 반면 `grounding`·
    `url_context` 는 저장 경로가 **마지막 non-None 으로 덮어써서**, 여러 번 나뉘어 온 턴이면
    캐시 히트에는 마지막 것만 남는다. 즉 같은 입력이라도 캐시 히트 여부에 따라 인용 출처
    집합이 달라질 수 있다 (별건 — 이슈 #99 범위 밖, `docs/37` §3.2·§6).

    mid-stream 에러는 async for 가 그대로 raise (HandlerError 등).

    ⚠️ **반환된 스트림은 끝까지 소비하거나 `aclose()` 해야 한다.** 이 스트림은 dedup standby
    리더 토큰을 쥐고 있고, 그 해제가 스트림의 `finally` 에 걸려 있다. 받아 놓고 순회하지 않으면
    같은 입력의 다른 요청이 최대 `standby_ttl`(기본 600초)만큼 대기한다 — 대기자는 리더가
    죽었는지 일하는 중인지 구분할 수 없어 TTL 을 다 기다린다 (docs/36 §3.2).
    """
    await asyncio.to_thread(ensure_configured)
    t = _resolve_timings(timings)
    chunk_stream, meta = await runner.llm_request_stream_async(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=t["mdb_ttl"],
        database=database,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
        debug=debug,
    )

    async def _events() -> AsyncGenerator[dict[str, Any], None]:
        # `async for` 는 빠져나갈 때 안쪽 이터레이터를 닫지 않는다. 안쪽 스트림의 finally 에
        # **standby 리더 토큰 해제**가 걸려 있으므로(runner.py 의 `_leader_stream`), 여기서
        # 명시적으로 닫아 준다. 없으면 해제가 이벤트 루프의 asyncgen 파이널라이저 시점까지
        # 미뤄져 GC 타이밍에 달린다 — 그동안 같은 입력의 다른 요청이 대기한다 (docs/36 §4.1).
        try:
            async for chunk in chunk_stream:
                event: dict[str, Any] = {
                    "type": "chunk",
                    "text": chunk.text,
                    "finish_reason": chunk.finish_reason,
                }
                if chunk.usage:
                    event["usage"] = chunk.usage.model_dump()
                # 나머지 필드도 통과시킨다 (이슈 #99). `usage` 와 같이 **값이 있을 때만**
                # 넣어 기존 소비자의 이벤트 모양을 바꾸지 않는다. 대상 분류는
                # _common 의 두 상수가 SSOT — 여기서 다시 나열하지 않는다.
                for name in _STREAM_LIST_FIELDS:
                    items = getattr(chunk, name)
                    if items:                       # None 과 빈 리스트를 함께 거른다
                        event[name] = [item.model_dump() for item in items]
                for name in _STREAM_CONTAINER_FIELDS:
                    obj = getattr(chunk, name)
                    if obj is not None:             # None 만 거른다 — 빈 Grounding() 은 걸러지지 않는다
                        event[name] = obj.model_dump()
                yield event
        finally:
            await chunk_stream.aclose()

    return _events(), meta


class StreamIterator(Protocol):
    """sync 스트리밍의 반환 계약 — **소비자가 확정 해제할 수 있어야 한다** (docs/36 §3.1).

    평범한 이터레이터가 아니라 **자원(이벤트 루프 + dedup standby 리더 토큰)을 쥔 이터레이터**다.
    그래서 `close()` 와 `with` 를 계약에 포함한다 — docstring 이 "닫아라" 고만 하고 타입에
    없으면, 타입체커를 쓰는 성실한 소비자일수록 그 지시를 못 따른다(PR #100 리뷰).

    구현(`_SyncStream`)이 아니라 이 계약을 내보내므로, 나중에 sync 판을 갈아 끼워도
    호출부는 바뀌지 않는다.
    """

    def __iter__(self) -> Iterator[dict[str, Any]]: ...
    def __next__(self) -> dict[str, Any]: ...
    def close(self) -> None: ...
    def __enter__(self) -> StreamIterator: ...
    def __exit__(self, *exc_info: object) -> None: ...


class _SyncStream:
    """async 이벤트 스트림을 sync 이터레이터로 노출한다 (docs/36 §3.1).

    `asyncio.run` 은 코루틴을 **끝까지 돌리고 루프를 닫는다.** 그래서 그것으로는 chunk 를
    도중에 건네줄 수 없어 전부 모으게 된다. 여기서는 루프를 **열어 둔 채** 소비자가
    `next()` 를 부를 때마다 `run_until_complete(__anext__())` 로 딱 한 걸음씩만 돌린다.

    이 객체가 **이벤트 루프를 소유한다.** 따라서 어떤 경로로 끝나든 반드시 회수해야 한다:
    끝까지 소비 / 중간 포기 / 예외 / 한 번도 소비 안 함(→ `__del__`). `close()` 는 멱등이다.
    """

    __slots__ = ("_loop", "_agen", "_closed")

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        agen: AsyncGenerator[dict[str, Any], None],
    ):
        self._loop = loop
        self._agen = agen
        self._closed = False

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self

    def __next__(self) -> dict[str, Any]:
        if self._closed:
            # 이미 끝났거나 닫힌 이터레이터는 StopIteration 을 돌려줘야 한다.
            # 그러지 않으면 닫힌 루프에서 RuntimeError 가 나 `next(it, default)`·재순회·
            # itertools 조합 같은 평범한 소비 패턴이 깨진다 (PR #100 리뷰).
            raise StopIteration
        try:
            return self._loop.run_until_complete(self._agen.__anext__())
        except StopAsyncIteration:
            self.close()
            raise StopIteration from None
        except BaseException:
            # mid-stream 실패도 자원은 회수하고 예외는 그대로 올린다 (FAIL-LOUD).
            self.close()
            raise

    def close(self) -> None:
        """스트림과 루프를 회수한다. 여러 번 불러도 안전하다."""
        if self._closed:
            return
        self._closed = True
        try:
            # 안쪽 스트림의 finally(= standby 리더 토큰 해제)를 여기서 돌린다.
            self._loop.run_until_complete(self._agen.aclose())
        except Exception:
            # 전송 계층 teardown(httpx 등)이 던질 수 있다. 삼키되 루프 회수는 계속한다 —
            # 여기서 예외가 밖으로 나가면 아래 정리가 통째로 날아가 루프가 샌다.
            logger.warning("sync 스트림 정리 중 aclose 실패", exc_info=True)
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                # dedup 이 asyncio.to_thread 를 쓰므로 executor 스레드가 생긴다.
                # asyncio.run 이 대신 해 주던 정리를 수동 루프에서는 직접 해야 한다.
                self._loop.run_until_complete(self._loop.shutdown_default_executor())
            finally:
                self._loop.close()

    def __enter__(self) -> _SyncStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        """**안전망일 뿐 계약이 아니다.**

        호출 시점을 우리가 고르지 못한다. 같은 스레드에서 다른 이벤트 루프가 돌고 있으면
        `close()` 의 `run_until_complete` 가 거부당해 정리가 실패하고(FastAPI 가
        `run_in_threadpool` 로 부른 뒤 루프 스레드에서 수거되는 구성이 그렇다), 인터프리터
        종료 중에도 마찬가지다(docs/36 §4.3). 확정 해제가 필요하면 `with` 나 `close()` 를 쓴다.
        """
        try:
            self.close()
        except Exception:  # pragma: no cover - 종료 경로 방어
            pass


def llm_request_stream(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[StreamIterator, dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). `llm_request_stream_async` 의 동기 래퍼.

    chunk 가 도착하는 대로 하나씩 돌려준다(docs/36).

    **이벤트 모양과 누적 규칙은 `llm_request_stream_async` 를 보라** — 같은 이벤트 dict 를
    주므로 리스트 5 종을 덮어쓰면 sync 에서도 똑같이 병렬 함수 호출을 잃는다.

    ⚠️ **반환된 이터레이터는 끝까지 소비하거나 `close()` 해야 한다** (`with` 권장). 방치하면
    같은 입력의 다른 요청이 최대 `standby_ttl`(기본 600초) 만큼 대기한다 — 안쪽 스트림이
    그동안 dedup standby 리더 토큰을 쥐고 있기 때문이다.

    ⚠️ **다만 한 번도 순회하지 않은 경우에는 `close()` 로도 그 토큰이 풀리지 않는다.**
    토큰 획득은 제너레이터 **바깥**에서 일어나는데 해제는 **안쪽 `finally`** 에 있고,
    시작조차 안 된 async 제너레이터에는 실행할 `finally` 가 없기 때문이다(PEP 525).
    이 비대칭은 `llm_request_stream_async` 도 같다. 해소하려면 토큰 획득 시점을 옮겨야 하며
    별건이다 (docs/36 §3.2·§7). **받았으면 최소한 한 번은 순회하라.**

    ⚠️ **이미 실행 중인 이벤트 루프 안에서는 부를 수 없다.** async 컨텍스트에서는
    `llm_request_stream_async` 를 쓴다.
    """
    loop = asyncio.new_event_loop()
    try:
        agen, meta = loop.run_until_complete(
            llm_request_stream_async(
                llm_input,
                stored_files=stored_files,
                cache_mode=cache_mode,
                save_mode=save_mode,
                database=database,
                timings=timings,
                debug=debug,
            )
        )
    except BaseException:
        # 준비 단계에서 실패해도 이미 만든 루프는 흘리지 않는다.
        loop.close()
        raise
    return _SyncStream(loop, agen), meta


async def llm_chat_stream_async(
    messages: list[ChatMessage],
    *,
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
    task_name: str | None = None,
    media_resolution: str | None = None,
) -> AsyncGenerator[StreamChunk, None]:
    """대화 스트리밍 호출 (async). 청크를 `StreamChunk` 객체로 바로 내보낸다 (docs/38).

    `async for chunk in llm_chat_stream_async(messages, ...)` 로 쓴다. 인자는 `messages`
    외에 밑단 `aio.stream` 과 같다. `service_name` 은 env `SERVICE_NAME` 에서 넣고, 없으면
    요청 전에 `ValidationError` 를 낸다. 응답 캐시·저장·Context Cache·meta 는 없다.

    **첨부.** 발화의 원래 위치에 `StoredFile` 을 넣으면 매니저가 Files API 주소 조각으로
    바꿔 보낸다(분할된 PDF 는 조각 여러 개). 파일마다 60초 상한이 있고, 넘기거나 실패한
    파일만 `[{file_name}: 첨부를 불러오지 못했습니다]` 로 바뀐 채 호출은 계속된다.

    **시간 상한.** 첨부 해소는 첫 청크 전에 이 제너레이터 안에서 일어난다. 호출을
    `asyncio.timeout` 으로 감싸면 그 상한이 해소 시간까지 잡으므로, 첨부가 Redis 에 없는
    대화(처음 붙였거나 47시간이 지난 것)를 덮을 만큼 잡아야 한다.
    """
    await asyncio.to_thread(ensure_configured)
    call_kwargs = {
        "system": system, "model": model, "temperature": temperature,
        "max_tokens": max_tokens, "seed": seed, "output": output, "schema": schema,
        "thinking": thinking, "tools": tools, "tool_config": tool_config,
        "task_name": task_name, "media_resolution": media_resolution,
    }
    # `async for` 는 빠져나갈 때 안쪽 제너레이터를 닫지 않는다 — 소비자가 도중에 그만두면
    # 밑단 HTTP 스트림이 GC 시점까지 남으므로 여기서 닫는다.
    async with contextlib.aclosing(chat.stream_chat(messages, call_kwargs)) as chunks:
        async for chunk in chunks:
            yield chunk


async def llm_chat_async(
    messages: list[ChatMessage],
    *,
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
    task_name: str | None = None,
    media_resolution: str | None = None,
) -> LLMResponse:
    """대화 호출 (async). `LLMResponse` 하나를 돌려준다 (docs/38).

    인자·첨부 처리·시간 상한은 `llm_chat_stream_async` 와 같다.
    """
    await asyncio.to_thread(ensure_configured)
    call_kwargs = {
        "system": system, "model": model, "temperature": temperature,
        "max_tokens": max_tokens, "seed": seed, "output": output, "schema": schema,
        "thinking": thinking, "tools": tools, "tool_config": tool_config,
        "task_name": task_name, "media_resolution": media_resolution,
    }
    return await chat.call_chat(messages, call_kwargs)


async def file_preprocessing_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    timings: Timings | None = None,
) -> dict[str, str] | list[list[str]]:
    """파일 전처리만 실행 (async) — 업로드/Context Cache 생성, LLM 호출 없음.

    `llm_request` 와 달리 LLM 응답 캐시 조회·LLM 호출을 하지 않고, stored_files 를
    files_api 로 업로드(+ opt-in Context Cache 생성)만 한다.

    Args:
        llm_input: `model`/`system`/`tools`/`tool_config` 만 사용(context cache 재료).
            LLM 을 부르지 않으므로 `contents` 는 보지 않는다.
        stored_files: file-manager 에 등록된 파일 참조. 여러 개 허용.
        timings: `context_cache_ttl`(opt-in) + `standby_ttl` 만 의미 있음.

    Returns:
        - context cache 모드(`context_cache_ttl` 지정 + 생성 성공)
          → ``{"context_cache_id": <hash_key>, "cached_content_name": <gemini cache id>}``
        - files_api-only / too-small fail-soft → ``(n, ?)`` 파일별 URI 리스트
    """
    await asyncio.to_thread(ensure_configured)
    t = _resolve_timings(timings)
    return await preprocess.file_preprocessing_async(
        llm_input,
        stored_files=stored_files,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
    )


def file_preprocessing(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    timings: Timings | None = None,
) -> dict[str, str] | list[list[str]]:
    """파일 전처리만 실행 (sync). file_preprocessing_async 의 동기 래퍼."""
    return asyncio.run(file_preprocessing_async(
        llm_input,
        stored_files=stored_files,
        timings=timings,
    ))


async def done_check(
    llm_input: dict,
    *,
    files: list[FileKeyingMeta] | None = None,
    database: str | None = None,
) -> DoneCheckResult:
    """MDB 캐시(LLM 응답) 존재 여부 확인.

    files: FileKeyingMeta 리스트 (hashing 3필드만). bytes 불필요.
    database: MongoDB database 선택 (kw_ai | kw_ai_dev 등).
    """
    await asyncio.to_thread(ensure_configured)
    if files:
        # FileKeyingMeta → checksum 리스트로 변환 후 cache_key 계산용으로 주입
        # (실제 LLM 호출엔 들어가지 않고 keying.canonicalize 에서만 사용)
        checksums = [make_file_checksum(m) for m in files]
        llm_input = {**llm_input, "file_checksums": checksums}

    result = _done_check(llm_input, lookup_cache=True, database=database)
    return DoneCheckResult(
        cache_key=result.cache_key,
        canonical_json=result.canonical_json,
        cached=result.cached,
        cache_hit=result.cached is not None,
        collection=result.collection,
    )


async def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
) -> tuple[LLMResponse, ResponseMeta]:
    """캐시 키로 결과 직접 조회. 없으면 CacheKeyNotFoundError."""
    await asyncio.to_thread(ensure_configured)
    try:
        response, meta = _fetch_by_cache_key(cache_key, database=database)
    except KeyError as e:
        raise CacheKeyNotFoundError(str(e)) from e
    return response, ResponseMeta(**meta)


def _reject_cached_content_name(ids: list[str]) -> None:
    """Gemini cache id(`cachedContents/...`)를 hash_key 자리에 넣은 실수를 fail-fast.

    delete/update_context_cache 는 `context_cache_id`(hash_key)만 받는다. Gemini 이름은
    Redis 키 매칭이 안 돼 조용한 no-op(만료/연장 안 됐는데 success)이 되므로 즉시 막는다.
    file_preprocessing 이 dict 로 두 id 를 노출하므로 혼동 가능 → 가드 필요 (docs/26 §4.5).
    """
    bad = [i for i in ids if i.startswith("cachedContents/")]
    if bad:
        raise ValueError(
            f"context_cache_id(hash_key)를 넣으세요 — cached_content_name 은 받지 않습니다: {bad}"
        )


_DELETE_GRACE_SECONDS = 5
"""delete_context_cache 의 고정 grace period (초). in-flight 요청 보호용. 사용자 지정 불가."""


async def delete_context_cache_async(
    cache_ids: str | list[str],
) -> ExpireResult:
    """Context Cache 를 **무조건 5초 뒤** 만료시킨다 (async).

    이름은 delete 지만 내부적으로는 Gemini `caches.update(ttl=5)` 로 TTL 을 단축한다 —
    `caches.delete` 는 원래 TTL 그대로 청구되는 반면 update 는 새 TTL 로 prorate 청구되기
    때문 (storage 비용 절감). 5초 동안 in-flight 요청을 보호한 뒤 Gemini cache + Redis 가
    만료된다. (수명을 **연장**하려면 `update_context_cache` 를 쓴다.)

    Args:
        cache_ids: `file_preprocessing`/`llm_request` 가 준 `context_cache_id`(hash_key).
            단건이면 문자열, 여러 개면 리스트. (Gemini `cached_content_name` 은 받지 않음.)

    Returns:
        ExpireResult — `expired`(성공/idempotent — 부재도 성공), `failed`(Gemini 5xx·Redis 오류 등).

    Raises:
        ValueError: cache_ids 가 비었거나 `cachedContents/...`(Gemini cache id)를 넣었을 때.

    Note:
        개별 cache 의 만료 실패는 raise 되지 않고 `result.failed` 에 담긴다.
    """
    if isinstance(cache_ids, str):
        ids = [cache_ids]
    else:
        ids = list(cache_ids) if cache_ids else []
    if not ids:
        raise ValueError(
            "삭제할 cache_id 가 없습니다. cache_id 를 1개 이상 넣어주세요."
        )
    if not all(ids):
        raise ValueError(
            "빈 cache_id 가 포함되어 있습니다. 유효한 cache_id 만 넣어주세요."
        )
    _reject_cached_content_name(ids)

    await asyncio.to_thread(ensure_configured)
    return await expire_caches(ids, _DELETE_GRACE_SECONDS)


def delete_context_cache(
    cache_ids: str | list[str],
) -> ExpireResult:
    """Context Cache 를 무조건 5초 뒤 만료시킨다 (sync). delete_context_cache_async 동기 래퍼."""
    return asyncio.run(
        delete_context_cache_async(cache_ids)
    )


async def update_context_cache_async(
    cache_ids: str | list[str],
    *,
    ttl: int,
) -> ExpireResult:
    """Context Cache 수명을 ttl 로 연장한다 (async).

    Gemini `caches.update(ttl=ttl)` + Redis TTL 연장. delete 와 달리 **연장 대상이
    없으면**(Redis 부재·Gemini 404) 해당 id 는 `failed`. (docs/26 §3.3)

    Args:
        cache_ids: `file_preprocessing`/`llm_request` 가 준 `context_cache_id`(hash_key).
            단건이면 문자열, 여러 개면 리스트. (Gemini `cached_content_name` 은 받지 않음.)
        ttl: 새 TTL (초, 1~3600).

    Returns:
        ExpireResult — `expired`(연장 성공), `failed`(부재·404·Gemini 5xx 등).

    Raises:
        ValueError: cache_ids 가 비었거나, ttl 이 범위(1~3600) 밖이거나,
            `cachedContents/...`(Gemini cache id)를 넣었을 때.

    Note:
        개별 cache 의 연장 실패는 raise 되지 않고 `result.failed` 에 담긴다.
    """
    if isinstance(cache_ids, str):
        ids = [cache_ids]
    else:
        ids = list(cache_ids) if cache_ids else []
    if not ids:
        raise ValueError(
            "연장할 cache_id 가 없습니다. cache_id 를 1개 이상 넣어주세요."
        )
    if not all(ids):
        raise ValueError(
            "빈 cache_id 가 포함되어 있습니다. 유효한 cache_id 만 넣어주세요."
        )
    if not 1 <= ttl <= 3600:
        raise ValueError(
            f"ttl 은 1~3600초 사이여야 합니다 (받은 값: {ttl})."
        )
    _reject_cached_content_name(ids)

    await asyncio.to_thread(ensure_configured)
    return await extend_caches(ids, ttl)


def update_context_cache(
    cache_ids: str | list[str],
    *,
    ttl: int,
) -> ExpireResult:
    """Context Cache 수명을 ttl 로 연장한다 (sync). update_context_cache_async 동기 래퍼."""
    return asyncio.run(
        update_context_cache_async(cache_ids, ttl=ttl)
    )
