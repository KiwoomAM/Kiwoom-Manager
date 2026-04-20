# -*- coding: utf-8 -*-
"""
File Manager Service 요청 모델
"""

from pydantic import BaseModel


class CheckCacheRequest(BaseModel):
    """캐시 체크 요청 (메타데이터 3개)"""

    file_name: str
    file_mtime: int
    file_size: int
