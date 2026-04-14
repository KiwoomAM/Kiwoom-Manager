"""외부 서비스 연결 관리."""

import os

import httpx

from app.config import settings

# file_manager httpx 싱글톤 (configure_handlers에서 초기화)
_file_manager_client: httpx.AsyncClient | None = None


def configure_handlers() -> None:
    """MDB/Redis/LLM handler SDK + file_manager 클라이언트를 서비스 설정으로 초기화.

    FastAPI lifespan에서 호출한다.
    """
    # mdb-handler SDK 설정
    os.environ.setdefault("MDB_HANDLER_URL", settings.mdb_handler_url)
    from mdb_handler import MongoDBHandler
    MongoDBHandler.reset()

    # redis-handler SDK 설정
    os.environ.setdefault("REDIS_URL", settings.redis_url)
    from redis_handler import init_redis
    init_redis()

    # llm-handler SDK 설정
    os.environ.setdefault("LLM_HANDLER_URL", settings.llm_handler_url)
    import llm_handler
    import llm_handler.aio
    llm_handler.configure()
    llm_handler.aio.configure()

    # MongoDB database 환경변수 설정 (store.py에서 사용)
    os.environ.setdefault("MONGODB_DATABASE", settings.mongodb_database)

    # file_manager httpx 클라이언트 초기화
    global _file_manager_client
    _file_manager_client = httpx.AsyncClient(
        base_url=settings.file_manager_url,
        timeout=settings.file_manager_timeout,
    )


def get_file_manager_client() -> httpx.AsyncClient:
    """file_manager httpx 클라이언트 반환.

    configure_handlers() 호출 후 사용해야 한다.
    """
    if _file_manager_client is None:
        raise RuntimeError(
            "file_manager client not configured — call configure_handlers() first"
        )
    return _file_manager_client


async def shutdown_handlers() -> None:
    """FastAPI lifespan shutdown에서 호출. 리소스 정리."""
    global _file_manager_client
    if _file_manager_client is not None:
        await _file_manager_client.aclose()
        _file_manager_client = None
