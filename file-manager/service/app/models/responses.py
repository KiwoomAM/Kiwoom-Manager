# -*- coding: utf-8 -*-
"""
File Manager Service 응답 모델
"""

from pydantic import BaseModel


class FileMetadataResponse(BaseModel):
    """파일 메타데이터 응답 (캐시 히트 / 업로드 완료 시 반환)"""

    file_name: str
    file_mtime: int
    file_size: int
    file_path: str
    mime_type: str
    extension: str


class CacheCheckResponse(BaseModel):
    """캐시 체크 응답"""

    hit: bool
    metadata: FileMetadataResponse | None = None
