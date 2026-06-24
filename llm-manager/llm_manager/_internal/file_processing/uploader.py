"""Files API 업로드 + Context Cache 생성.

llm-handler SDK를 통해 Gemini Files API 업로드와 create_cache를 수행한다.
"""

from __future__ import annotations

import asyncio
import logging
import os

from llm_manager._internal.handler_client.aio import create_cache as _aio_create_cache
from llm_manager._internal.handler_client.aio import upload_file
from llm_manager._internal.handler_client.models import CacheRef, FileRef

from .splitter import split_pdf

logger = logging.getLogger(__name__)

# create_cache 재시도 설정
_MAX_RETRIES = 2
_RETRY_DELAY = 5  # 초
_RETRYABLE_KEYWORDS = ("500", "503", "429", "RESOURCE_EXHAUSTED")


async def upload_pdf_parts(
    path: str, target_mb: int = 50, max_pages: int = 1000
) -> list[FileRef]:
    """PDF를 split 후 각 파트를 Files API에 업로드.

    split된 임시 파일은 업로드 완료/실패 후 삭제 보장.

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB).
        max_pages: 파트당 최대 페이지 수 (Gemini 한도).

    Returns:
        업로드된 FileRef 리스트.
    """
    parts = split_pdf(path, target_mb, max_pages)

    # 원본 그대로 반환된 경우 (분할 불필요)
    if len(parts) == 1 and parts[0] == path:
        ref = await upload_file(path, mime_type="application/pdf")
        return [ref]

    # 분할된 임시 파일들 업로드
    refs: list[FileRef] = []
    try:
        for i, part_path in enumerate(parts):
            logger.info("PDF 파트 업로드 %d/%d: %s", i + 1, len(parts), part_path)
            ref = await upload_file(part_path, mime_type="application/pdf")
            refs.append(ref)
    finally:
        # 임시 파일 삭제 (원본은 건드리지 않음)
        for part_path in parts:
            if part_path != path:
                try:
                    os.unlink(part_path)
                except OSError:
                    pass

    return refs


async def create_context_cache(
    file_refs: list[FileRef],
    *,
    model: str,
    system_instruction: str | None = None,
    ttl: int = 3600,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> CacheRef:
    """Context Cache 생성 (재시도 포함).

    Gemini Context Cache 는 cache 생성 시 `tools` / `tool_config` 도 함께 저장해야
    이후 `call(cached_content=...)` 에서 Bad Request 없이 재사용 가능하다. llm_handler
    의 `create_cache` 가 이들 파라미터를 지원하므로 llm-manager wrapper 에서도 그대로 전달.

    Args:
        file_refs: 업로드된 FileRef 리스트.
        model: Gemini 모델명.
        system_instruction: 시스템 프롬프트.
        ttl: 캐시 TTL (초).
        tools: Function calling / Built-in 도구 정의 (cache 에 동봉).
        tool_config: function calling 모드 ("auto" | "any" | "none").

    Returns:
        CacheRef(name, model, ...).

    Raises:
        마지막 재시도 실패 시 원본 예외를 그대로 raise.
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await _aio_create_cache(
                file_refs,
                model=model,
                system=system_instruction,
                ttl=ttl,
                tools=tools,
                tool_config=tool_config,
            )
        except Exception as e:
            err_str = str(e)
            if attempt < _MAX_RETRIES and any(
                kw in err_str for kw in _RETRYABLE_KEYWORDS
            ):
                logger.warning(
                    "create_cache 실패 (시도 %d/%d): %s → %d초 후 재시도",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    err_str,
                    _RETRY_DELAY,
                )
                await asyncio.sleep(_RETRY_DELAY)
            else:
                raise
