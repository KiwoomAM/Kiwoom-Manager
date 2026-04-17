"""file_manager MDB 저장소 — 메타데이터 캐시 조회/저장.

캐시 키: file_name + file_size + file_mtime 조합.
MDB Handler SDK를 통해 MongoDB에 접근한다.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from pathlib import Path

from mdb_handler import MongoDBHandler

from app.config import settings

logger = logging.getLogger(__name__)


def _get_collection():
    mdb = MongoDBHandler()
    return mdb.get_database(settings.mongodb_database, settings.mongodb_collection)


def check_cache(file_name: str, file_size: int, file_mtime: str | int) -> dict | None:
    """file_name + file_size + file_mtime으로 MDB 조회.

    Returns:
        히트 시 metadata dict, 미스 시 None.
    """
    col = _get_collection()
    doc = col.find_one({
        "file_name": file_name,
        "file_size": file_size,
        "file_mtime": file_mtime,
    })
    if doc:
        logger.info("cache hit: %s (size=%d, mtime=%s)", file_name, file_size, file_mtime)
    else:
        logger.info("cache miss: %s (size=%d, mtime=%s)", file_name, file_size, file_mtime)
    return doc


def save_metadata(metadata: dict) -> None:
    """메타데이터를 MDB에 저장 (upsert by _id=file_path)."""
    col = _get_collection()
    doc = {**metadata, "_id": metadata["file_path"]}
    col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    logger.info("metadata saved: %s → %s", metadata["file_name"], metadata["file_path"])


def save_file(file_data: bytes, file_name: str) -> str:
    """파일을 UUID.확장자로 저장하고 저장 경로를 반환.

    Returns:
        저장된 파일 경로 (UUID.ext).
    """
    storage = Path(settings.storage_dir)
    storage.mkdir(parents=True, exist_ok=True)

    ext = Path(file_name).suffix  # ".xlsx", ".pdf" 등
    saved_name = f"{uuid.uuid4()}{ext}"
    saved_path = storage / saved_name

    saved_path.write_bytes(file_data)
    logger.info("file saved: %s → %s (%d bytes)", file_name, saved_name, len(file_data))
    return saved_name


def get_file_bytes(file_path: str) -> bytes:
    """저장된 파일을 읽어서 bytes로 반환.

    Args:
        file_path: UUID.ext 형태의 파일명.

    Returns:
        파일 bytes.

    Raises:
        FileNotFoundError: 파일이 없을 때.
    """
    full_path = Path(settings.storage_dir) / file_path
    if not full_path.is_file():
        raise FileNotFoundError(f"file not found: {file_path}")
    return full_path.read_bytes()


def build_metadata(
    file_name: str,
    file_mtime: str | int,
    file_size: int,
    file_path: str,
    mime_type: str | None = None,
    extension: str | None = None,
) -> dict:
    """FM Service가 생성하는 완전한 metadata dict 구성.

    file_name, file_mtime, file_size: 에이전트에서 전달
    file_path: save_file이 반환한 UUID.ext
    mime_type, extension: 에이전트가 제공하면 그 값을 사용, 없으면 file_name에서 추론.
    """
    if extension is None:
        extension = Path(file_name).suffix.lstrip(".")
    if mime_type is None:
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    return {
        "file_name": file_name,
        "file_mtime": file_mtime,
        "file_size": file_size,
        "file_path": file_path,
        "mime_type": mime_type,
        "extension": extension,
    }
