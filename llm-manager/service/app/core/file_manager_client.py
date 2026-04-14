"""file_manager Service HTTP 클라이언트.

Service ↔ file_manager 간 파일 bytes 조회 (POST /files/fetch).
인터페이스 가정: docs/file-manager-separation.md §3.3 참고.
"""

from __future__ import annotations

import httpx

from app.core.errors import LLMRequestServiceError
from app.core.providers import get_file_manager_client


async def fetch_file_bytes(file_path: str) -> bytes:
    """file_manager로부터 파일 bytes를 받아온다.

    Args:
        file_path: file_server 절대경로.

    Returns:
        파일 내용 bytes.

    Raises:
        LLMRequestServiceError:
            - 503 (error_type="file_manager"): 연결 실패
            - 504 (error_type="file_manager"): 타임아웃
            - 502 (error_type="file_manager"): 404 (파일 없음 — 설계상 발생 안 함 가정이나 방어)
    """
    client = get_file_manager_client()
    try:
        resp = await client.post("/files/fetch", json={"file_path": file_path})
    except httpx.ConnectError as e:
        raise LLMRequestServiceError(
            f"file_manager unavailable: {e}",
            status_code=503,
            error_type="file_manager",
        ) from e
    except httpx.TimeoutException as e:
        raise LLMRequestServiceError(
            f"file_manager timeout: {e}",
            status_code=504,
            error_type="file_manager",
        ) from e

    if resp.status_code == 404:
        raise LLMRequestServiceError(
            f"file_manager: file not found ({file_path})",
            status_code=502,
            error_type="file_manager",
        )

    resp.raise_for_status()
    return resp.content
