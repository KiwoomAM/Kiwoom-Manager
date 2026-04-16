"""외부 서비스 연결 관리."""

import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

# standby flush 대상 패턴 (cache stampede dedup)
_STANDBY_PATTERNS: tuple[str, ...] = (
    "file_cache:*_standby:*",
    "agent_llm_standby:*",
)


def _ensure_redis_connected() -> None:
    """Redis 연결 확보. 실패 시 RuntimeError raise.

    init_redis()는 실패해도 False 반환(메모리 폴백)하므로 명시적 체크 필요.
    standby dedup은 Redis 필수 — 폴백 불가.
    """
    from redis_handler import init_redis
    if not init_redis():
        raise RuntimeError(
            "Redis 연결 실패 — llm-request를 시작할 수 없습니다. "
            "REDIS_URL 설정 및 Redis 서버 상태를 확인하세요."
        )


def _flush_stale_standbys() -> None:
    """기동 시 이전 세션의 stale standby 키 일괄 정리.

    SIGKILL 등으로 finally 미실행 시 남는 고아 키 제거.
    단일 Docker 인스턴스 전제 — 다른 서버 영향 없음 (§12 참고).
    """
    from redis_handler import get_client
    client = get_client()
    if client is None:
        return  # Redis 미연결 시 호출되지 않지만 방어

    total = 0
    for pattern in _STANDBY_PATTERNS:
        # SCAN — 커서 기반 점진적 스캔. KEYS와 달리 운영 Redis에서도 허용됨.
        for key in client.scan_iter(match=pattern, count=100):
            client.delete(key)
            total += 1

    if total > 0:
        logger.info("startup standby flush: %d stale keys 정리", total)


def configure_handlers() -> None:
    """MDB/Redis/LLM handler SDK를 서비스 설정으로 초기화.

    FastAPI lifespan에서 호출한다.
    """
    # mdb-handler SDK 설정
    os.environ.setdefault("MDB_HANDLER_URL", settings.mdb_handler_url)
    from mdb_handler import MongoDBHandler
    MongoDBHandler.reset()

    # redis-handler SDK 설정 — 연결 실패 시 기동 중단
    os.environ.setdefault("REDIS_URL", settings.redis_url)
    _ensure_redis_connected()
    _flush_stale_standbys()

    # llm-handler SDK 설정
    os.environ.setdefault("LLM_HANDLER_URL", settings.llm_handler_url)
    import llm_handler
    import llm_handler.aio
    llm_handler.configure()
    llm_handler.aio.configure()

    # MongoDB database 환경변수 설정 (store.py에서 사용)
    os.environ.setdefault("MONGODB_DATABASE", settings.mongodb_database)

    # file_manager SDK URL 설정
    os.environ.setdefault("FILE_MANAGER_URL", settings.file_manager_url)


async def shutdown_handlers() -> None:
    """FastAPI lifespan shutdown에서 호출. 리소스 정리."""
    pass
