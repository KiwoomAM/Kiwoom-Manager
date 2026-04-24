"""file_cache Redis 저장소.

Files API URI와 Context Cache name을 Redis에 캐싱한다.
redis-handler의 BaseJsonStore 패턴을 사용 — 자식 클래스 안에서 `self._make_key`,
`self._get_json` 등 protected 메서드를 호출하는 게 BaseJsonStore docstring의 권장
사용법. 모듈 레벨 함수에서 `_store._private` 직접 호출은 캡슐화 위반이므로,
도메인 메서드는 `FileCacheStore` 클래스 안에 두고 모듈 함수는 단순 위임으로 유지.
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
    """file_cache Redis 저장소.

    BaseJsonStore 가 의도하는 사용 패턴 — 도메인 메서드를 자식 클래스 안에서 정의하고
    `self._make_key/_get_json/_set_json/_delete` 같은 protected 헬퍼를 호출.
    """

    def __init__(self) -> None:
        super().__init__(prefix="file_cache")

    # ── Files API CRUD ───────────────────────────────────────

    def find_files_api(self, file_checksum: str) -> dict | None:
        """file_checksum으로 Files API 레코드 조회."""
        return self._get_json(self._make_key("files_api", file_checksum))

    def save_files_api(
        self,
        file_checksum: str,
        doc: dict,
        ttl: int = FILES_API_TTL,
    ) -> None:
        """Files API 레코드 저장."""
        self._set_json(self._make_key("files_api", file_checksum), doc, ttl=ttl)

    def delete_files_api(self, file_checksum: str) -> None:
        """Files API 레코드 삭제 (404 fallback용)."""
        self._delete(self._make_key("files_api", file_checksum))

    # ── Context Cache CRUD ───────────────────────────────────

    def find_context_cache(self, hash_key: str) -> dict | None:
        """hash_key로 Context Cache 레코드 조회."""
        return self._get_json(self._make_key("context_cache", hash_key))

    def save_context_cache(
        self,
        hash_key: str,
        doc: dict,
        ttl: int = CONTEXT_CACHE_DEFAULT_TTL,
    ) -> None:
        """Context Cache 레코드 저장."""
        self._set_json(self._make_key("context_cache", hash_key), doc, ttl=ttl)

    def delete_context_cache(self, hash_key: str) -> None:
        """Context Cache 레코드 삭제 (404 fallback용)."""
        self._delete(self._make_key("context_cache", hash_key))


# 모듈 레벨 싱글톤
_store = FileCacheStore()


# ── hash_key 생성 ────────────────────────────────────────────


def make_context_cache_key(
    file_checksums: list[str],
    model: str | None = None,
    system_instruction: str | None = None,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> str:
    """Context Cache용 hash_key를 생성한다.

    keying.py의 canonicalize + sha256 방식과 동일.

    `tools` / `tool_config` 도 key 재료에 포함 — cache 생성 시 이들도 함께 저장되므로,
    동일 파일·모델·system 이라도 tools/tool_config 가 다르면 별도 cache 로 분리돼야
    오염된 cache HIT (이전 tools 로 생성된 cache 를 다른 tools 요청에 HIT 시키는 버그)
    를 방지할 수 있다.
    """
    canonical = canonicalize_llm_input(
        {
            "file_checksums": sorted(file_checksums),
            "model": model or "",
            "system_instruction": system_instruction or "",
            "tools": tools or [],
            "tool_config": tool_config or "",
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


# ── 모듈 레벨 위임 함수 (호출자 호환성 유지) ──────────────────
# resolver.py 등 기존 호출자가 `from app.logic.file_processing import cache_store`
# 후 `cache_store.find_files_api(...)` 형태로 사용 중이므로, 이 인터페이스를
# 그대로 유지한다. 실제 로직은 FileCacheStore 클래스 메서드에 위임.


def find_files_api(file_checksum: str) -> dict | None:
    """file_checksum으로 Files API 레코드 조회."""
    return _store.find_files_api(file_checksum)


def save_files_api(
    file_checksum: str,
    doc: dict,
    ttl: int = FILES_API_TTL,
) -> None:
    """Files API 레코드 저장."""
    _store.save_files_api(file_checksum, doc, ttl=ttl)


def find_context_cache(hash_key: str) -> dict | None:
    """hash_key로 Context Cache 레코드 조회."""
    return _store.find_context_cache(hash_key)


def save_context_cache(
    hash_key: str,
    doc: dict,
    ttl: int = CONTEXT_CACHE_DEFAULT_TTL,
) -> None:
    """Context Cache 레코드 저장."""
    _store.save_context_cache(hash_key, doc, ttl=ttl)


def delete_files_api(file_checksum: str) -> None:
    """Files API 레코드 삭제 (404 fallback용)."""
    _store.delete_files_api(file_checksum)


def delete_context_cache(hash_key: str) -> None:
    """Context Cache 레코드 삭제 (404 fallback용)."""
    _store.delete_context_cache(hash_key)
