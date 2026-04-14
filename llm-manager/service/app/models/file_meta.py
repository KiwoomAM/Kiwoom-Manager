"""file_manager SDK 배포 전 임시 stub (Service 측).

file_manager 담당자와 협의된 metadata 번들 정의.
file_mtime 타입은 TBD — 문자열/ns 정수 union으로 우선 허용.

TODO: file_manager SDK 배포되면 이 파일 삭제하고
`from file_manager import FileMeta` 로 교체.
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel


class FileMeta(BaseModel):
    """file_manager가 제공하는 파일 metadata 번들."""

    file_name: str
    file_mtime: Union[str, int]  # TBD: 담당자 협의 후 단일 타입으로 확정 예정
    file_size: int
    file_path: str       # file_server 절대경로
    mime_type: str
    extension: str
