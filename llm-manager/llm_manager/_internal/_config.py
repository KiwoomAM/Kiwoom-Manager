"""llm-manager 내부 설정 + 핸들러 lazy 초기화.

서버 시절 config.py(BaseSettings) + core/providers.py(configure_handlers, FastAPI
lifespan 호출)를 SDK 용으로 통합한다. handler URL 은 하드코딩 기본값(필요 시 환경변수
override)이고, 초기화는 첫 public 호출 시 1회 lazy 실행(double-checked).
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# 사내 핸들러 하드코딩 기본값 (환경변수로 override 가능).
MDB_HANDLER_URL = os.getenv("MDB_HANDLER_URL", "http://192.7.9.45:8300")
LLM_HANDLER_URL = os.getenv("LLM_HANDLER_URL", "http://192.7.9.45:8100")
REDIS_URL = os.getenv("REDIS_URL", "redis://192.7.9.45:7617/0")
FILE_HANDLER_URL = os.getenv("FILE_HANDLER_URL", "http://192.7.9.45:8600")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "kw_ai")

# standby flush 대상 패턴 (cache stampede dedup)
_STANDBY_PATTERNS: tuple[str, ...] = (
    "file_cache:*_standby:*",
    "agent_llm_standby:*",
)

_configured = False
_lock = threading.Lock()


def _ensure_redis_connected() -> None:
    """Redis 연결 확보. 실패 시 RuntimeError.

    init_redis()는 실패해도 False 반환(메모리 폴백)하므로 명시적 체크 필요 —
    standby dedup은 Redis 필수, 폴백 불가.
    """
    from redis_manager import init_redis
    if not init_redis():
        raise RuntimeError(
            "Redis 연결 실패 — llm-manager 를 초기화할 수 없습니다. "
            "REDIS_URL 설정 및 Redis 서버 상태를 확인하세요."
        )


def _flush_stale_standbys() -> None:
    """기동 시 이전 세션의 stale standby 키 일괄 정리 (SIGKILL 등 고아 키 제거)."""
    from redis_manager import get_client
    client = get_client()
    if client is None:
        return
    total = 0
    for pattern in _STANDBY_PATTERNS:
        for key in client.scan_iter(match=pattern, count=100):
            client.delete(key)
            total += 1
    if total > 0:
        logger.info("startup standby flush: %d stale keys 정리", total)


def ensure_configured() -> None:
    """MDB/Redis/LLM/file-manager 핸들러 SDK 를 하드코딩 URL 로 1회 초기화 (lazy).

    public API 함수 진입 시 호출. 이미 설정됐으면 즉시 반환(double-checked).
    """
    global _configured
    if _configured:  # 빠른 경로
        return
    with _lock:
        if _configured:  # lock 획득 후 재확인
            return

        # mdb-manager — agent_llm 컬렉션 write 는 mdb-handler ACL 상 caller="llm-manager" 필요.
        # 전역 SERVICE_NAME 을 덮어쓰지 않는다 — store.py 가 mdb 접근마다
        # with_caller("llm-manager") 로 per-call 주입하므로, 호스트 앱의 SERVICE_NAME·
        # mdb 싱글톤을 건드리지 않는다 (정체성 격리, 부작용 없음).
        os.environ.setdefault("MDB_HANDLER_URL", MDB_HANDLER_URL)

        # redis-handler — 연결 실패 시 초기화 중단
        os.environ.setdefault("REDIS_URL", REDIS_URL)
        _ensure_redis_connected()
        _flush_stale_standbys()

        # llm-handler 클라이언트 — Vision/대용량 PDF 분 단위 호출 대응 (기본 120s 초과)
        os.environ.setdefault("LLM_HANDLER_URL", LLM_HANDLER_URL)
        from llm_manager._internal import handler_client
        from llm_manager._internal.handler_client import aio as handler_client_aio
        # base_url 을 명시 주입한다. handler_client.get_settings() 가 lazy 라 위 setdefault 로도
        # 반영되지만, 원격 agent 에선 하드코딩 URL 을 직접 전달하는 편이 명확하고 안전하다.
        handler_client.configure(base_url=LLM_HANDLER_URL, timeout=600.0)
        handler_client_aio.configure(base_url=LLM_HANDLER_URL, timeout=600.0)

        # MongoDB database (store.py 에서 사용)
        os.environ.setdefault("MONGODB_DATABASE", MONGODB_DATABASE)

        # file_manager SDK URL
        os.environ.setdefault("FILE_HANDLER_URL", FILE_HANDLER_URL)

        _configured = True
