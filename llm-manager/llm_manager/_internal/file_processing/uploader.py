"""Files API 업로드 + Context Cache 생성.

llm-handler SDK를 통해 Gemini Files API 업로드와 create_cache를 수행한다.
"""

from __future__ import annotations

import asyncio
import logging
import os

import fitz

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
) -> tuple[list[FileRef], list[tuple[int, int]] | None]:
    """PDF를 split 후 각 파트를 Files API에 업로드.

    split된 임시 파일은 업로드 완료/실패 후 삭제 보장.

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB).
        max_pages: 파트당 최대 페이지 수 (Gemini 한도).

    Returns:
        ``(refs, pdf_part_ranges)`` 튜플.
        ``refs``: 업로드된 FileRef 리스트.
        ``pdf_part_ranges``: 분할된 경우 파트별 원본 페이지 범위(1-based, 끝 포함,
        업로드 순서). 병합·캡을 거친 **최종 파트 파일**에서 페이지수를 직접 세어 누적하므로
        경계가 항상 실제와 일치한다. 분할 안 된 경우(단일 파트)는 ``None``.
    """
    parts = split_pdf(path, target_mb, max_pages)

    # 원본 그대로 반환된 경우 (분할 불필요)
    if len(parts) == 1 and parts[0] == path:
        ref = await upload_file(path, mime_type="application/pdf")
        return [ref], None

    # 분할된 임시 파일들 업로드 (+ 파트별 원본 페이지 범위 누적)
    refs: list[FileRef] = []
    pdf_part_ranges: list[tuple[int, int]] = []
    start = 1
    try:
        for i, part_path in enumerate(parts):
            doc = fitz.open(part_path)
            n_pages = doc.page_count
            doc.close()
            pdf_part_ranges.append((start, start + n_pages - 1))
            start += n_pages
            logger.info(
                "PDF 파트 업로드 %d/%d: %s (원본 p.%d-%d)",
                i + 1, len(parts), part_path,
                pdf_part_ranges[-1][0], pdf_part_ranges[-1][1],
            )
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

    return refs, pdf_part_ranges


async def create_context_cache(
    contents: list[str | FileRef],
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
        contents: 캐싱할 콘텐츠 — 파일별 헤더 라벨(str) + FileRef 혼합 (doc 29).
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
                contents,
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
