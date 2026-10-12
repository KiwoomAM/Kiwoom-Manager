# -*- coding: utf-8 -*-
"""핸들러의 접근 등급을 정하는 키(`auth_key`)를 요청마다 싣는 계약.

말의 뜻은 레포 루트 CONTEXT.md 「auth_key」를 따른다. SDK 를 거친 호출은 모두 admin 이어야 하므로,
아무것도 설정하지 않은 소비자도 admin 키를 싣는다. file-manager·llm-manager 처럼 안에서 mdb-manager 를
쓰는 SDK 도 auth_key 없이 만들고 with_caller 로 부르므로, 그 경로도 같이 본다. 네트워크 경계(httpx
클라이언트의 transport)만 가짜로 두고 공개 클래스를 그대로 부른다.
"""

import asyncio
import functools
from collections.abc import Iterator

import httpx
import pytest

from mdb_manager import MongoDBManager
from mdb_manager._config import _ADMIN_KEY
from mdb_manager.exceptions import MDBHandlerError
from mdb_manager.aio import AsyncMongoDBManager

_ADMIN = f"Bearer {_ADMIN_KEY}"


class _FakeHandler:
    """mdb-handler 흉내. 받은 요청을 모으고, 어느 경로든 비어 있는 성공 응답을 돌려준다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.failure: httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            return self.failure
        return httpx.Response(200, json={"collections": [], "data": None})

    def fail_with(self, status_code: int, body: dict) -> None:
        self.failure = httpx.Response(status_code, json=body)


@pytest.fixture
def handler(monkeypatch) -> Iterator[_FakeHandler]:
    """SDK 가 만드는 HTTP 클라이언트에 가짜 transport 만 끼운다."""
    fake = _FakeHandler()
    transport = httpx.MockTransport(fake)
    monkeypatch.setattr(httpx, "Client", functools.partial(httpx.Client, transport=transport))
    monkeypatch.setattr(httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport))
    MongoDBManager.reset()
    yield fake
    MongoDBManager.reset()


async def _async_calls(manager: AsyncMongoDBManager) -> None:
    async with manager:
        await manager.list_collections()
        await manager.with_caller("file-manager").get_collection("file_cache").find_one({})


def test_sync_manager_sends_admin_key_when_not_configured(handler):
    """설정하지 않으면 admin 키를 싣는다 — with_caller 로 호출자를 덮어쓴 요청도 같다."""
    manager = MongoDBManager()
    manager.list_collections()
    manager.with_caller("llm-manager").get_database("kw_ai", "x").find_one({})

    assert [r.headers["Authorization"] for r in handler.requests] == [_ADMIN, _ADMIN]
    assert handler.requests[-1].headers["X-Caller-Service"] == "llm-manager"


def test_async_manager_sends_admin_key_when_not_configured(handler):
    """설정하지 않으면 admin 키를 싣는다 — with_caller 로 호출자를 덮어쓴 요청도 같다."""
    asyncio.run(_async_calls(AsyncMongoDBManager()))

    assert [r.headers["Authorization"] for r in handler.requests] == [_ADMIN, _ADMIN]
    assert handler.requests[-1].headers["X-Caller-Service"] == "file-manager"


@pytest.mark.parametrize(("auth_key", "sent"), [("guest", "Bearer guest"), ("", _ADMIN), (None, _ADMIN)])
def test_sync_manager_sends_chosen_key_and_admin_when_empty(handler, auth_key, sent):
    """생성자로 고른 키를 싣고, 비우면 admin 이다."""
    MongoDBManager(auth_key=auth_key).list_collections()

    assert handler.requests[-1].headers["Authorization"] == sent


@pytest.mark.parametrize(("auth_key", "sent"), [("guest", "Bearer guest"), ("", _ADMIN), (None, _ADMIN)])
def test_async_manager_sends_chosen_key_and_admin_when_empty(handler, auth_key, sent):
    """생성자로 고른 키를 싣고, 비우면 admin 이다."""
    asyncio.run(_async_calls(AsyncMongoDBManager(auth_key=auth_key)))

    assert handler.requests[-1].headers["Authorization"] == sent


# 핸들러가 접근 등급으로 거절할 때 돌려주는 응답 (Kiwoom-Handler app/core/access.py 의 메시지).
_REJECTIONS = [
    (401, "Authorization 헤더의 접근 키를 알아볼 수 없음 — 등급을 정할 수 없어 거절"),
    (403, "GET /v1/admin/collections: guest 등급에서 쓸 수 없는 경로 — 필요하면 AX 팀에 요청"),
]


@pytest.mark.parametrize(("status", "message"), _REJECTIONS, ids=["401", "403"])
def test_access_rejection_is_handler_error_with_status_and_message(handler, status, message):
    """접근 거절은 상태코드와 핸들러 메시지를 실은 MDBHandlerError 다.

    전용 예외를 두지 않는다 — 소비자는 status_code 로 구분하고, 원인은 메시지가 말한다."""
    handler.fail_with(status, {"error": message})

    with pytest.raises(MDBHandlerError) as caught:
        MongoDBManager().list_collections()

    assert type(caught.value) is MDBHandlerError
    assert caught.value.status_code == status
    assert message in str(caught.value)
    assert len(handler.requests) == 1
