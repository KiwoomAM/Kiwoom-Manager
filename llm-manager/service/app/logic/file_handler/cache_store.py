"""file_cache Redis 저장소.

Files API URI와 Context Cache name을 Redis에 캐싱한다.
redis-handler의 BaseJsonStore 패턴을 사용.
"""

from __future__ import annotations

from redis_handler import BaseJsonStore

from app.logic.keying import canonicalize_llm_input, make_cache_key

# TTL 상수
FILES_API_TTL = 169200  # 47시간 (Google Files API 48시간 만료보다 1시간 짧게 → 만료 URI 방지)
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
