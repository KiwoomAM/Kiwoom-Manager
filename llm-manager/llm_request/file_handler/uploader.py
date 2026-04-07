"""Files API 업로드 + Context Cache 생성.

llm-handler SDK를 통해 Gemini Files API 업로드와 create_cache를 수행한다.
"""

from __future__ import annotations

import asyncio
import logging
import os

from llm_handler.aio import create_cache as _aio_create_cache
from llm_handler.aio import upload_file as _aio_upload
from llm_handler.models import CacheRef, FileRef

from .splitter import split_pdf

logger = logging.getLogger(__name__)

# create_cache 재시도 설정
_MAX_RETRIES = 2
_RETRY_DELAY = 5  # 초
_RETRYABLE_KEYWORDS = ("500", "503", "429", "RESOURCE_EXHAUSTED")


async def upload_file(path: str, mime_type: str) -> FileRef:
    """Files API에 파일 업로드.

    Args:
        path: 로컬 파일 경로.
        mime_type: MIME 타입.

    Returns:
        FileRef(name, uri, mime_type, state).
    """
    return await _aio_upload(path, mime_type=mime_type)


async def upload_pdf_parts(path: str, target_mb: int = 50) -> list[FileRef]:
    """PDF를 split 후 각 파트를 Files API에 업로드.

    split된 임시 파일은 업로드 완료/실패 후 삭제 보장.

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB).

    Returns:
        업로드된 FileRef 리스트.
    """
    parts = split_pdf(path, target_mb)

    # 원본 그대로 반환된 경우 (분할 불필요)
    if len(parts) == 1 and parts[0] == path:
        ref = await upload_file(path, "application/pdf")
        return [ref]

    # 분할된 임시 파일들 업로드
    refs: list[FileRef] = []
    try:
        for i, part_path in enumerate(parts):
            logger.info("PDF 파트 업로드 %d/%d: %s", i + 1, len(parts), part_path)
            ref = await upload_file(part_path, "application/pdf")
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
) -> CacheRef:
    """Context Cache 생성 (재시도 포함).

    Args:
        file_refs: 업로드된 FileRef 리스트.
        model: Gemini 모델명.
        system_instruction: 시스템 프롬프트.
        ttl: 캐시 TTL (초).

    Returns:
        CacheRef(name, model, ...).

    Raises:
        마지막 재시도 실패 시 원본 예외를 그대로 raise.
    """
    contents = [
        {
            "role": "user",
            "content": [
                {"type": "file", "file_uri": ref.uri, "mime_type": ref.mime_type}
                for ref in file_refs
            ],
        }
    ]

    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await _aio_create_cache(
                model=model,
                contents=contents,
                system_instruction=system_instruction,
                ttl=ttl,
            )
        except Exception as e:
            last_error = e
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

    raise last_error  # type: ignore[misc]
