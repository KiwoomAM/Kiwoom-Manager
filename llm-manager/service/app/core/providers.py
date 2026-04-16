"""외부 서비스 연결 관리."""

import os

from app.config import settings


def configure_handlers() -> None:
    """MDB/Redis/LLM handler SDK를 서비스 설정으로 초기화.

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

    # file_manager SDK URL 설정
    os.environ.setdefault("FILE_MANAGER_URL", settings.file_manager_url)


async def shutdown_handlers() -> None:
    """FastAPI lifespan shutdown에서 호출. 리소스 정리."""
    pass
