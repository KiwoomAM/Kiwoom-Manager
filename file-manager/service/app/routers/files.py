# -*- coding: utf-8 -*-
"""
File Manager Service 파일 관리 라우터

엔드포인트:
  POST /v1/files/check-cache  — 캐시 체크 (캐시 키 3개로 MongoDB 조회, file_path 반환)
  POST /v1/files/upload        — 파일 업로드 (파일 + 5개 필드 메타데이터, file_path 반환)
  GET  /v1/files/{file_path}   — 파일 반환 (File Server에서 꺼내기)
"""

import json
import logging
import uuid
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, Response, UploadFile

from app.config import MONGODB_CACHE_COLLECTION, settings
from app.core.errors import MDBConnectionError, MetadataNotFoundError
from app.models.requests import CheckCacheRequest
from app.models.responses import CacheCheckResponse, UploadResponse
from file_handler import read_file, save_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/files", tags=["files"])

# ─── MDB Handler HTTP 클라이언트 ──────────────────────────────────────

_mdb_client: httpx.AsyncClient | None = None
_mdb_prefix = f"/v1/{settings.mongodb_database}/{MONGODB_CACHE_COLLECTION}"


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


async def _mdb_call(path: str, json: dict) -> dict:
    """MDB Handler POST 호출 + httpx 예외를 MDBConnectionError로 번역."""
    try:
        resp = await get_mdb_client().post(path, json=json)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except httpx.TimeoutException as e:
        raise MDBConnectionError(f"MDB Handler 응답 타임아웃: {e}") from e
    except httpx.HTTPStatusError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.response.status_code}): {e.response.text}"
        ) from e


async def _mdb_find_one(filter: dict) -> dict | None:
    """MDB Handler에서 단일 문서 조회"""
    body = await _mdb_call(f"{_mdb_prefix}/find_one", {"filter": filter})
    return body.get("data")


async def _mdb_insert_one(document: dict) -> str:
    """MDB Handler에 단일 문서 삽입"""
    body = await _mdb_call(f"{_mdb_prefix}/insert_one", {"document": document})
    return body["inserted_id"]


# ─── 엔드포인트 ───────────────────────────────────────────────────────


@router.post("/check-cache", response_model=CacheCheckResponse)
async def check_cache(req: CheckCacheRequest):
    """캐시 체크: 캐시 키 3개로 MongoDB 조회 → file_path만 반환"""

    filter = {
        "metadata.file_name": req.file_name,
        "metadata.file_mtime": req.file_mtime,
        "metadata.file_size": req.file_size,
    }

    doc = await _mdb_find_one(filter)

    if doc is None:
        return CacheCheckResponse(hit=False)

    return CacheCheckResponse(
        hit=True,
        file_path=doc["metadata"]["file_path"],
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    file_name: str = Form(...),
    file_mtime: int = Form(...),
    file_size: int = Form(...),
    mime_type: str = Form(...),
    extension: str = Form(...),
):
    """파일 업로드: 파일 저장 + 6개 필드 MongoDB 저장 → file_path만 반환.

    mime_type/extension은 SDK가 추출해서 전달하며, FM Service는 재추출하지 않는다.
    """

    # 1) UUID 생성 및 file_path 구성 (extension은 SDK가 전달한 값 사용)
    file_path = f"{uuid.uuid4().hex}.{extension}" if extension else uuid.uuid4().hex

    # 2) File Server에 저장
    file_data = await file.read()
    await save_file(file_path, file_data)

    # 3) 메타데이터 구성 (SDK가 준 mime_type/extension을 그대로 사용)
    metadata = {
        "file_name": file_name,
        "file_mtime": file_mtime,
        "file_size": file_size,
        "file_path": file_path,
        "mime_type": mime_type,
        "extension": extension,
    }

    # 4) MDB Handler에 메타데이터 저장
    await _mdb_insert_one({"metadata": metadata})

    logger.info("파일 업로드 완료: %s → %s", file_name, file_path)
    return UploadResponse(file_path=file_path)


@router.get("/{file_path:path}")
async def get_file(file_path: str):
    """File Server에서 파일을 꺼내 반환 + MongoDB의 6개 필드 메타데이터도 같이 전달.

    - 응답 body: 파일 바이너리
    - 응답 헤더 `X-File-Metadata`: URL-encoded JSON (6개 필드)
    - 응답 헤더 `Content-Disposition`: 브라우저 다운로드 UX용 (원본 파일명)

    정상 흐름에선 반드시 MongoDB에 기록이 존재하며, 없을 경우 데이터 정합성 오류로 간주한다.
    """

    file_data = await read_file(file_path)

    doc = await _mdb_find_one({"metadata.file_path": file_path})
    if doc is None:
        raise MetadataNotFoundError(file_path)

    metadata = doc["metadata"]  # 6개 필드: file_name, file_mtime, file_size, mime_type, extension, file_path

    encoded_name = quote(metadata["file_name"])
    metadata_header = quote(json.dumps(metadata, ensure_ascii=False))

    return Response(
        content=file_data,
        media_type=metadata["mime_type"],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            "X-File-Metadata": metadata_header,
        },
    )
