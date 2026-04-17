# -*- coding: utf-8 -*-
"""
File Server 저장소 인터페이스

현재는 로컬 파일시스템으로 임시 구현.
추후 S3, NFS 등으로 교체 시 이 모듈만 수정하면 된다.
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.core.errors import FileNotFoundOnServerError, FileStorageError

logger = logging.getLogger(__name__)


def _get_storage_root() -> Path:
    root = Path(settings.file_storage_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_safe_path(file_path: str) -> Path:
    """저장소 루트 내부의 경로인지 검증 후 반환. 경로 조작 방지."""
    root = _get_storage_root().resolve()
    full_path = (root / file_path).resolve()
    if not str(full_path).startswith(str(root)):
        raise FileStorageError(f"잘못된 파일 경로: {file_path}")
    return full_path


def _save_file_sync(file_path: str, file_data: bytes) -> None:
    full_path = _resolve_safe_path(file_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(file_data)


def _read_file_sync(file_path: str) -> bytes:
    full_path = _resolve_safe_path(file_path)
    if not full_path.exists():
        raise FileNotFoundOnServerError(f"파일을 찾을 수 없습니다: {file_path}")
    return full_path.read_bytes()


async def save_file(file_path: str, file_data: bytes) -> None:
    """파일을 File Server에 저장한다.

    Args:
        file_path: 저장 경로 (예: "a1b2c3d4e5f6.xlsx")
        file_data: 파일 바이너리 데이터
    """
    try:
        await asyncio.to_thread(_save_file_sync, file_path, file_data)
        logger.info("파일 저장 완료: %s (%d bytes)", file_path, len(file_data))
    except FileStorageError:
        raise
    except OSError as e:
        logger.error("파일 저장 실패: %s - %s", file_path, e)
        raise FileStorageError(f"파일 저장 실패: {file_path}") from e


async def read_file(file_path: str) -> bytes:
    """File Server에서 파일을 읽어 반환한다.

    Args:
        file_path: 저장 경로 (예: "a1b2c3d4e5f6.xlsx")

    Returns:
        파일 바이너리 데이터
    """
    try:
        data = await asyncio.to_thread(_read_file_sync, file_path)
        logger.info("파일 반환: %s (%d bytes)", file_path, len(data))
        return data
    except (FileNotFoundOnServerError, FileStorageError):
        raise
    except OSError as e:
        logger.error("파일 읽기 실패: %s - %s", file_path, e)
        raise FileStorageError(f"파일 읽기 실패: {file_path}") from e
