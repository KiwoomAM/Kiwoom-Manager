"""테스트 파일들이 함께 쓰는 가짜 winus-handler 와 호출 도구.

네트워크 경계(httpx 클라이언트의 transport)만 가짜로 두고 공개 함수를 그대로 부르기 위한 것이다.
fixture `handler` 는 conftest.py 가 이것으로 만든다.
"""

import asyncio
import json

import httpx
import pytest

import winus_manager
import winus_manager.aio as winus_aio

_SQL = "SELECT 1 FROM DUAL WHERE 1 = :x"
_DML = "UPDATE t SET a = :a"

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
    """winus-handler 흉내. 받은 요청을 모으고, 정해 둔 응답을 돌려준다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: dict[str, dict] = {path: body for _, path, _, body in _CALLS}
        self.failure: httpx.Response | None = None
        self.transport_error: type[httpx.TransportError] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.transport_error is not None:
            raise self.transport_error("시간 초과", request=request)
        if self.failure is not None:
            return self.failure
        return httpx.Response(200, json=self.responses[request.url.path])

    def fail_in_transport(self, error: type[httpx.TransportError]) -> None:
        """응답 대신 전송 단계의 실패를 낸다."""
        self.transport_error = error

    def fail_with(self, status_code: int, body: dict) -> None:
        self.failure = httpx.Response(status_code, json=body)

    @property
    def last_body(self) -> dict:
        return json.loads(self.requests[-1].content)


def _reset_clients() -> None:
    winus_manager.close()
    # async close 는 이벤트 루프가 필요하다 — 가짜 transport 라 버려도 남는 자원이 없다.
    winus_aio._instance = None


def _sync(name: str, *args, **kwargs):
    return getattr(winus_manager, name)(*args, **kwargs)


def _async(name: str, *args, **kwargs):
    return asyncio.run(getattr(winus_aio, name)(*args, **kwargs))


_SURFACES = pytest.mark.parametrize("call", [_sync, _async], ids=["sync", "aio"])
