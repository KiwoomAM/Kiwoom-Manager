# -*- coding: utf-8 -*-
"""
FileManagerHandler — 파일 캐시 오케스트레이션 (file-handler + mdb-handler)

파일 바이트는 file-handler(내장 HTTP 클라이언트)에 저장하고,
메타데이터(MongoDB)는 mdb-manager SDK(→ mdb-handler)로 접근한다.
  - check_cache    : mdb-handler에 메타데이터 직접 질의
  - upload_file    : 파일 바이트는 file-handler에 저장 + 6필드 메타데이터는 mdb-handler에 기록
  - get_file       : 메타데이터는 mdb-handler 조회 + 파일 바이트는 file-handler 수신
  - build_metadata : 로컬에서 mime_type/extension 파생 (HTTP 호출 없음)

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

import asyncio
import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Optional

from file_manager._client import AsyncClient
from file_manager._mdb import close_mdb, find_metadata, insert_metadata
from file_manager.exceptions import MetadataNotFoundError

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
        # dedup 키별 asyncio.Lock — register_or_get 임계구역 직렬화용.
        # 같은 (file_name,file_mtime,file_size) 동시 호출만 줄 세운다(다른 키는 병렬).
        # 키 개수만큼 증가하지만 Lock 객체가 작아 무시 가능(YAGNI: 정리 로직 안 둠).
        self._locks: dict[tuple, asyncio.Lock] = {}
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
        """캐시 체크. 캐시 키 3개로 mdb-handler에 직접 질의한다.

        Args:
            metadata: file_name/file_mtime/file_size를 포함하는 dict

        Returns:
            {"hit": True,  "file_path": "<UUID.확장자>"}  (캐시 히트)
            {"hit": False, "file_path": None}              (캐시 미스)
        """
        filter = {
            "metadata.file_name": metadata["file_name"],
            "metadata.file_mtime": metadata["file_mtime"],
            "metadata.file_size": metadata["file_size"],
        }
        doc = await find_metadata(filter)
        if doc is None:
            return {"hit": False, "file_path": None}
        return {"hit": True, "file_path": doc["metadata"]["file_path"]}

    async def upload_file(self, file_data: bytes, metadata: dict) -> str:
        """파일 바이트는 file-handler에 저장하고, 메타데이터는 mdb-handler에 기록.

        Args:
            file_data: 파일 바이너리 데이터
            metadata: {file_name, file_mtime, file_size, mime_type, extension}

        Returns:
            file-handler가 생성한 file_path (UUID.확장자) 문자열
        """
        files = {"file": (metadata["file_name"], file_data)}
        data = {"extension": metadata["extension"]}
        resp = await self._client.post(
            f"{_API_PREFIX}/upload",
            files=files,
            data=data,
        )
        file_path = resp["file_path"]

        document = {
            "metadata": {
                "file_name": metadata["file_name"],
                "file_mtime": metadata["file_mtime"],
                "file_size": metadata["file_size"],
                "file_path": file_path,
                "mime_type": metadata["mime_type"],
                "extension": metadata["extension"],
            }
        }
        await insert_metadata(document)
        return file_path

    async def get_file(self, file_path: str) -> tuple[bytes, dict]:
        """(바이너리, 6필드 메타데이터) 튜플 반환.

        메타데이터는 mdb-handler에서 조회하고, 파일 바이트는 file-handler에서 받는다.

        Args:
            file_path: file-handler가 반환했던 file_path (예: "a1b2c3.xlsx")

        Returns:
            (file_data, metadata)
              metadata: {file_name, file_mtime, file_size, mime_type, extension, file_path}
        """
        doc = await find_metadata({"metadata.file_path": file_path})
        if doc is None:
            raise MetadataNotFoundError(file_path)
        metadata = doc["metadata"]

        file_data, _headers = await self._client.get_bytes_with_headers(
            f"{_API_PREFIX}/{file_path}"
        )
        return file_data, metadata

    def _dedup_lock(self, metadata: dict) -> asyncio.Lock:
        """register_or_get 임계구역용 per-key 락.

        같은 (file_name, file_mtime, file_size)만 직렬화한다. setdefault는 이벤트 루프
        단일 스레드에서 원자적이라 락 자체의 경쟁이 없다.
        """
        key = (
            metadata["file_name"],
            metadata["file_mtime"],
            metadata["file_size"],
        )
        return self._locks.setdefault(key, asyncio.Lock())

    async def register_or_get(
        self,
        bytes_data: bytes,
        metadata: dict,
    ) -> dict:
        """check_cache → miss 면 upload_file → 6 필드 dict 반환.

        같은 (file_name, file_mtime, file_size) 동시 호출은 per-key 락으로 직렬화되어
        프로세스 내에서 중복 업로드/저장이 발생하지 않는다(두 번째는 캐시 히트).

        Phase 3 helper — agent 가 llm-request 호출 전에 file-manager 등록 책임을
        직접 가져갈 때 사용. 결과 dict 를 그대로 StoredFile(**result) 로 변환 가능.

        Args:
            bytes_data: 파일 바이트
            metadata: 5 필드 — file_name, file_size, file_mtime, mime_type, extension

        Returns:
            6 필드 dict — metadata + {"file_path": ...}
        """
        async with self._dedup_lock(metadata):
            hash_meta = {
                "file_name": metadata["file_name"],
                "file_size": metadata["file_size"],
                "file_mtime": metadata["file_mtime"],
            }
            result = await self.check_cache(hash_meta)
            if result["hit"]:
                file_path = result["file_path"]
            else:
                file_path = await self.upload_file(bytes_data, metadata)
        return {**metadata, "file_path": file_path}

    async def close(self) -> None:
        """HTTP 클라이언트 + mdb 클라이언트 연결 종료."""
        if self._client is not None:
            await self._client.close()
        await close_mdb()
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
