# -*- coding: utf-8 -*-
"""
File Manager Service — FastAPI 진입점
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import FMServiceError
from app.routers import files
from file_handler import FileHandlerError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("File Manager Service 시작")
    yield
    await files.close_mdb_client()
    logger.info("File Manager Service 종료")


app = FastAPI(
    title="File Manager Service",
    description="파일 저장, 캐시 조회, 파일 반환을 담당하는 서비스",
    version="1.0.0",
    lifespan=lifespan,
)

# ─── 라우터 등록 ───────────────────────────────────────────────────────

app.include_router(files.router)


# ─── 전역 예외 핸들러 ──────────────────────────────────────────────────


@app.exception_handler(FMServiceError)
async def fm_service_error_handler(request: Request, exc: FMServiceError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message},
    )


@app.exception_handler(FileHandlerError)
async def file_handler_error_handler(request: Request, exc: FileHandlerError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message},
    )


# ─── 요청 로깅 미들웨어 ───────────────────────────────────────────────


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    start = time.perf_counter()

    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "[%s] %s %s → %d (%.1fms)",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ─── 헬스 체크 ─────────────────────────────────────────────────────────


@app.get("/health")
async def health_check():
    return {"status": "ok"}
