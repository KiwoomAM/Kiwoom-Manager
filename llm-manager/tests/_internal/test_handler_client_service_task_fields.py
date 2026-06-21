"""handler_client: service_name·task_name 둘 다 실 필드로 body 에 실린다.

- task_name = 호출(task) 단위 라벨 (llm_input 입력)
- service_name = 실행 서비스 식별자 (env SERVICE_NAME, 호출 측에서 주입)

둘은 병합되지 않고 각각 별도 필드로 handler body 에 전달된다 (handler 가 둘 다 수용).
"""

from __future__ import annotations

import inspect

import pytest

from llm_manager._internal.handler_client import (
    call as sync_call,
    embed as sync_embed,
    stream as sync_stream,
)
from llm_manager._internal.handler_client import aio
from llm_manager._internal.handler_client._common import build_body, build_embed_body


class TestBuildBodyBothFields:

    def test_both_present(self):
        body = build_body("hi", model="m", task_name="summary", service_name="kiwoom-rm")
        assert body["task_name"] == "summary"
        assert body["service_name"] == "kiwoom-rm"

    def test_only_task_name(self):
        body = build_body("hi", model="m", task_name="summary")
        assert body["task_name"] == "summary"
        assert "service_name" not in body

    def test_only_service_name(self):
        body = build_body("hi", model="m", service_name="kiwoom-rm")
        assert body["service_name"] == "kiwoom-rm"
        assert "task_name" not in body

    def test_neither(self):
        body = build_body("hi", model="m")
        assert "task_name" not in body
        assert "service_name" not in body


class TestEmbedBodyBothFields:

    def test_both(self):
        body = build_embed_body("hi", model="m", task_name="t", service_name="s")
        assert body["task_name"] == "t"
        assert body["service_name"] == "s"


class TestPublicSignaturesHaveBoth:
    """공개 진입 함수가 두 인자를 모두 받아야 runner 의 service_name 주입이 전달된다."""

    @pytest.mark.parametrize(
        "fn", [aio.call, aio.stream, aio.embed, sync_call, sync_stream, sync_embed]
    )
    def test_has_both_params(self, fn):
        params = inspect.signature(fn).parameters
        assert "task_name" in params
        assert "service_name" in params
