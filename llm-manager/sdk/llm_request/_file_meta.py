"""file_manager SDK 배포 전 임시 stub (SDK 측).

Service 측 stub와 동일 필드. 네트워크 직렬화용.

TODO: file_manager SDK 배포되면 이 파일 삭제하고
`from file_manager import FileMeta` 로 교체.
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel


class FileMeta(BaseModel):
    """file_manager가 제공하는 파일 metadata 번들 (SDK 측 stub)."""

    file_name: str
    file_mtime: Union[str, int]
    file_size: int
    file_path: str
    mime_type: str
    extension: str
