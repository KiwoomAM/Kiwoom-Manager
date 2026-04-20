# -*- coding: utf-8 -*-
"""
File Manager Service 응답 모델
"""

from pydantic import BaseModel


class CacheCheckResponse(BaseModel):
    """캐시 체크 응답 — 히트 시 file_path만 반환 (나머지 5개는 에이전트가 로컬에 보유)"""

    hit: bool
    file_path: str | None = None


class UploadResponse(BaseModel):
    """업로드 응답 — FM Service가 생성한 file_path만 반환"""

    file_path: str
