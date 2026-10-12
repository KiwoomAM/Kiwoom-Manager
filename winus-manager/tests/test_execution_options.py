"""조회마다 붙이는 실행 옵션(이름·마감)과 핸들러가 돌려주는 실패의 계약.

용어의 뜻은 winus-manager/CONTEXT.md 를 따른다. 네트워크 경계만 가짜로 두고 공개
함수(`winus_manager.*`, `winus_manager.aio.*`)를 그대로 부른다.
"""

import httpx
import pytest

import winus_manager
import winus_manager.aio as winus_aio
from tests._fakes import _CALL_IDS, _CALLS, _SQL, _SURFACES
from winus_manager import (
    ConsumerTimeoutError,
    HandlerConnectionError,
    HandlerError,
    QueryCancelledError,
    SDKError,
)


@_SURFACES
@pytest.mark.parametrize("name,path,args,_body", _CALLS, ids=_CALL_IDS)
def test_실행_옵션을_넘기면_본문에_실린다(handler, call, name, path, args, _body):
    call(name, *args, client_query_id="pf-nav-batch-20260930", query_timeout_ms=3_000)

    assert handler.requests[-1].url.path == path
    assert handler.last_body["client_query_id"] == "pf-nav-batch-20260930"
    assert handler.last_body["query_timeout_ms"] == 3_000


@_SURFACES
@pytest.mark.parametrize("name,path,args,_body", _CALLS, ids=_CALL_IDS)
def test_실행_옵션을_안_넘기면_본문에_싣지_않는다(
    handler, call, name, path, args, _body
):
    """옵션을 쓰지 않는 호출의 본문은 이전과 같고, 빠진 값은 서버가 정한다."""
    call(name, *args)

    assert "client_query_id" not in handler.last_body
    assert "query_timeout_ms" not in handler.last_body


@_SURFACES
def test_409_는_QueryCancelledError_로_올라온다(handler, call):
    """운영자나 마감, 또는 원본이 의도적으로 끊은 조회다. 재시도하면 끊은 쪽이 멈춘 조회가
    되살아난다."""
    handler.fail_with(
        409,
        {
            "error": "조회가 취소됨",
            "detail": "운영자가 취소함 (ORA-01013)",
            "request_id": "r-409",
        },
    )

    with pytest.raises(QueryCancelledError) as caught:
        call("fetch_all", _SQL, {"x": 1})

    assert caught.value.status_code == 409
    assert caught.value.detail == "운영자가 취소함 (ORA-01013)"
    assert caught.value.request_id == "r-409"


def test_QueryCancelledError_는_기존_except_HandlerError_에도_잡힌다(handler):
    handler.fail_with(409, {"error": "조회가 취소됨", "request_id": "r-409"})

    with pytest.raises(HandlerError):
        winus_manager.fetch_all(_SQL, {"x": 1})


@_SURFACES
def test_다른_실패는_HandlerError_로_올라오고_request_id_를_싣는다(handler, call):
    """핸들러 로그와 관리 목록에서 그 요청을 찾는 손잡이다."""
    handler.fail_with(
        400, {"error": "쿼리 실행 실패", "detail": "ORA-00942", "request_id": "r-400"}
    )

    with pytest.raises(HandlerError) as caught:
        call("fetch_all", _SQL, {"x": 1})

    assert not isinstance(caught.value, QueryCancelledError)
    assert caught.value.status_code == 400
    assert caught.value.request_id == "r-400"


@_SURFACES
def test_마감이_소비자_타임아웃_이상이면_보내지_않는다(handler, call):
    """소비자가 먼저 포기하면 그 뒤로 조회가 주인 없이 원장에서 돈다. 같아도 막는다 —
    서버의 시계는 자리를 받은 뒤에 가므로, 같으면 서버가 소비자보다 늦게 끊는다."""
    winus_manager.configure(timeout=10.0)
    winus_aio.configure(timeout=10.0)

    with pytest.raises(ValueError, match="query_timeout_ms=10000"):
        call("fetch_all", _SQL, {"x": 1}, query_timeout_ms=10_000)

    assert handler.requests == []


@_SURFACES
def test_마감이_소비자_타임아웃보다_짧으면_보낸다(handler, call):
    winus_manager.configure(timeout=10.0)
    winus_aio.configure(timeout=10.0)

    call("fetch_all", _SQL, {"x": 1}, query_timeout_ms=9_999)

    assert handler.last_body["query_timeout_ms"] == 9_999


@_SURFACES
@pytest.mark.parametrize(
    "timeout", [httpx.ReadTimeout, httpx.WriteTimeout], ids=["read", "write"]
)
def test_응답을_기다리다_그만두면_ConsumerTimeoutError_로_올라온다(handler, call, timeout):
    """요청은 핸들러에 닿았으므로 조회가 원장에서 서버 마감까지 돌 수 있다. 운영자가 그
    이름으로 끊을 수 있게 싣는다."""
    handler.fail_in_transport(timeout)

    with pytest.raises(ConsumerTimeoutError) as caught:
        call("fetch_all", _SQL, {"x": 1}, client_query_id="pf-nav-batch-20260930")

    assert caught.value.client_query_id == "pf-nav-batch-20260930"


def test_ConsumerTimeoutError_는_기존_except_httpx_TimeoutException_에도_잡힌다(handler):
    """이 예외가 생기기 전 소비자는 날것의 httpx 타임아웃을 잡아 재시도했다. 그 코드를
    깨지 않는다."""
    handler.fail_in_transport(httpx.ReadTimeout)

    with pytest.raises(httpx.TimeoutException) as caught:
        winus_manager.fetch_all(_SQL, {"x": 1})

    assert isinstance(caught.value, SDKError)
    # 옛 except 블록이 읽던 속성도 그대로 있어야 한다.
    assert caught.value.request.url.path == "/v1/query/all"


@_SURFACES
@pytest.mark.parametrize(
    "timeout", [httpx.ConnectTimeout, httpx.PoolTimeout], ids=["connect", "pool"]
)
def test_핸들러에_닿지_못한_타임아웃은_HandlerConnectionError_로_올라온다(
    handler, call, timeout
):
    """요청이 나가지 않았으므로 조회는 돌지 않았다. 연결 실패와 같은 뜻이다."""
    handler.fail_in_transport(timeout)

    with pytest.raises(HandlerConnectionError):
        call("fetch_all", _SQL, {"x": 1})


@_SURFACES
def test_그_밖의_전송_실패는_감싸지_않는다(handler, call):
    """응답 도중 끊긴 연결 같은 실패는 소비자(dbgateway)가 httpx.TransportError 로 따로
    잡는다. 감싸면 그 분기가 깨진다."""
    handler.fail_in_transport(httpx.ReadError)

    with pytest.raises(httpx.ReadError) as caught:
        call("fetch_all", _SQL, {"x": 1})

    assert not isinstance(caught.value, SDKError)
