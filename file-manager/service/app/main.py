"""file_manager Service FastAPI 앱 (시연용 stub).

실행:
    cd packages/file-manager/service
    uvicorn app.main:app --port 8102 --reload
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.routers import files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("file_manager")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 MDB handler 초기화."""
    os.environ.setdefault("MDB_HANDLER_URL", settings.mdb_handler_url)
    from mdb_handler import MongoDBHandler
    MongoDBHandler.reset()
    logger.info("file_manager service 시작 (db=%s, col=%s)", settings.mongodb_database, settings.mongodb_collection)
    yield
    logger.info("file_manager service 종료")


app = FastAPI(
    title="File Manager Service (stub)",
    description="파일 캐시 기반 저장/조회 서비스 (시연용)",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(files.router, tags=["files"])


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
