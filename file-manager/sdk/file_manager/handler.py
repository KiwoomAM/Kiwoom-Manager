# -*- coding: utf-8 -*-
"""
FileManagerHandler — FM Service HTTP 클라이언트 래퍼

비즈니스 로직 없음. 인자를 받아서 FM Service에 전달하고 응답을 반환한다.

사용 예시:
    from file_manager import FileManagerHandler

    fm = FileManagerHandler()

    # 캐시 체크
    result = await fm.check_cache({
        "file_name": "report.xlsx",
        "file_mtime": 1713000000,
        "file_size": 524288,
    })

    if result["hit"]:
        metadata = result["metadata"]
    else:
        # 파일 업로드
        metadata = await fm.upload_file(file_data, {
            "file_name": "report.xlsx",
            "file_mtime": 1713000000,
            "file_size": 524288,
        })

    # 파일 반환
    file_bytes = await fm.get_file(metadata["file_path"])

    await fm.close()
"""

import logging
from typing import Optional

from file_manager._client import AsyncClient

logger = logging.getLogger(__name__)

_API_PREFIX = "/v1/files"


class FileManagerHandler:
    """
    File Manager 통합 핸들러.

    FM Service를 통해 파일 캐시 체크, 업로드, 반환을 수행한다.
    SDK는 판단하지 않고 전달만 한다.
    """

    _instance: Optional["FileManagerHandler"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._client = AsyncClient()
        self._initialized = True
        logger.info("FileManagerHandler 초기화 완료")

    async def check_cache(self, metadata: dict) -> dict:
        """캐시 체크: 메타데이터 3개로 기존 파일 존재 여부 확인.

        Args:
            metadata: {"file_name": str, "file_mtime": int, "file_size": int}

        Returns:
            {"hit": True, "metadata": {...}} 또는 {"hit": False, "metadata": None}
        """
        return await self._client.post_json(
            f"{_API_PREFIX}/check-cache",
            json=metadata,
        )

    async def upload_file(self, file_data: bytes, metadata: dict) -> dict:
        """파일 업로드: 파일과 메타데이터를 FM Service에 전송.

        Args:
            file_data: 파일 바이너리 데이터
            metadata: {"file_name": str, "file_mtime": int, "file_size": int}

        Returns:
            저장된 파일의 전체 메타데이터
            {"file_name", "file_mtime", "file_size", "file_path", "mime_type", "extension"}
        """
        files = {
            "file": (metadata["file_name"], file_data),
        }
        data = {
            "file_name": metadata["file_name"],
            "file_mtime": str(metadata["file_mtime"]),
            "file_size": str(metadata["file_size"]),
        }
        return await self._client.post_multipart(
            f"{_API_PREFIX}/upload",
            files=files,
            data=data,
        )

    async def get_file(self, file_path: str) -> bytes:
        """파일 반환: File Server에서 파일을 꺼내 바이너리로 반환.

        Args:
            file_path: FM Service가 반환한 file_path (예: "a1b2c3d4e5f6.xlsx")

        Returns:
            파일 바이너리 데이터
        """
        return await self._client.get_bytes(f"{_API_PREFIX}/{file_path}")

    async def health_check(self) -> dict:
        """FM Service 상태 확인."""
        resp = await self._client._request("GET", "/health")
        return resp.json()

    async def close(self) -> None:
        """HTTP 클라이언트 연결 종료."""
        if self._client is not None:
            await self._client.close()
            logger.info("FileManagerHandler 연결 종료")

    @classmethod
    def reset(cls) -> None:
        """Singleton 인스턴스 리셋."""
        if cls._instance is not None:
            cls._instance = None
            logger.info("FileManagerHandler Singleton 리셋 완료")

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return f"<FileManagerHandler status={status}>"
