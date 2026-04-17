# -*- coding: utf-8 -*-
"""
File Manager Service 파일 관리 라우터

엔드포인트:
  POST /v1/files/check-cache  — 캐시 체크 (메타데이터 3개로 MongoDB 조회)
  POST /v1/files/upload        — 파일 업로드 (파일 + 메타데이터)
  GET  /v1/files/{file_path}   — 파일 반환 (File Server에서 꺼내기)
"""

import logging
import mimetypes
import uuid
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, Response, UploadFile

from app.config import settings
from app.core.errors import MDBConnectionError
from app.core import storage
from app.models.requests import CheckCacheRequest
from app.models.responses import CacheCheckResponse, FileMetadataResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/files", tags=["files"])

# ─── MDB Handler HTTP 클라이언트 ──────────────────────────────────────

_mdb_client: httpx.AsyncClient | None = None
_mdb_prefix = f"/v1/{settings.mdb_database}/{settings.mdb_collection}"


def get_mdb_client() -> httpx.AsyncClient:
    global _mdb_client
    if _mdb_client is None:
        _mdb_client = httpx.AsyncClient(
            base_url=settings.mdb_handler_url,
            timeout=30.0,
        )
    return _mdb_client


async def close_mdb_client() -> None:
    global _mdb_client
    if _mdb_client is not None:
        await _mdb_client.aclose()
        _mdb_client = None


# ─── MDB Handler 호출 헬퍼 ────────────────────────────────────────────


async def _mdb_find_one(filter: dict) -> dict | None:
    """MDB Handler에서 단일 문서 조회"""
    try:
        client = get_mdb_client()
        resp = await client.post(
            f"{_mdb_prefix}/find_one",
            json={"filter": filter},
        )
        resp.raise_for_status()
        return resp.json().get("data")
    except httpx.ConnectError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except httpx.TimeoutException as e:
        raise MDBConnectionError(f"MDB Handler 응답 타임아웃: {e}") from e
    except httpx.HTTPStatusError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.response.status_code}): {e.response.text}"
        ) from e


async def _mdb_insert_one(document: dict) -> str:
    """MDB Handler에 단일 문서 삽입"""
    try:
        client = get_mdb_client()
        resp = await client.post(
            f"{_mdb_prefix}/insert_one",
            json={"document": document},
        )
        resp.raise_for_status()
        return resp.json()["inserted_id"]
    except httpx.ConnectError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except httpx.TimeoutException as e:
        raise MDBConnectionError(f"MDB Handler 응답 타임아웃: {e}") from e
    except httpx.HTTPStatusError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.response.status_code}): {e.response.text}"
        ) from e


# ─── 엔드포인트 ───────────────────────────────────────────────────────


@router.post("/check-cache", response_model=CacheCheckResponse)
async def check_cache(req: CheckCacheRequest):
    """캐시 체크: file_name + file_mtime + file_size로 MongoDB 조회"""

    filter = {
        "metadata.file_name": req.file_name,
        "metadata.file_mtime": req.file_mtime,
        "metadata.file_size": req.file_size,
    }

    doc = await _mdb_find_one(filter)

    if doc is None:
        return CacheCheckResponse(hit=False)

    metadata = doc["metadata"]
    return CacheCheckResponse(
        hit=True,
        metadata=FileMetadataResponse(**metadata),
    )


@router.post("/upload", response_model=FileMetadataResponse)
async def upload_file(
    file: UploadFile = File(...),
    file_name: str = Form(...),
    file_mtime: int = Form(...),
    file_size: int = Form(...),
):
    """파일 업로드: 파일 저장 + 메타데이터 MongoDB 저장"""

    # 1) UUID 생성 및 file_path 구성
    file_ext = PurePosixPath(file_name).suffix
    file_path = f"{uuid.uuid4().hex}{file_ext}"

    # 2) mime_type, extension 추출
    mime_type, _ = mimetypes.guess_type(file_name)
    mime_type = mime_type or "application/octet-stream"
    extension = PurePosixPath(file_name).suffix.lstrip(".")

    # 3) File Server에 저장
    file_data = await file.read()
    await storage.save_file(file_path, file_data)

    # 4) 메타데이터 구성
    metadata = {
        "file_name": file_name,
        "file_mtime": file_mtime,
        "file_size": file_size,
        "file_path": file_path,
        "mime_type": mime_type,
        "extension": extension,
    }

    # 5) MDB Handler에 메타데이터 저장
    await _mdb_insert_one({"metadata": metadata})

    logger.info("파일 업로드 완료: %s → %s", file_name, file_path)
    return FileMetadataResponse(**metadata)


@router.get("/{file_path:path}")
async def get_file(file_path: str):
    """File Server에서 파일을 꺼내 반환"""

    file_data = await storage.read_file(file_path)

    # MongoDB에서 원본 파일명 조회
    doc = await _mdb_find_one({"metadata.file_path": file_path})
    if doc:
        original_name = doc["metadata"]["file_name"]
        mime_type = doc["metadata"].get("mime_type", "application/octet-stream")
    else:
        original_name = file_path
        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"

    encoded_name = quote(original_name)
    return Response(
        content=file_data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
        },
    )
