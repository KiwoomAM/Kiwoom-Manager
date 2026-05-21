"""llm-request Service FastAPI 앱."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from file_manager import FileManagerHandler
from llm_handler.exceptions import HandlerConnectionError, HandlerError

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
    """앱 시작 시 handler SDK 초기화 + 종료 시 HTTP 클라이언트 close."""
    configure_handlers()
    logger.info("llm-request service 시작")
    yield
    # shutdown — 싱글톤 FileManagerHandler 의 httpx.AsyncClient 정리
    fm = FileManagerHandler()
    await fm.close()
    logger.info("llm-request service 종료")


app = FastAPI(
    title="LLM Request Service",
    description="LLM 응답 캐싱 + 파일 처리 서비스",
    version="0.2.0",
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


@app.exception_handler(HandlerError)
async def relay_handler_error(_request: Request, exc: HandlerError) -> JSONResponse:
    """llm-handler 의 분류 결과를 변형 없이 그대로 caller 에게 relay.

    llm-handler 가 status_code(422/502/504 등) + error_type(6 sub-class:
    validation/content_blocked/transport/provider/handler_ops/handler_internal)
    으로 세분한 에러를, llm-request 가 자기 taxonomy 로 묶거나 평탄화하지 않고
    그대로 통과시킨다. HandlerError base 핸들러 하나가 6 sub-class 전부 catch.
    """
    request_id = getattr(_request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.message,
            detail=exc.detail,  # handler_internal sub-cause slug 등 — 변형 없이 relay
            error_type=exc.error_type,
            request_id=request_id,
        ).model_dump(),
    )


@app.exception_handler(HandlerConnectionError)
async def relay_handler_unreachable(_request: Request, exc: HandlerConnectionError) -> JSONResponse:
    """llm-handler 서비스 자체에 도달 불가 (status_code=0).

    연결 실패 = 재시도 가능한 전송 계층 장애 → caller 가 transport 로 동일 재시도
    분기할 수 있도록 502 `transport` 로 정규화 (모호한 "handler" 대신 retryable 신호).
    HandlerConnectionError 는 HandlerError 가 아니라 SDKError 하위라 위 핸들러로
    잡히지 않으므로 별도 처리.
    """
    request_id = getattr(_request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=502,
        content=ErrorResponse(
            error=exc.message,
            error_type="transport",
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
    except Exception as exc:
        # 미처리 예외 — llm-request 자체 버그 / 한계. relay 되는 handler_internal
        # (=llm-handler 내부)과 구분되도록 request_internal 로 분류, detail 에
        # exception class name 을 실어 원인 종류 식별 (기존엔 "내부 서버 오류" 만
        # 나가 진단 정보가 통째로 버려졌음).
        logger.exception("미처리 예외: request_id=%s %s", request_id, type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="내부 서버 오류",
                detail=type(exc).__name__,
                error_type="request_internal",
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
