"""Phase 2 — stored_files 를 file-manager 에서 fetch 하여 FileInput 으로 변환.

설계: docs/18.file-meta-response-exposure.md Phase 2 §5.3
"""

from __future__ import annotations

import logging

from file_manager import FileManagerHandler
from file_manager.exceptions import FMServiceError

from app.core.errors import FileResolveError
from app.models.requests import FileInput, StoredFile

logger = logging.getLogger(__name__)


async def resolve_stored_files(refs: list[StoredFile]) -> list[FileInput]:
    """각 StoredFile 을 file-manager 에서 fetch → bytes 로딩된 FileInput 반환.

    1. fm.get_file(ref.file_path) 1차 시도 → 성공 시 resolved_via="primary"
    2. 404 시 fm.check_cache(hash 3 필드) → 새 file_path → 재 fetch + resolved_via="fallback"
    3. 둘 다 miss → FileResolveError raise
    4. 비-404 에러 (5xx / network) → 그대로 전파
    """
    fm = FileManagerHandler()
    return [await _resolve_one(fm, ref) for ref in refs]


async def _resolve_one(fm: FileManagerHandler, ref: StoredFile) -> FileInput:
    try:
        data, _ = await fm.get_file(ref.file_path)  # bytes 만 사용 — caller 의 6 필드 그대로
        return FileInput(**ref.model_dump(), data=data, resolved_via="primary")
    except FMServiceError as e:
        if e.status_code != 404:
            raise  # 비-404 (network / 5xx) 는 그대로 전파

        cache = await fm.check_cache({
            "file_name": ref.file_name,
            "file_size": ref.file_size,
            "file_mtime": ref.file_mtime,
        })
        if not cache["hit"]:
            raise FileResolveError(
                f"file_path '{ref.file_path}' not found, "
                f"check_cache miss for file_name={ref.file_name}"
            ) from e

        new_path = cache["file_path"]
        logger.warning(
            "[Phase2] stale file_path: input=%s, resolved=%s, file_name=%s",
            ref.file_path, new_path, ref.file_name,
        )
        data, _ = await fm.get_file(new_path)
        # file_path 는 새 path 로 갱신 — 나머지 5 필드는 caller 값 유지
        return FileInput(
            **{**ref.model_dump(), "file_path": new_path},
            data=data,
            resolved_via="fallback",
        )
