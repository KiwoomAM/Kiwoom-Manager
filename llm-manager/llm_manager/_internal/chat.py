"""챗봇용 대화 함수 core — docs/38.

`messages` 안의 `StoredFile` 을 파일마다 Files API 주소 조각으로 풀어 원래 위치에 넣고
밑단 `stream`·`call` 에 넘긴다. 응답 캐시·저장·Context Cache·meta 는 없다(docs/38 §2.4·§2.5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from llm_manager._internal.file_processing import cache_store
from llm_manager._internal.file_processing.resolver import _resolve_one_files_api
from llm_manager._internal.gemini_errors import is_gemini_file_unavailable
from llm_manager._internal.handler_client.aio import call as aio_call
from llm_manager._internal.handler_client.aio import stream as aio_stream
from llm_manager._internal.handler_client.models import Content, LLMResponse, Part, StreamChunk
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.meta import require_service_name
from llm_manager._internal.stored_file_resolver import resolve_stored_files
from llm_manager.exceptions import (
    FileResolveError,
    FileTooLargeError,
    PdfPageTooLargeError,
    UnsupportedFileTypeError,
)
from llm_manager.models import ChatMessage, StoredFile

logger = logging.getLogger(__name__)

#: 첨부 하나를 Files API 주소로 푸는 데 허용하는 시간(초). 매니저 안쪽 상한(HTTP 600초)은
#: 사실상 없는 것과 같아 여기서 세운다. **파일 단위**라 늦은 첨부만 안내 문구로 빠진다.
#: 해소 전체에는 상한을 두지 않는다 — 소비자가 호출을 감싼 상한이 해소 시간까지 잡는다(§3.3).
ATTACHMENT_RESOLVE_TIMEOUT = 60.0

#: files_api 업로드 dedup standby TTL(초). 공개 `Timings` 의 standby_ttl 기본값과 같다.
_STANDBY_TTL_SECONDS = 600

#: 파일 하나에만 매인 실패 — 이것만 안내 문구로 바꾼다. Redis·file-manager·Gemini 업로드
#: 장애처럼 대화 전체에 걸린 실패는 올린다. 덮으면 장애 동안 모든 첨부가 조용히 빠진 채
#: 모델이 문서 없이 답하고, 호출하는 쪽은 그 사실을 알 방법이 없다.
_FILE_SCOPED_ERRORS = (
    FileResolveError,           # file-manager 에 그 파일이 없다
    UnsupportedFileTypeError,   # 받지 않는 형식이다
    FileTooLargeError,          # 너무 크다
    PdfPageTooLargeError,       # 쪼갤 수 없는 PDF 다
)


async def _resolve_one(sf: StoredFile) -> tuple[list[Part], str]:
    """StoredFile 하나 → (Files API 주소 조각들, 체크섬). 분할된 PDF 면 조각이 여러 개다."""
    [fi] = await resolve_stored_files([sf])
    checksum = make_file_checksum(fi)
    parts, _refs = await _resolve_one_files_api(
        fi, checksum, standby_ttl_seconds=_STANDBY_TTL_SECONDS,
    )
    return parts, checksum


async def _parts_or_notice(sf: StoredFile) -> tuple[list[Part], str | None]:
    """(주소 조각, 체크섬)을 돌려주고, 파일 하나에 매인 실패면 (안내 문구, None)을 돌려준다.

    실패를 파일 단위로 격리하는 이유는 오래전 첨부 하나 때문에 지금 대화가 멈추지 않게 하는
    것이다. 그 실패는 로그에만 남긴다. 인프라 장애는 격리 대상이 아니라 그대로 올라간다
    (`_FILE_SCOPED_ERRORS`).
    """
    try:
        async with asyncio.timeout(ATTACHMENT_RESOLVE_TIMEOUT):
            return await _resolve_one(sf)
    except TimeoutError:
        logger.warning(
            "첨부 해소가 상한(%.0f초)을 넘었다 — 안내 문구로 바꾼다: %s",
            ATTACHMENT_RESOLVE_TIMEOUT, sf.file_path,
        )
    except _FILE_SCOPED_ERRORS:
        logger.exception("첨부를 Files API 주소로 풀지 못했다 — 안내 문구로 바꾼다: %s", sf.file_path)
    return [Part(type="text", text=f"[{sf.file_name}: 첨부를 불러오지 못했습니다]")], None


async def _resolve_messages(messages: list[ChatMessage]) -> tuple[list[Content], list[str]]:
    """발화마다 StoredFile 을 제자리에서 주소 조각으로 바꿔 밑단 `Content` 로 만든다.

    반환의 둘째는 주소로 푼 파일의 체크섬이다 — 첨부를 못 쓴다는 응답(404·403) 때 지울 Redis
    기록이다. 안내 문구로 바뀐 파일은 주소를 보내지 않았으니 그 응답의 원인이 아니라 넣지 않는다.

    파일은 순차로 푼다. 캐시 MISS 는 파일 전체를 파일매니저에서 받아 다시 올리므로, 동시에
    풀면 대역폭과 메모리가 한꺼번에 물린다.
    """
    contents: list[Content] = []
    checksums: list[str] = []
    for message in messages:
        parts: list[Part] = []
        for item in message.content:
            if isinstance(item, StoredFile):
                file_parts, checksum = await _parts_or_notice(item)
                parts.extend(file_parts)
                if checksum is not None:
                    checksums.append(checksum)
            else:
                parts.append(item)
        contents.append(Content(role=message.role, content=parts))
    return contents, checksums


async def _resolve_after_file_unavailable(
    messages: list[ChatMessage], checksums: list[str], error: Exception,
) -> list[Content]:
    """보낸 파일들의 Redis 주소 기록을 지우고 다시 푼다 — 재사용 대신 새로 올리게 된다.

    기존 `resolve_files_after_404` 는 크기 분류와 Context Cache 흐름을 다시 거치고 결과가
    파일별이 아니라 쓸 수 없다(docs/38 §2.2). 지우는 헬퍼만 같이 쓴다.
    """
    logger.warning("Gemini 가 첨부를 못 씀 — Redis 주소 기록 %d건을 지우고 다시 푼다: %s", len(checksums), error)
    for checksum in checksums:
        try:
            cache_store.delete_files_api(checksum)
        except Exception:
            # 지우지 못하면 다시 풀 때 같은 주소를 재사용해 재시도가 같은 오류로 또 진다 — 그
            # 예외가 올라가므로 여기서는 로그만 남기고 나머지 기록 삭제를 계속한다.
            logger.exception("Redis files_api 삭제 실패 (계속 진행): checksum=%s", checksum[:16])
    contents, _ = await _resolve_messages(messages)
    return contents


async def stream_chat(
    messages: list[ChatMessage], call_kwargs: dict[str, Any],
) -> AsyncGenerator[StreamChunk, None]:
    """첨부를 푼 뒤 밑단 스트림의 청크를 그대로 내보낸다. 첫 청크 전이면 첨부를 못 쓴다는
    응답(404·403)을 1회 복구한다."""
    service_name = require_service_name()
    contents, checksums = await _resolve_messages(messages)
    started = False
    try:
        async for chunk in aio_stream(messages=contents, service_name=service_name, **call_kwargs):
            started = True
            yield chunk
    except Exception as e:
        # 이미 내보낸 청크는 되돌릴 수 없어, 다시 부르면 답이 두 번 나간다.
        if started or not checksums or not is_gemini_file_unavailable(e):
            raise
        contents = await _resolve_after_file_unavailable(messages, checksums, e)
        async for chunk in aio_stream(messages=contents, service_name=service_name, **call_kwargs):
            yield chunk


async def call_chat(messages: list[ChatMessage], call_kwargs: dict[str, Any]) -> LLMResponse:
    """첨부를 푼 뒤 밑단 단건 호출의 응답을 돌려준다. 첨부를 못 쓴다는 응답(404·403)은 1회 복구한다."""
    service_name = require_service_name()
    contents, checksums = await _resolve_messages(messages)
    try:
        return await aio_call(messages=contents, service_name=service_name, **call_kwargs)
    except Exception as e:
        if not checksums or not is_gemini_file_unavailable(e):
            raise
        contents = await _resolve_after_file_unavailable(messages, checksums, e)
        return await aio_call(messages=contents, service_name=service_name, **call_kwargs)
