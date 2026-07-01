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

import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Optional

from file_manager import _dedup
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

    async def register_or_get(
        self,
        bytes_data: bytes,
        metadata: dict,
    ) -> dict:
        """check_cache → miss 면 upload_file → 6 필드 dict 반환.

        같은 (file_name, file_mtime, file_size) 동시 호출을 Redis standby(SETNX)로
        **프로세스·워커 넘어** 직렬화한다 — 첫 요청만 업로드하고, 나머지는 대기 후
        캐시 결과를 재사용해 중복 저장을 막는다. Redis 미연결/타임아웃 시에는 직접
        업로드로 degrade(가용성 우선; 드물게 중복 가능).

        Args:
            bytes_data: 파일 바이트
            metadata: 5 필드 — file_name, file_size, file_mtime, mime_type, extension

        Returns:
            6 필드 dict — metadata + {"file_path": ...}
        """
        hash_meta = {
            "file_name": metadata["file_name"],
            "file_size": metadata["file_size"],
            "file_mtime": metadata["file_mtime"],
        }

        # 1) fast path: 이미 저장돼 있으면 바로 반환
        result = await self.check_cache(hash_meta)
        if result["hit"]:
            return {**metadata, "file_path": result["file_path"]}

        key = _dedup.standby_key(metadata)

        async def _find_cache():
            r = await self.check_cache(hash_meta)
            return r if r["hit"] else None

        # 2) standby 선점 시도 (Redis 미연결 시 단독 작업자로 degrade)
        try:
            acquired = await _dedup.try_acquire_standby(key)
        except RuntimeError:
            acquired = True

        # 3) 미선점이면 남의 작업 완료를 대기하고 캐시 재사용
        if not acquired:
            try:
                cached = await _dedup.wait_for_standby_release(
                    key, timeout=_dedup.STANDBY_TTL_SECONDS, find_cache_fn=_find_cache
                )
            except Exception:
                logger.exception(
                    "wait_for_standby_release 예외(무시, 직접 업로드 폴백): key=%s", key
                )
                cached = None
            if cached is not None:
                return {**metadata, "file_path": cached["file_path"]}
            # timeout/해제-miss → 폴백: 내가 직접 업로드(선점 안 했으므로 release 안 함)

        # 4) 업로드. 선점했다면 finally에서 standby 해제
        try:
            file_path = await self.upload_file(bytes_data, metadata)
        finally:
            if acquired:
                await _dedup.release_standby(key)
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
