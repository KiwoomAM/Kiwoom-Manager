"""file_manager SDK — FM Service API를 호출하는 래퍼.

서버 URL은 환경변수 FILE_MANAGER_URL (기본 http://localhost:8102).
"""

from __future__ import annotations

import base64
import os

import httpx

from file_manager.models import CacheCheckResult, FileMeta

_DEFAULT_URL = "http://localhost:8102"
_DEFAULT_TIMEOUT = 30.0


def _base_url() -> str:
    return os.environ.get("FILE_MANAGER_URL", _DEFAULT_URL)


# ── sync ─────────────────────────────────────────────────────


def check_cache(metadata: dict, *, timeout: float = _DEFAULT_TIMEOUT) -> CacheCheckResult:
    """캐시 조회 — file_name, file_size, file_mtime으로 기존 파일 확인."""
    with httpx.Client(base_url=_base_url(), timeout=timeout) as c:
        resp = c.post("/files/check-cache", json=metadata)
        resp.raise_for_status()
        return CacheCheckResult.model_validate(resp.json())


def upload_file(file_data: bytes, metadata: dict, *, timeout: float = _DEFAULT_TIMEOUT) -> FileMeta:
    """파일 업로드 — 캐시 미스 시 FM Service에 파일 + 메타데이터 전송."""
    payload = {
        **metadata,
        "file_data_b64": base64.b64encode(file_data).decode(),
    }
    with httpx.Client(base_url=_base_url(), timeout=timeout) as c:
        resp = c.post("/files/upload", json=payload)
        resp.raise_for_status()
        return FileMeta.model_validate(resp.json()["metadata"])


def get_file(file_path: str, *, timeout: float = _DEFAULT_TIMEOUT) -> bytes:
    """파일 조회 — file_path로 FM Service에서 파일 bytes 반환."""
    with httpx.Client(base_url=_base_url(), timeout=timeout) as c:
        resp = c.post("/files/fetch", json={"file_path": file_path})
        resp.raise_for_status()
        return resp.content


# ── async ────────────────────────────────────────────────────


async def acheck_cache(metadata: dict, *, timeout: float = _DEFAULT_TIMEOUT) -> CacheCheckResult:
    """check_cache의 async 버전."""
    async with httpx.AsyncClient(base_url=_base_url(), timeout=timeout) as c:
        resp = await c.post("/files/check-cache", json=metadata)
        resp.raise_for_status()
        return CacheCheckResult.model_validate(resp.json())


async def aupload_file(file_data: bytes, metadata: dict, *, timeout: float = _DEFAULT_TIMEOUT) -> FileMeta:
    """upload_file의 async 버전."""
    payload = {
        **metadata,
        "file_data_b64": base64.b64encode(file_data).decode(),
    }
    async with httpx.AsyncClient(base_url=_base_url(), timeout=timeout) as c:
        resp = await c.post("/files/upload", json=payload)
        resp.raise_for_status()
        return FileMeta.model_validate(resp.json()["metadata"])


async def aget_file(file_path: str, *, timeout: float = _DEFAULT_TIMEOUT) -> bytes:
    """get_file의 async 버전."""
    async with httpx.AsyncClient(base_url=_base_url(), timeout=timeout) as c:
        resp = await c.post("/files/fetch", json={"file_path": file_path})
        resp.raise_for_status()
        return resp.content
