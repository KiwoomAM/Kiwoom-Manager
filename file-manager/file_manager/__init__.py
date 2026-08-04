# -*- coding: utf-8 -*-
"""
File Manager Handler - 파일 저장/캐시/반환 관리 모듈

사용법:
    from file_manager import FileManagerHandler

    fm = FileManagerHandler()

    # 1) 3개 필드에서 5개 필드 메타데이터 구성 (mime_type/extension 로컬 추출)
    metadata = fm.build_metadata(
        file_name="report.xlsx",
        file_mtime=1713000000,
        file_size=524288,
    )

    # 2) 캐시 체크
    result = await fm.check_cache(metadata)

    # 3) 히트면 file_path 병합, 미스면 업로드 후 병합
    if result["hit"]:
        metadata["file_path"] = result["file_path"]
    else:
        metadata["file_path"] = await fm.upload_file(file_data, metadata)

    # 4) 실제 파일이 필요할 때 — 바이너리 + 6개 필드 메타데이터 반환
    file_bytes, stored_metadata = await fm.get_file(metadata["file_path"])

    await fm.close()
"""

from .handler import FileManagerHandler
from .exceptions import FMError, FMConfigError, FMConnectionError, FMServiceError

__all__ = [
    "FileManagerHandler",
    "FMError",
    "FMConfigError",
    "FMConnectionError",
    "FMServiceError",
]
