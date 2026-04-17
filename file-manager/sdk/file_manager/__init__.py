# -*- coding: utf-8 -*-
"""
File Manager Handler - 파일 저장/캐시/반환 관리 모듈

사용법:
    from file_manager import FileManagerHandler

    fm = FileManagerHandler()

    # 캐시 체크
    result = await fm.check_cache({
        "file_name": "report.xlsx",
        "file_mtime": 1713000000,
        "file_size": 524288,
    })

    # 캐시 미스 시 업로드
    if not result["hit"]:
        metadata = await fm.upload_file(file_data, {
            "file_name": "report.xlsx",
            "file_mtime": 1713000000,
            "file_size": 524288,
        })

    # 파일 반환
    file_bytes = await fm.get_file("a1b2c3d4e5f6.xlsx")

    await fm.close()
"""

from .handler import FileManagerHandler
from .exceptions import FMError, FMConnectionError, FMServiceError

__all__ = [
    "FileManagerHandler",
    "FMError",
    "FMConnectionError",
    "FMServiceError",
]
