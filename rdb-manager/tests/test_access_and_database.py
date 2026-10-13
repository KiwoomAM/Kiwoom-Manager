"""핸들러의 접근 등급을 정하는 키(`auth_key`)와 요청이 고르는 database 의 계약.

말의 뜻은 레포 루트 CONTEXT.md 「auth_key」를 따른다. SDK 를 거친 호출은 모두 admin 이어야 하므로,
아무것도 설정하지 않은 소비자도 admin 키를 싣는다. 네트워크 경계(httpx 클라이언트의 transport)만
가짜로 두고 공개 함수(`rdb_manager.*`, `rdb_manager.aio.*`)를 그대로 부른다.
"""

import asyncio
import functools
import json
import logging
from collections.abc import Iterator

import httpx
import pytest

import rdb_manager
import rdb_manager.aio as rdb_aio
from rdb_manager import HandlerError
from rdb_manager._config import _ADMIN_KEY

_ADMIN = f"Bearer {_ADMIN_KEY}"
_SQL = "SELECT 1 WHERE 1 = %(x)s"
_DML = "UPDATE t SET a = %(a)s"

# 공개 함수 이름, 핸들러 경로, 위치 인자, 성공 응답 본문(핸들러 응답 모델의 모양).
_CALLS = [
    (
        "query_to_dataframe",
        "/v1/query/dataframe",
        (_SQL, {"x": 1}),
        {"columns": [], "rows": [], "dtypes": {}, "request_id": "r1"},
    ),
    ("fetch_all", "/v1/query/all", (_SQL, {"x": 1}), {"rows": [], "request_id": "r1"}),
    (
        "fetch_all_dict",
        "/v1/query/all-dict",
        (_SQL, {"x": 1}),
        {"rows": [], "request_id": "r1"},
    ),
    ("fetch_one", "/v1/query/one", (_SQL, {"x": 1}), {"row": None, "request_id": "r1"}),
    (
        "execute_dml",
        "/v1/dml/execute",
        (_DML, {"a": 1}),
        {"rowcount": 0, "request_id": "r1"},
    ),
    (
        "execute_many_dml",
        "/v1/dml/execute-many",
        (_DML, [{"a": 1}]),
        {"rowcount": 0, "request_id": "r1"},
    ),
    (
        "execute_insert",
        "/v1/dml/insert",
        (_DML, {"a": 1}),
        {"rowcount": 0, "request_id": "r1"},
    ),
    (
        "execute_update",
        "/v1/dml/update",
        (_DML, {"a": 1}),
        {"rowcount": 0, "request_id": "r1"},
    ),
]
_CALL_IDS = [name for name, *_ in _CALLS]


class _FakeHandler:
    """rdb-handler 흉내. 받은 요청을 모으고, 정해 둔 응답을 돌려준다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: dict[str, dict] = {path: body for _, path, _, body in _CALLS}
        self.responses["/v1/check"] = {"connected": True}
        self.failure: httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            return self.failure
        return httpx.Response(200, json=self.responses[request.url.path])

    def fail_with(self, status_code: int, body: dict) -> None:
        self.failure = httpx.Response(status_code, json=body)

    @property
    def last_body(self) -> dict:
        return json.loads(self.requests[-1].content)


def _reset_clients() -> None:
    rdb_manager.close()
    # async close 는 이벤트 루프가 필요하다 — 가짜 transport 라 버려도 남는 자원이 없다.
    rdb_aio._instance = None


@pytest.fixture
def handler(monkeypatch) -> Iterator[_FakeHandler]:
    """SDK 가 만드는 HTTP 클라이언트에 가짜 transport 만 끼운다."""
    fake = _FakeHandler()
    transport = httpx.MockTransport(fake)
    monkeypatch.setattr(
        httpx, "Client", functools.partial(httpx.Client, transport=transport)
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport)
    )
    _reset_clients()
    yield fake
    _reset_clients()


def _sync(name: str, *args, **kwargs):
    return getattr(rdb_manager, name)(*args, **kwargs)


def _async(name: str, *args, **kwargs):
    return asyncio.run(getattr(rdb_aio, name)(*args, **kwargs))


_SURFACES = pytest.mark.parametrize("call", [_sync, _async], ids=["sync", "aio"])


# ── 접근 키 ─────────────────────────────────────────────


@_SURFACES
@pytest.mark.parametrize(
    "name,_path,args,_body",
    [*_CALLS, ("check_database_connection", "/v1/check", (), {})],
    ids=[*_CALL_IDS, "check_database_connection"],
)
def test_sends_admin_key_when_not_configured(handler, call, name, _path, args, _body):
    """설정하지 않으면 admin 키를 싣는다."""
    call(name, *args)

    assert handler.requests[-1].headers["Authorization"] == _ADMIN


@pytest.mark.parametrize("surface", [rdb_manager, rdb_aio], ids=["sync", "aio"])
@pytest.mark.parametrize(
    ("auth_key", "sent"), [("guest", "Bearer guest"), ("", "Bearer "), (None, _ADMIN)]
)
def test_configure_sends_chosen_key_as_is_and_admin_when_not_given(
    handler, surface, auth_key, sent
):
    """configure 로 고른 키를 그대로 싣고, 주지 않으면 admin 이다.

    빈 문자열도 그대로 실어 핸들러가 401 로 거절한다 — 비어 있는 키가 조용히 admin 이 되지 않게."""
    surface.configure(auth_key=auth_key)

    call = _sync if surface is rdb_manager else _async
    call("fetch_all", "SELECT 1")

    assert handler.requests[-1].headers["Authorization"] == sent


# ── database ────────────────────────────────────────────


@_SURFACES
@pytest.mark.parametrize("name,path,args,_body", _CALLS, ids=_CALL_IDS)
def test_chosen_database_is_sent_in_body(handler, call, name, path, args, _body):
    """database 를 고르면 본문에 실린다."""
    call(name, *args, database="kw_sandbox")

    assert handler.requests[-1].url.path == path
    assert handler.last_body["database"] == "kw_sandbox"


@_SURFACES
@pytest.mark.parametrize("name,_path,args,_body", _CALLS, ids=_CALL_IDS)
def test_database_is_omitted_when_not_chosen(handler, call, name, _path, args, _body):
    """database 를 고르지 않으면 본문에 싣지 않는다 — 기본 database 는 핸들러 한 곳이 정한다."""
    call(name, *args)

    assert "database" not in handler.last_body


# ── 접근 거절 ───────────────────────────────────────────

# 핸들러가 접근 등급으로 거절할 때 돌려주는 응답 (Kiwoom-Handler app/core/access.py 의 메시지).
_REJECTIONS = [
    (401, "Authorization 헤더의 접근 키를 알아볼 수 없음 — 등급을 정할 수 없어 거절"),
    (403, "GET /v1/check: guest 등급에서 쓸 수 없는 경로 — 필요하면 AX 팀에 요청"),
]


@_SURFACES
@pytest.mark.parametrize(("status", "message"), _REJECTIONS, ids=["401", "403"])
def test_access_rejection_is_handler_error_with_status_and_message(
    handler, call, status, message
):
    """접근 거절은 상태코드와 핸들러 메시지를 실은 HandlerError 다.

    전용 예외를 두지 않는다 — 소비자는 status_code 로 구분하고, 원인은 메시지가 말한다."""
    handler.fail_with(status, {"error": message})

    with pytest.raises(HandlerError) as caught:
        call("fetch_all", "SELECT 1")

    assert type(caught.value) is HandlerError
    assert caught.value.status_code == status
    assert message in str(caught.value)
    assert len(handler.requests) == 1


@_SURFACES
@pytest.mark.parametrize(("status", "message"), _REJECTIONS, ids=["401", "403"])
def test_connection_check_returns_false_and_logs_access_rejection_as_error(
    handler, call, caplog, status, message
):
    """연결 점검은 접근 거절에 False 를 돌려주고 원인을 ERROR 로 남긴다.

    DB 가 끊긴 것으로 읽히지 않게 한다 — 키가 어긋나도 점검은 False 라 원인이 로그에만 남는다."""
    handler.fail_with(status, {"error": message})

    with caplog.at_level(logging.ERROR, logger="rdb_manager"):
        assert call("check_database_connection") is False

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        str(status) in r.getMessage() and message in r.getMessage() for r in errors
    )
