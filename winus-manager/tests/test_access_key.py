"""핸들러의 접근 등급을 정하는 키(`auth_key`)를 요청마다 싣는 계약.

말의 뜻은 레포 루트 CONTEXT.md 「auth_key」를 따른다. SDK 를 거친 호출은 모두 admin 이어야
하므로, 아무것도 설정하지 않은 소비자도 admin 키를 싣는다. 네트워크 경계만 가짜로 두고 공개
함수를 그대로 부른다 — 테스트 장치는 test_execution_options 의 것을 함께 쓴다(conftest.py).
"""

import logging

import pytest

import winus_manager
import winus_manager.aio as winus_aio
from tests.test_execution_options import _CALL_IDS, _CALLS, _SURFACES, _async, _sync
from winus_manager import HandlerError
from winus_manager._config import _ADMIN_KEY

_ADMIN = f"Bearer {_ADMIN_KEY}"


@_SURFACES
@pytest.mark.parametrize("name,_path,args,_body", _CALLS, ids=_CALL_IDS)
def test_설정하지_않으면_admin_키를_싣는다(handler, call, name, _path, args, _body):
    call(name, *args)

    assert handler.requests[-1].headers["Authorization"] == _ADMIN


@pytest.mark.parametrize("surface", [winus_manager, winus_aio], ids=["sync", "aio"])
@pytest.mark.parametrize(
    ("auth_key", "sent"), [("guest", "Bearer guest"), ("", _ADMIN), (None, _ADMIN)]
)
def test_configure_로_고른_키를_싣고_비우면_admin_이다(
    handler, surface, auth_key, sent
):
    surface.configure(auth_key=auth_key)

    call = _sync if surface is winus_manager else _async
    call("fetch_all", "SELECT 1 FROM DUAL")

    assert handler.requests[-1].headers["Authorization"] == sent


# 핸들러가 접근 등급으로 거절할 때 돌려주는 응답 (Kiwoom-Handler app/core/access.py 의 메시지).
_REJECTIONS = [
    (401, "Authorization 헤더의 접근 키를 알아볼 수 없음 — 등급을 정할 수 없어 거절"),
    (403, "POST /v1/query/all: guest 등급에서 쓸 수 없는 경로 — 필요하면 AX 팀에 요청"),
]


@_SURFACES
@pytest.mark.parametrize(("status", "message"), _REJECTIONS, ids=["401", "403"])
def test_접근_거절은_상태코드와_핸들러_메시지를_실은_HandlerError_다(
    handler, call, status, message
):
    """전용 예외를 두지 않는다 — 소비자는 status_code 로 구분하고, 원인은 메시지가 말한다."""
    handler.fail_with(status, {"error": message})

    with pytest.raises(HandlerError) as caught:
        call("fetch_all", "SELECT 1 FROM DUAL")

    assert type(caught.value) is HandlerError
    assert caught.value.status_code == status
    assert message in str(caught.value)
    assert len(handler.requests) == 1


@_SURFACES
@pytest.mark.parametrize(("status", "message"), _REJECTIONS, ids=["401", "403"])
def test_연결_점검은_접근_거절에_False_를_돌려주고_원인을_ERROR_로_남긴다(
    handler, call, caplog, status, message
):
    """DB 가 끊긴 것으로 읽히지 않게 한다 — 키가 어긋나도 점검은 False 라 원인이 로그에만 남는다."""
    handler.fail_with(status, {"error": message})

    with caplog.at_level(logging.ERROR, logger="winus_manager"):
        assert call("check_database_connection") is False

    assert str(status) in caplog.text
    assert message in caplog.text
