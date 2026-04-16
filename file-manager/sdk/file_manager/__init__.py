"""file_manager SDK.

Public API:
    - FileMeta: 파일 metadata 번들
    - CacheCheckResult: check_cache 반환 타입
    - check_cache / acheck_cache: 캐시 조회
    - upload_file / aupload_file: 파일 업로드
    - get_file / aget_file: 파일 bytes 조회
"""

from file_manager.client import (
    acheck_cache,
    aget_file,
    aupload_file,
    check_cache,
    get_file,
    upload_file,
)
from file_manager.models import CacheCheckResult, FileMeta

__all__ = [
    "FileMeta",
    "CacheCheckResult",
    "check_cache",
    "acheck_cache",
    "upload_file",
    "aupload_file",
    "get_file",
    "aget_file",
]
