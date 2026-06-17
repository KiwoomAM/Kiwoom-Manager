"""handler_client: 구 ``service_name`` 인자 deprecated 별칭 동작.

호출 라벨이 service_name → task_name 으로 rename됐다. 마이그레이션 기간 동안 옛 인자도
받아주되(둘 다 수용) 경고를 남기고 task_name 으로 흡수한다. 안 그러면 옛 호출자가
`aio_call(**llm_input)` 펼치기에서 TypeError 로 깨진다.
"""

from __future__ import annotations

import inspect
import logging

import pytest

from llm_manager._internal.handler_client import (
    call as sync_call,
    embed as sync_embed,
    stream as sync_stream,
)
from llm_manager._internal.handler_client import aio
from llm_manager._internal.handler_client._common import _resolve_task_name, build_body


class TestResolveTaskName:

    def test_none(self):
        assert _resolve_task_name(None, None) is None

    def test_task_name_only(self):
        assert _resolve_task_name("t", None) == "t"

    def test_service_name_absorbed(self):
        assert _resolve_task_name(None, "s") == "s"

    def test_both_prefers_task_name(self):
        assert _resolve_task_name("t", "s") == "t"

    def test_warns_on_service_name(self, caplog):
        with caplog.at_level(logging.WARNING):
            _resolve_task_name(None, "kiwoom-rm")
        assert any("deprecated" in r.message for r in caplog.records)


class TestPublicSignatureAcceptsServiceName:
    """공개 진입 함수가 service_name 을 받아야 TypeError 없이 흡수된다."""

    @pytest.mark.parametrize(
        "fn", [aio.call, aio.stream, aio.embed, sync_call, sync_stream, sync_embed]
    )
    def test_has_deprecated_param(self, fn):
        assert "service_name" in inspect.signature(fn).parameters


def test_build_body_label_uses_task_name():
    body = build_body(
        "hi", model="gemini-2.5-flash", task_name=_resolve_task_name(None, "kiwoom-rm")
    )
    assert body["task_name"] == "kiwoom-rm"
    assert "service_name" not in body   # wire 에는 task_name 으로만 실림
