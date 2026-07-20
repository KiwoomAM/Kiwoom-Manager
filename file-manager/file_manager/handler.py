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
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Optional

from file_manager import _dedup
from file_manager._client import AsyncClient
from file_manager._mdb import close_mdb, find_metadata, insert_metadata
from file_manager.exceptions import MetadataNotFoundError

logger = logging.getLogger(__name__)

_API_PREFIX = "/v1/files"

# 밀리초 epoch 판별 임계값. 초 단위 epoch은 33658년까지도 10^12에 못 미치고,
# 밀리초 단위는 2001년 이후로 이미 10^12를 넘으므로 둘을 안전하게 가른다.
_MTIME_MS_THRESHOLD = 1_000_000_000_000


def _demillis(n: int) -> int:
    """10^12 이상이면 밀리초 epoch으로 보고 초로 환산, 아니면 그대로."""
    return n // 1000 if n >= _MTIME_MS_THRESHOLD else n


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

    @staticmethod
    def normalize_mtime(value) -> int:
        """다양한 표현의 file_mtime을 int(epoch seconds)로 통일.

        소비처마다 제각각인 mtime 표현(정수/정수문자열/ISO 문자열)을
        build_metadata가 요구하는 int로 맞추는 순수 유틸(HTTP 없음).

        - int/float → int (float는 절삭)
        - 숫자 문자열("1700000000", "1700000000.9") → int로 절삭
        - 밀리초 epoch(10^12 이상) → 1000으로 나눠 초로 환산
        - ISO 8601 문자열("2026-05-11", "2026-01-01T00:00:00Z") → epoch seconds
        - None / 공백 / str 아님 / 변환 실패 → 0 (메타 식별자로만 쓰이는 폴백)

        주의: 타임존이 없는 ISO 문자열은 서버 로컬 타임존으로 해석된다(호스트 TZ에
        따라 같은 문자열이 다른 epoch이 될 수 있음). 캐시 식별자로만 쓰이므로 무방하다.
        """
        if isinstance(value, (int, float)):
            return _demillis(int(value))
        if not isinstance(value, str):
            return 0
        text = value.strip()
        if not text:
            return 0
        try:
            return _demillis(int(text))
        except ValueError:
            pass
        try:
            # 소수점 숫자 문자열("1700000000.9")도 float 값과 동일하게 절삭 (비대칭 제거).
            # nan/inf는 int()가 ValueError/OverflowError로 걸러 ISO 파싱으로 넘어간다.
            return _demillis(int(float(text)))
        except (ValueError, OverflowError):
            pass
        try:
            # 'Z' 접미사를 표준 +00:00으로 정규화 (Python 3.11 미만 fromisoformat 대응;
            # 3.11+는 'Z'를 네이티브로 파싱하므로 이 치환은 no-op 안전장치)
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            logger.warning("file_mtime 변환 실패, 0으로 대체: %r", value)
            return 0

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
        key = _dedup.standby_key(metadata)

        async def _find_cache():
            r = await self.check_cache(hash_meta)
            return r if r["hit"] else None

        # standby로 감싼 check→upload를 "재선점 루프"로 직렬화한다.
        # 선점자의 upload_file이 실패해 standby가 풀려도, 대기자 전원이 동시에 직접
        # 업로드로 쏟아지지 않고 "다음 한 명만" 재선점해 업로드한다(thundering herd 방지,
        # 구 asyncio.Lock의 직렬 재시도 의미론 복원). 상한 초과 시에만 직접 업로드로 degrade.
        for _ in range(_dedup.STANDBY_MAX_ROUNDS):
            # 1) 이미 저장돼 있으면 바로 반환
            result = await self.check_cache(hash_meta)
            if result["hit"]:
                return {**metadata, "file_path": result["file_path"]}

            # 2) standby 선점 시도 (Redis 미연결 → standby 없이 직접 업로드로 degrade)
            try:
                token = await _dedup.try_acquire_standby(key)
            except RuntimeError:
                file_path = await self.upload_file(bytes_data, metadata)
                return {**metadata, "file_path": file_path}

            # 3) 선점 성공 → 업로드 후 "내 토큰으로만" 해제(만료 후 남이 재선점한 키 삭제 방지)
            if token is not None:
                try:
                    file_path = await self.upload_file(bytes_data, metadata)
                finally:
                    await _dedup.release_standby(key, token)
                return {**metadata, "file_path": file_path}

            # 4) 미선점 → 대기. 남이 저장 완료했으면 재사용, 아니면(선점자 실패/해제-miss) 재선점.
            try:
                cached = await _dedup.wait_for_standby_release(
                    key, timeout=_dedup.STANDBY_TTL_SECONDS, find_cache_fn=_find_cache
                )
            except Exception:
                logger.exception(
                    "wait_for_standby_release 예외(무시, 재선점): key=%s", key
                )
                cached = None
            if cached is not None:
                return {**metadata, "file_path": cached["file_path"]}
            # cached is None → 루프 상단으로 돌아가 재선점

        # 재선점 상한 초과 → degrade: 직접 업로드(드물게 중복 가능)
        logger.warning(
            "standby 재선점 상한(%d회) 초과 — 직접 업로드 degrade. key=%s",
            _dedup.STANDBY_MAX_ROUNDS, key,
        )
        file_path = await self.upload_file(bytes_data, metadata)
        return {**metadata, "file_path": file_path}

    async def register_from_bytes(
        self, data: bytes, *, file_name: str, file_mtime: int
    ) -> dict:
        """바이트에서 바로 등록. build_metadata + register_or_get을 한 번에 흡수.

        소비처가 여기저기 복붙하던 (build_metadata → register_or_get) 2줄을 한 줄로
        줄인다. file_size는 len(data)로 내부 계산하므로 소비처가 따로 챙기지 않는다
        (클라이언트가 보낸 크기보다 실제 바이트 길이가 정확).

        Args:
            data: 파일 바이트
            file_name: 원본 파일명 (확장자 포함 — mime_type/extension 파생에 쓰임)
            file_mtime: 파일 수정 시간 (epoch seconds) — 바이트에서 도출 불가라 필수

        Returns:
            6 필드 dict — metadata + {"file_path": ...} (register_or_get 반환 그대로)
        """
        metadata = self.build_metadata(file_name, file_mtime, len(data))
        return await self.register_or_get(data, metadata)

    async def register_from_path(self, path: Path) -> dict:
        """경로에서 바로 등록. 경로에서 메타를 도출해 register_from_bytes에 위임.

        file_name(=path.name)·file_mtime(=int(mtime))을 경로에서 도출하고,
        바이트 읽기는 asyncio.to_thread로 off-loop 처리해 이벤트 루프를 막지 않는다.

        Args:
            path: 읽어서 등록할 파일 경로

        Returns:
            6 필드 dict — register_from_bytes 반환 그대로

        Raises:
            FileNotFoundError: path가 존재하지 않으면 번역 없이 전파.
        """
        data = await asyncio.to_thread(path.read_bytes)
        file_mtime = int(path.stat().st_mtime)
        return await self.register_from_bytes(
            data, file_name=path.name, file_mtime=file_mtime
        )

    @asynccontextmanager
    async def materialize_to_tempfile(self, data: bytes, *, suffix: str = ""):
        """바이트를 임시파일로 떨궈 Path를 yield하고, 종료 시 unlink.

        file-manager는 파일을 바이트로 돌려주는데 하류 추출기(pandas·PyMuPDF 등)는
        경로(Path)를 요구한다. 그 "바이트 → 임시 Path → 사용 후 정리" 패턴을 흡수한다.
        쓰기는 asyncio.to_thread로 off-loop 처리하고, 정리는 정상·예외 경로 모두 보장한다.

        Args:
            data: 임시파일에 쓸 바이트
            suffix: 임시파일 확장자 (예: ".pdf") — 확장자 기반 추출기 대응

        Yields:
            생성된 임시파일 Path (블록을 벗어나면 삭제됨)
        """
        def _write() -> str:
            tf = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                with tf:
                    tf.write(data)
            except Exception:
                # 쓰기 실패 시 orphan 방지 — 이름 반환 전이라 바깥 finally가 못 지운다
                Path(tf.name).unlink(missing_ok=True)
                raise
            return tf.name

        name = await asyncio.to_thread(_write)
        path = Path(name)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

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
