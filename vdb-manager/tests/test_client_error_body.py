# -*- coding: utf-8 -*-
"""서버가 내는 422 의 본문 모양이 둘이라 한쪽만 읽으면 메시지가 깨진다.

- 핸들러 자체 예외(`InvalidPayloadFilterError` 등) → `{"error": "..."}`
- FastAPI 의 요청 검증 실패(extra 금지·id 형식·길이 불일치) → `{"detail": [...]}`

둘째를 못 읽으면 소비자가 보는 예외 메시지가 날것의 JSON 덩어리가 된다.
"""

from __future__ import annotations

import httpx
import pytest

from vdb_manager._client import SyncClient
from vdb_manager.exceptions import VDBHandlerError


def _client_returning(response: httpx.Response, monkeypatch) -> SyncClient:
    client = SyncClient()
    monkeypatch.setattr(client._http, "request", lambda *a, **kw: response)
    return client


def test_error_envelope_is_read(monkeypatch):
    response = httpx.Response(422, json={"error": "지원하지 않는 필터 절: ['filter']"})
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.post("/v1/collections/c/count", json={})

    assert exc.value.status_code == 422
    assert exc.value.message == "지원하지 않는 필터 절: ['filter']"


def test_validation_detail_is_flattened(monkeypatch):
    response = httpx.Response(
        422,
        json={
            "detail": [
                {
                    "type": "value_error",
                    "loc": ["body", "ids", 0],
                    "msg": "Value error, ids[0] — UUID 아님: 'nope'",
                    "input": "nope",
                }
            ]
        },
    )
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.post("/v1/collections/c/upsert_points", json={})

    assert exc.value.status_code == 422
    assert "UUID 아님" in exc.value.message
    assert "body.ids.0" in exc.value.message
    assert '"type"' not in exc.value.message, "날것의 JSON 이 그대로 새면 안 된다"


def test_multiple_validation_errors_are_all_reported(monkeypatch):
    response = httpx.Response(
        422,
        json={
            "detail": [
                {"loc": ["body", "ids"], "msg": "길이 불일치"},
                {"loc": ["body", "start_id"], "msg": "Extra inputs are not permitted"},
            ]
        },
    )
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.post("/v1/collections/c/upsert_points", json={})

    assert "길이 불일치" in exc.value.message
    assert "Extra inputs are not permitted" in exc.value.message


def test_non_json_body_falls_back_to_text(monkeypatch):
    response = httpx.Response(500, text="Internal Server Error")
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.get("/v1/admin/status")

    assert exc.value.message == "Internal Server Error"
