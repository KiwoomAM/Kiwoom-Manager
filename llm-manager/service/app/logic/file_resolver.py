"""Phase 3 — stored_files 를 placeholder FileInput 으로 변환 (lazy fetch).

Phase 2 에선 여기서 file-manager fetch + 404 fallback 진행했으나, Phase 3 에선
fetch 자체를 4·5단계 (resolver.py) 로 이전 — `try_acquire_standby` 메커니즘이
fetch 까지 자동으로 dedup. 70 동시 요청에서도 leader 1 명만 fetch.

설계: docs/21.method1-deprecation-lazy-fetch.md §5.4
"""

from __future__ import annotations

import logging

from file_manager import FileManagerHandler
from file_manager.exceptions import FMServiceError

from app.core.errors import FileResolveError
from app.logic.file_processing.validators import (
    validate_file_sizes,
    validate_mime_types,
)
from app.models.requests import FileInput, StoredFile

logger = logging.getLogger(__name__)


async def resolve_stored_files(refs: list[StoredFile]) -> list[FileInput]:
    """stored_files → placeholder FileInput list (data=b"") 변환.

    실제 fetch 는 안 함. validators 만 진입 즉시 적용 (fail-fast — file-manager 호출 전).
    실제 bytes 는 resolver.py 의 _process_inline / _upload_and_cache_file 에서 lazy 하게 fetch.
    """
    placeholders = [
        FileInput(
            file_name=ref.file_name,
            file_size=ref.file_size,
            file_mtime=ref.file_mtime,
            mime_type=ref.mime_type,
            extension=ref.extension,
            data=b"",                        # placeholder — lazy fetch 전제
            file_path=ref.file_path,
            resolved_via="primary",
        )
        for ref in refs
    ]

    # fail-fast: file-manager 호출 전 mime/size 검증
    validate_mime_types(placeholders)
    validate_file_sizes(placeholders)

    return placeholders


async def fetch_with_fallback(fm: FileManagerHandler, fi: FileInput) -> bytes:
    """fi.file_path 로 bytes fetch. 404 시 check_cache → 새 path 재시도 → fallback.

    호출자: resolver.py 의 _process_inline / _upload_and_cache_file. miss 시
    FileResolveError raise. fi.file_path / fi.resolved_via 를 fallback 시 갱신.

    Args:
        fm: FileManagerHandler 싱글톤
        fi: file_path / hashing 3 메타 채워진 FileInput. data 는 비어있음

    Returns:
        파일 bytes
    """
    try:
        data, _ = await fm.get_file(fi.file_path)
        return data
    except FMServiceError as e:
        if e.status_code != 404:
            raise  # 비-404 (network / 5xx) 그대로 전파

        cache = await fm.check_cache({
            "file_name": fi.file_name,
            "file_size": fi.file_size,
            "file_mtime": fi.file_mtime,
        })
        if not cache["hit"]:
            raise FileResolveError(
                f"file_path '{fi.file_path}' not found, "
                f"check_cache miss for file_name={fi.file_name}"
            ) from e

        new_path = cache["file_path"]
        logger.warning(
            "[Phase3] stale file_path: input=%s, resolved=%s, file_name=%s",
            fi.file_path, new_path, fi.file_name,
        )
        fi.file_path = new_path
        fi.resolved_via = "fallback"
        data, _ = await fm.get_file(new_path)
        return data
