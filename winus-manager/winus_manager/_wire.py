"""동기·비동기 클라이언트가 함께 쓰는 와이어 규칙.

실행 옵션, 요청 본문 조립, 실패 응답을 예외로 바꾸는 법을 한 곳에 둔다. 두 클라이언트가
각자 들고 있으면 한쪽만 고쳐지는 날 `winus_manager` 와 `winus_manager.aio` 가 다르게
동작한다.
"""

from dataclasses import asdict, dataclass
from typing import Any

import httpx

from winus_manager.exceptions import (
    ConsumerTimeoutError,
    HandlerConnectionError,
    HandlerError,
    QueryCancelledError,
    SDKError,
)

# 요청이 핸들러에 닿기 전에 난 타임아웃. httpx 에서는 이름과 달리 ConnectError 가 아니라
# TimeoutException 의 하위라, 타임아웃이라는 이유만으로 묶으면 돌지 않은 조회를 돈 것으로 본다.
_NOT_SENT_TIMEOUTS = (httpx.ConnectTimeout, httpx.PoolTimeout)

# 상태코드마다 따로 올릴 예외. 여기 없는 실패는 HandlerError 로 올린다.
_ERROR_BY_STATUS: dict[int, type[HandlerError]] = {409: QueryCancelledError}


@dataclass(frozen=True)
class ExecutionOptions:
    """조회마다 붙이는 실행 옵션. 핸들러의 `ExecutionOptions` 와 같은 필드를 같은 이름으로 둔다.

    필드 이름이 곧 와이어 이름이다. 옵션이 새로 생기면 필드 하나를 더하면 본문에도 실린다.
    뜻은 winus-manager/CONTEXT.md 를 따른다.

    Attributes:
        client_query_id: 소비자가 지은 이 조회의 이름.
        query_timeout_ms: 이 조회의 요청 마감(밀리초).
    """

    client_query_id: str | None = None
    query_timeout_ms: int | None = None

    def check_within(self, consumer_timeout_s: float | None) -> None:
        """요청 마감이 소비자 타임아웃 안에 드는지 확인한다.

        Args:
            consumer_timeout_s: 이 클라이언트의 소비자 타임아웃(초). `None` 이면 기다림에
                끝이 없다.

        Raises:
            ValueError: 마감이 소비자 타임아웃 이상일 때. 서버의 시계는 자리를 받은 뒤에
                가므로 둘이 같아도 서버가 소비자보다 늦게 끊는다.
        """
        if (
            self.query_timeout_ms is not None
            and consumer_timeout_s is not None
            and self.query_timeout_ms >= consumer_timeout_s * 1000
        ):
            raise ValueError(
                f"query_timeout_ms={self.query_timeout_ms} 가 소비자 타임아웃 "
                f"{consumer_timeout_s}초 이상 — SDK 가 먼저 포기해 조회가 주인 없이 원장에서 돌게 됨"
            )

    def as_body(self) -> dict[str, Any]:
        """요청 본문에 합칠 모양을 만든다.

        넘기지 않은 옵션은 뺀다. 그러면 옵션을 쓰지 않는 호출의 본문이 이전과 같고, 빠진
        값은 서버가 정한다.

        Returns:
            필드 이름을 와이어 이름으로 쓰는 옵션. 넘긴 것만 담는다.
        """
        return {name: value for name, value in asdict(self).items() if value is not None}


def query_body(
    query: str, params: dict[str, Any] | None, options: ExecutionOptions
) -> dict[str, Any]:
    """SELECT 요청 본문."""
    body: dict[str, Any] = {"query": query, **options.as_body()}
    if params is not None:
        body["params"] = params
    return body


def dml_body(
    sql: str, params: dict[str, Any] | None, options: ExecutionOptions
) -> dict[str, Any]:
    """DML 요청 본문."""
    body: dict[str, Any] = {"sql": sql, **options.as_body()}
    if params is not None:
        body["params"] = params
    return body


def dml_many_body(
    sql: str, params_list: list[dict[str, Any]], options: ExecutionOptions
) -> dict[str, Any]:
    """배치 DML 요청 본문."""
    return {"sql": sql, "params_list": params_list, **options.as_body()}


def error_from_response(response: httpx.Response) -> HandlerError:
    """핸들러의 실패 응답을 예외로 바꾼다.

    Args:
        response: 상태코드가 400 이상인 응답.

    Returns:
        상태코드에 맞는 예외. 던지는 것은 호출자다.
    """
    try:
        body = response.json()
        error_msg = body.get("error", response.text)
        detail = body.get("detail")
        request_id = body.get("request_id")
    except Exception:
        error_msg, detail, request_id = response.text, None, None
    error_type = _ERROR_BY_STATUS.get(response.status_code, HandlerError)
    return error_type(
        f"[위너스 핸들러] {error_msg}",
        status_code=response.status_code,
        detail=detail,
        request_id=request_id,
    )


def error_from_transport(
    error: httpx.ConnectError | httpx.TimeoutException, options: ExecutionOptions
) -> SDKError:
    """전송 단계의 실패를 예외로 바꾼다.

    조회가 원장에서 돌았을 수 있는지로 구분한다. 핸들러에 닿지 못했으면 연결 실패이고, 닿은
    뒤 응답을 기다리다 그만뒀으면 소비자 타임아웃이다.

    Args:
        error: `httpx` 가 낸 전송 실패.
        options: 그 요청에 붙인 실행 옵션. 소비자 타임아웃에 조회 이름을 싣는 데 쓴다.

    Returns:
        조회가 돌았을 수 있는지에 맞는 예외. 던지는 것은 호출자다.
    """
    if isinstance(error, httpx.ConnectError):
        return HandlerConnectionError(f"[위너스 핸들러] 연결 실패: {error}")
    if isinstance(error, _NOT_SENT_TIMEOUTS):
        return HandlerConnectionError(f"[위너스 핸들러] 연결 시간 초과: {error}")
    return ConsumerTimeoutError(
        f"[위너스 핸들러] 응답 대기 시간 초과: {error}",
        client_query_id=options.client_query_id,
        request=error.request,
    )
