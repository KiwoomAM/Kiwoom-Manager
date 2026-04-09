"""logic 내부에서 사용하는 공유 타입."""

from __future__ import annotations

from typing import TypedDict


class FileChecksum(TypedDict):
    """파일 checksum + 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int  # bytes
    mime_type: str
