"""llm-request Service FastAPI 앱."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import LLMRequestServiceError
from app.core.providers import configure_handlers
from app.models.responses import ErrorResponse
from app.routers import request


# 로그 설정. "서버에서 일어나는 일을 어떻게 기록할지" — 시간, 레벨, 이름 형식 지정.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("llm_request")


# 서버 시작/종료 시 실행할 것. 
# yield 전 = 시작 시, yield 후 = 종료 시. 
# configure_handlers()가 MDB/Redis/LLM 연결을 여기서 초기화
@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 handler SDK 초기화."""
    configure_handlers()
    logger.info("llm-request service 시작")
    yield
    logger.info("llm-request service 종료")


app = FastAPI(
    title="LLM Request Service",
    description="LLM 응답 캐싱 + 파일 처리 서비스",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(request.router, tags=["request"])


@app.exception_handler(LLMRequestServiceError)
async def handle_service_error(_request: Request, exc: LLMRequestServiceError) -> JSONResponse:
    """LLMRequestServiceError를 표준 ErrorResponse로 변환."""
    request_id = getattr(_request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.message,
            error_type=exc.error_type,
            request_id=request_id,
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI 요청 검증 에러를 표준 ErrorResponse로 변환."""
    request_id = getattr(_request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="요청 검증 실패",
            detail=str(exc),
            error_type="validation",
            request_id=request_id,
        ).model_dump(),
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """요청별 request_id 생성 + 로깅."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start_time = time.monotonic()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("미처리 예외: request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="내부 서버 오류",
                error_type="handler",
                request_id=request_id,
            ).model_dump(),
        )

    latency_ms = (time.monotonic() - start_time) * 1000
    logger.info(
        "%s %s → %s (%.0fms) [%s]",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
        request_id,
    )
    return response


@app.get("/health")
async def health_check() -> dict[str, str]:
    """헬스 체크."""
    return {"status": "ok"}
