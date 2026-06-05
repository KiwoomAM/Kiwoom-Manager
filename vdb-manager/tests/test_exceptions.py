# -*- coding: utf-8 -*-
"""VDBError hierarchy exposes message/status_code uniformly."""

from vdb_manager.exceptions import (
    VDBConnectionError,
    VDBError,
    VDBHandlerError,
)


def test_base_error_has_message_and_default_status_code():
    err = VDBError("boom")
    assert err.message == "boom"
    assert err.status_code == 0


def test_handler_error_carries_status_code():
    err = VDBHandlerError("not found", status_code=404)
    assert err.status_code == 404
    assert err.message == "not found"


def test_connection_error_is_vdb_error_with_message():
    err = VDBConnectionError("연결 실패")
    assert isinstance(err, VDBError)
    assert err.message == "연결 실패"
    assert err.status_code == 0
