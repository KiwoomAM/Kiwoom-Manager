# -*- coding: utf-8 -*-
"""`vdb-handler`가 내는 오류 본문에서 무엇을 읽는가.

`vdb-handler`는 자체 예외든 요청 검증 실패든 `{"error": "..."}` 하나로 답한다. 위치가
필요한 검증 실패는 `vdb-handler`가 `body.ids.0: ...` 꼴로 앞에 붙이고 여러 건은 ` / ` 로
이어 보내므로, SDK 가 다시 조립할 것이 없다.

한때는 검증 실패가 FastAPI 기본인 `{"detail": [...]}` 로 나가 SDK 가 그것을
평탄화했다. `vdb-handler`가 한 모양으로 정규화하면서(`KiwoomAM/Kiwoom-Handler#71`) 그 코드는
닿지 않게 되어 걷어냈다 — 닿지 않는 코드를 남겨 두면 다음 사람이 그것을 `vdb-handler` 의 계약으로
읽는다. 배포본이 하나뿐이라는 것은 확인했다.
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


def test_여러_건이_이어진_메시지도_그대로_쓴다(monkeypatch):
    """위치를 붙이고 이어 붙이는 일은 `vdb-handler`가 한다. SDK 는 다시 조립하지 않는다."""
    response = httpx.Response(
        422,
        json={"error": "body.ids.0: UUID 아님: 'nope' / body.limit: 1 이상이어야 함"},
    )
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.post("/v1/collections/c/upsert_points", json={})

    assert exc.value.message == "body.ids.0: UUID 아님: 'nope' / body.limit: 1 이상이어야 함"


def test_봉투가_없는_본문은_조립하지_않고_그대로_싣는다(monkeypatch):
    """되돌리기로 옛 이미지가 잠깐 뜨면 `{"detail": [...]}` 가 올 수 있다.

    그때 메시지는 날것의 JSON 이 되지만 예외 갈래와 상태 코드는 그대로다. 못생겨질
    뿐 깨지지 않는다 — 그 퇴화를 감수하는 대신 닿지 않는 평탄화 코드를 없앴다.
    """
    response = httpx.Response(422, json={"detail": [{"loc": ["body"], "msg": "무엇인가"}]})
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.post("/v1/collections/c/upsert_points", json={})

    assert exc.value.status_code == 422
    assert "무엇인가" in exc.value.message


def test_non_json_body_falls_back_to_text(monkeypatch):
    response = httpx.Response(500, text="Internal Server Error")
    client = _client_returning(response, monkeypatch)

    with pytest.raises(VDBHandlerError) as exc:
        client.get("/v1/admin/status")

    assert exc.value.message == "Internal Server Error"
