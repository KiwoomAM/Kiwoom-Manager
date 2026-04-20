"""file_cache Redis 저장소.

Files API URI와 Context Cache name을 Redis에 캐싱한다.
redis-handler의 BaseJsonStore 패턴을 사용.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from llm_handler.models import FileRef
from redis_handler import BaseJsonStore

from app.logic.keying import canonicalize_llm_input, make_cache_key

logger = logging.getLogger(__name__)

# TTL 상수
FILES_API_TTL = 169200  # 47시간 (Google Files API 48시간 만료보다 1시간 짧게 → 폴백용)
FILES_API_TTL_FLOOR = 3600  # 1시간 — compute_files_api_ttl의 최소 보장
CONTEXT_CACHE_DEFAULT_TTL = 3600  # 1시간


class FileCacheStore(BaseJsonStore):
    """file_cache Redis 저장소."""

    def __init__(self) -> None:
        super().__init__(prefix="file_cache")


# 모듈 레벨 싱글톤
_store = FileCacheStore()


# ── hash_key 생성 ────────────────────────────────────────────


def make_context_cache_key(
    file_checksums: list[str],
    model: str | None = None,
    system_instruction: str | None = None,
) -> str:
    """Context Cache용 hash_key를 생성한다.

    keying.py의 canonicalize + sha256 방식과 동일.
    """
    canonical = canonicalize_llm_input(
        {
            "file_checksums": sorted(file_checksums),
            "model": model or "",
            "system_instruction": system_instruction or "",
        }
    )
    return make_cache_key(canonical)


# ── Files API TTL 동적 계산 ──────────────────────────────────


def compute_files_api_ttl(
    refs: list[FileRef],
    *,
    buffer_seconds: int = 3600,
) -> int:
    """FileRef.expiration_time 기반 동적 Redis TTL 계산.

    Gemini가 주는 실제 만료 시각에서 buffer만큼 빼서 Redis TTL로 사용한다.
    이 값으로 save_files_api(..., ttl=...)를 호출하면 Redis가 Gemini보다
    일찍 만료되도록 보장하여 stale URI 반환을 방지.

    규칙:
      - 모든 refs에 expiration_time 있음 → (가장 이른 만료 - now - buffer_seconds)
      - 일부라도 없음/파싱 실패 → FILES_API_TTL(47h) 폴백
      - 계산 결과 < FILES_API_TTL_FLOOR → floor 적용 (최소 1h 보장)
      - 상한: FILES_API_TTL

    Args:
        refs: upload_file 반환 FileRef 리스트 (split PDF는 여러 개).
        buffer_seconds: Gemini 만료보다 얼마나 일찍 Redis가 만료되게 할지 (기본 1h).

    Returns:
        Redis TTL 초.
    """
    if not refs:
        return FILES_API_TTL
    if not all(r.expiration_time for r in refs):
        return FILES_API_TTL

    try:
        parsed = [datetime.fromisoformat(r.expiration_time) for r in refs]  # type: ignore[arg-type]
    except (ValueError, TypeError) as e:
        logger.warning("FileRef.expiration_time 파싱 실패: %s — 폴백 TTL 사용", e)
        return FILES_API_TTL

    now = datetime.now(UTC)
    earliest_exp = min(parsed)
    remaining = int((earliest_exp - now).total_seconds()) - buffer_seconds

    if remaining < FILES_API_TTL_FLOOR:
        logger.warning(
            "compute_files_api_ttl: 잔여시간 %d초 < floor(%d초) — floor 적용. "
            "Gemini 만료 임박, 404 자동 복구 경로 가동 가능성.",
            remaining, FILES_API_TTL_FLOOR,
        )
        return FILES_API_TTL_FLOOR

    return min(remaining, FILES_API_TTL)


# ── Files API CRUD ───────────────────────────────────────────


def find_files_api(file_checksum: str) -> dict | None:
    """file_checksum으로 Files API 레코드 조회."""
    key = _store._make_key("files_api", file_checksum)
    return _store._get_json(key)


def save_files_api(
    file_checksum: str,
    doc: dict,
    ttl: int = FILES_API_TTL,
) -> None:
    """Files API 레코드 저장."""
    key = _store._make_key("files_api", file_checksum)
    _store._set_json(key, doc, ttl=ttl)


# ── Context Cache CRUD ───────────────────────────────────────


def find_context_cache(hash_key: str) -> dict | None:
    """hash_key로 Context Cache 레코드 조회.""" 
    key = _store._make_key("context_cache", hash_key)
    return _store._get_json(key)


def save_context_cache(
    hash_key: str,
    doc: dict,
    ttl: int = CONTEXT_CACHE_DEFAULT_TTL,
) -> None:
    """Context Cache 레코드 저장."""
    key = _store._make_key("context_cache", hash_key)
    _store._set_json(key, doc, ttl=ttl)


# ── 삭제 ─────────────────────────────────────────────────────


def delete_files_api(file_checksum: str) -> None:
    """Files API 레코드 삭제 (404 fallback용)."""
    key = _store._make_key("files_api", file_checksum)
    _store._delete(key)


def delete_context_cache(hash_key: str) -> None:
    """Context Cache 레코드 삭제 (404 fallback용)."""
    key = _store._make_key("context_cache", hash_key)
    _store._delete(key)
