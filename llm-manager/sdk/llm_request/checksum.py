"""파일 checksum + 메타정보 추출 (내부 유틸리티).

file_paths로 전달받은 파일 경로에서 SHA-256 checksum과
filename, size, mime_type을 추출한다.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import TypedDict

import filetype


class FileChecksum(TypedDict):
    """파일 checksum + 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int  # bytes
    mime_type: str


def file_checksum(
    source: str | Path,
    *,
    mime_type: str | None = None,
    filename: str | None = None,
) -> FileChecksum:
    """파일 경로에서 SHA-256 checksum과 메타정보를 추출한다.

    Args:
        source: 로컬 파일 경로.
        mime_type: 명시적 MIME 타입. None이면 자동 감지.
        filename: 파일명 오버라이드. None이면 path.name으로 경로에서 추출.

    Returns:
        FileChecksum dict.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
    """
    path = Path(source)
    resolved_filename = filename or path.name

    # SHA-256 스트리밍 해시 (대용량 파일 대응)
    with open(path, "rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()

    size = path.stat().st_size

    if mime_type is None:
        ext_mime, _ = mimetypes.guess_type(str(path)) # 확장자 기반 MIME 타입 추측
        guess = filetype.guess(str(path)) # 파일 내용 기반 MIME 타입 추측 (확장자 무관)
        mime_type = ext_mime or (guess.mime if guess else None) or "application/octet-stream"

    return FileChecksum(
        checksum=f"sha256:{digest}",
        filename=resolved_filename,
        size=size,
        mime_type=mime_type,
    )
