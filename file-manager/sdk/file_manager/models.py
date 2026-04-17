"""file_manager 공개 타입."""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel


class FileMeta(BaseModel):
    """file_manager가 저장하는 파일 metadata 번들 (서비스 내부 참조용).

    file_name, file_mtime, file_size, mime_type, extension: 에이전트 또는 호출자 입력
    file_path: FM Service가 UUID로 생성하는 파일 식별자
    """

    file_name: str
    file_mtime: Union[str, int]
    file_size: int
    file_path: str
    mime_type: str
    extension: str
