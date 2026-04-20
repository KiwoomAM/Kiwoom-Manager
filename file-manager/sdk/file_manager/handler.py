# -*- coding: utf-8 -*-
"""
FileManagerHandler — FM Service HTTP 클라이언트 + 로컬 메타데이터 헬퍼

HTTP 래퍼 3개(check_cache/upload_file/get_file) + 로컬 추출 헬퍼 1개(build_metadata).
파생 메타데이터(mime_type/extension) 추출은 SDK 측에서 결정적으로 수행되며,
FM Service는 SDK가 전달한 값을 그대로 저장한다.

사용 예시:
    from file_manager import FileManagerHandler

    fm = FileManagerHandler()

    # 1) 3개 필드를 확보한 뒤 build_metadata로 5개 필드 메타데이터 구성
    metadata = fm.build_metadata(
        file_name="report.xlsx",
        file_mtime=1713000000,
        file_size=524288,
    )
    # metadata = {file_name, file_mtime, file_size, mime_type, extension}

    # 2) 캐시 체크 (SDK가 캐시 키 3개만 서버에 전달)
    result = await fm.check_cache(metadata)

    if result["hit"]:
        metadata["file_path"] = result["file_path"]
    else:
        # 3) 미스 시 업로드. 반환값은 file_path 문자열만
        metadata["file_path"] = await fm.upload_file(file_data, metadata)

    # 4) 실제 파일이 필요할 때 — 바이너리 + 저장된 6개 필드 메타데이터 함께 반환
    file_bytes, stored_metadata = await fm.get_file(metadata["file_path"])

    await fm.close()
"""

import json
import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import unquote

from file_manager._client import AsyncClient
from file_manager.exceptions import FMServiceError

logger = logging.getLogger(__name__)

_API_PREFIX = "/v1/files"


class FileManagerHandler:
    """
    File Manager 통합 핸들러.

    FM Service를 통해 파일 캐시 체크, 업로드, 반환을 수행하고,
    로컬에서 파일명 기반 메타데이터(mime_type, extension)를 추출한다.
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

    def build_metadata(
        self, file_name: str, file_mtime: int, file_size: int
    ) -> dict:
        """파일명에서 mime_type/extension을 파생해 5개 필드 메타데이터를 구성.

        파일 바이너리를 읽지 않는 순수 로컬 계산 (HTTP 호출 없음).

        Args:
            file_name: 원본 파일명 (확장자 포함)
            file_mtime: 파일 수정 시간 (epoch seconds)
            file_size: 파일 크기 (bytes)

        Returns:
            {file_name, file_mtime, file_size, mime_type, extension}
        """
        extension = PurePosixPath(file_name).suffix.lstrip(".")
        mime_type, _ = mimetypes.guess_type(file_name)
        mime_type = mime_type or "application/octet-stream"
        return {
            "file_name": file_name,
            "file_mtime": file_mtime,
            "file_size": file_size,
            "mime_type": mime_type,
            "extension": extension,
        }

    async def check_cache(self, metadata: dict) -> dict:
        """캐시 체크. 입력 metadata에서 캐시 키 3개만 서버에 전달한다.

        Args:
            metadata: file_name/file_mtime/file_size를 포함하는 dict
                      (build_metadata 결과를 그대로 넣어도 됨)

        Returns:
            {"hit": True,  "file_path": "<UUID.확장자>"}  (캐시 히트)
            {"hit": False, "file_path": None}              (캐시 미스)
        """
        payload = {
            "file_name": metadata["file_name"],
            "file_mtime": metadata["file_mtime"],
            "file_size": metadata["file_size"],
        }
        return await self._client.post_json(
            f"{_API_PREFIX}/check-cache",
            json=payload,
        )

    async def upload_file(self, file_data: bytes, metadata: dict) -> str:
        """파일 업로드 + 메타데이터 저장.

        Args:
            file_data: 파일 바이너리 데이터
            metadata: {file_name, file_mtime, file_size, mime_type, extension}
                      build_metadata 결과를 그대로 전달하면 된다

        Returns:
            FM Service가 생성한 file_path (UUID.확장자) 문자열
        """
        files = {
            "file": (metadata["file_name"], file_data),
        }
        data = {
            "file_name": metadata["file_name"],
            "file_mtime": str(metadata["file_mtime"]),
            "file_size": str(metadata["file_size"]),
            "mime_type": metadata["mime_type"],
            "extension": metadata["extension"],
        }
        resp = await self._client.post_multipart(
            f"{_API_PREFIX}/upload",
            files=files,
            data=data,
        )
        return resp["file_path"]

    async def get_file(self, file_path: str) -> tuple[bytes, dict]:
        """File Server에서 파일을 꺼내 (바이너리, 메타데이터) 튜플로 반환.

        FM Service는 MongoDB에 저장된 6개 필드 메타데이터를
        `X-File-Metadata` 헤더(URL-encoded JSON)로 함께 전달한다.

        Args:
            file_path: FM Service가 반환한 file_path (예: "a1b2c3d4e5f6.xlsx")

        Returns:
            (file_data, metadata)
              - file_data: 파일 바이너리
              - metadata: {file_name, file_mtime, file_size, mime_type, extension, file_path} (6개)
        """
        file_data, headers = await self._client.get_bytes_with_headers(
            f"{_API_PREFIX}/{file_path}"
        )
        metadata_header = headers.get("x-file-metadata")
        if not metadata_header:
            raise FMServiceError(
                "X-File-Metadata 헤더 누락 — FM Service 버전이 오래됐을 수 있음",
                status_code=500,
            )
        metadata = json.loads(unquote(metadata_header))
        return file_data, metadata

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
