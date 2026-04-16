"""file_manager 공개 타입."""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel


class FileMeta(BaseModel):
    """file_manager가 제공하는 파일 metadata 번들.

    file_name, file_mtime, file_size: 에이전트에서 전달
    file_path, mime_type, extension: FM Service가 생성
    """

    file_name: str
    file_mtime: Union[str, int]
    file_size: int
    file_path: str
    mime_type: str
    extension: str


class CacheCheckResult(BaseModel):
    """check_cache 반환 타입."""

    hit: bool
    metadata: FileMeta | None = None
