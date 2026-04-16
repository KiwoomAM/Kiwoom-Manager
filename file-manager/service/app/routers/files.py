"""파일 관리 라우터.

POST /files/check-cache — 캐시 조회 (file_name, file_size, file_mtime)
POST /files/upload       — 파일 업로드 (캐시 미스 시)
POST /files/fetch        — 파일 bytes 반환 (file_path)
"""

from __future__ import annotations

import base64
import logging
from typing import Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app import store

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request/Response 모델 ────────────────────────────────────


class CheckCacheRequest(BaseModel):
    file_name: str
    file_size: int
    file_mtime: Union[str, int]


class UploadRequest(BaseModel):
    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    file_data_b64: str  # base64 인코딩된 파일 데이터


class FetchRequest(BaseModel):
    file_path: str


# ── 엔드포인트 ────────────────────────────────────────────────


@router.post("/files/check-cache")
async def check_cache(body: CheckCacheRequest) -> JSONResponse:
    """캐시 조회 — file_name + file_size + file_mtime으로 MDB 조회."""
    cached = store.check_cache(body.file_name, body.file_size, body.file_mtime)
    if cached:
        cached.pop("_id", None)
        return JSONResponse(content={"hit": True, "metadata": cached})
    return JSONResponse(content={"hit": False, "metadata": None})


@router.post("/files/upload")
async def upload_file(body: UploadRequest) -> JSONResponse:
    """파일 업로드 — 파일 서버에 UUID.ext로 저장 + MDB에 메타데이터 저장."""
    file_data = base64.b64decode(body.file_data_b64)

    file_path = store.save_file(file_data, body.file_name)

    metadata = store.build_metadata(
        file_name=body.file_name,
        file_mtime=body.file_mtime,
        file_size=body.file_size,
        file_path=file_path,
    )

    store.save_metadata(metadata)

    return JSONResponse(content={"metadata": metadata})


@router.post("/files/fetch")
async def fetch_file(body: FetchRequest) -> Response:
    """파일 조회 — file_path로 파일 서버에서 bytes 반환."""
    try:
        data = store.get_file_bytes(body.file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"file not found: {body.file_path}")

    return Response(content=data, media_type="application/octet-stream")
