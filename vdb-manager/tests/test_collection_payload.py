# -*- coding: utf-8 -*-
"""포인트는 그대로 두고 payload 만 고치는 네 연산.

서버(KiwoomAM/Kiwoom-Handler#73)가 연 경로에 대응한다. 경로와 body 를 캡처해
검증하는 이 패키지의 관례를 따르되, 호출된 HTTP 메서드까지 본다 — 병합과 전체
교체는 경로와 body 모양이 같고 메서드만 다르기 때문이다.
"""

from __future__ import annotations

import pytest

COLLECTION = "fnguide_reports_hybrid"
UUID_A = "3f2b0c4e-1d5a-4c9b-8e7f-2a6b4c8d0e1f"


# ------------------------------------------------------------------
# 어느 경로로 어떤 메서드가 가는가
# ------------------------------------------------------------------


def test_set_payload_merges_with_patch(make_collection):
    col, client = make_collection({"status": "updated", "count": 2})

    col.set_payload(COLLECTION, ids=[1, UUID_A], payload={"file_path": "x"})

    call = client.calls[0]
    assert call["method"] == "PATCH"
    assert call["path"] == f"/v1/collections/{COLLECTION}/points/payload"
    assert call["json"] == {"ids": [1, UUID_A], "payload": {"file_path": "x"}}


def test_overwrite_payload_replaces_with_put(make_collection):
    col, client = make_collection({"status": "replaced", "count": 2})

    col.overwrite_payload(COLLECTION, ids=[1, UUID_A], payload={"file_path": "x"})

    call = client.calls[0]
    assert call["method"] == "PUT"
    assert call["path"] == f"/v1/collections/{COLLECTION}/points/payload"
    assert call["json"] == {"ids": [1, UUID_A], "payload": {"file_path": "x"}}


def test_merge_and_replace_differ_only_by_method(make_collection):
    """둘이 뒤바뀌면 주지 않은 키가 조용히 사라진다.

    경로와 body 가 같으므로 HTTP 메서드가 유일한 구분이다. 여기서 붙잡지 않으면
    병합을 부른 소비자가 전체 교체를 당한다.
    """
    merge, merge_client = make_collection()
    replace, replace_client = make_collection()
    args = {"ids": [1], "payload": {"a": 1}}

    merge.set_payload(COLLECTION, **args)
    replace.overwrite_payload(COLLECTION, **args)

    assert merge_client.calls[0]["path"] == replace_client.calls[0]["path"]
    assert merge_client.calls[0]["json"] == replace_client.calls[0]["json"]
    assert merge_client.calls[0]["method"] == "PATCH"
    assert replace_client.calls[0]["method"] == "PUT"


def test_delete_payload_keys_sends_keys(make_collection):
    col, client = make_collection({"status": "deleted", "keys": ["위치"]})

    col.delete_payload_keys(COLLECTION, ids=[1, 2], keys=["위치"])

    call = client.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == f"/v1/collections/{COLLECTION}/points/payload"
    assert call["json"] == {"ids": [1, 2], "keys": ["위치"]}


def test_clear_payload_uses_its_own_path(make_collection):
    col, client = make_collection({"status": "cleared", "count": 2})

    col.clear_payload(COLLECTION, ids=[1, 2])

    call = client.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == f"/v1/collections/{COLLECTION}/points/payload/all"


def test_clear_payload_never_reaches_the_key_delete_path(make_collection):
    """전부 삭제가 키 삭제와 같은 경로로 가면 안 된다.

    서버가 두 연산을 갈라 둔 이유는 인자를 빠뜨렸을 때 전체가 지워지는 일을 막기
    위해서다. SDK 가 같은 경로로 보내면서 keys 만 빼면 그 경계가 무너진다.
    """
    col, client = make_collection()

    col.clear_payload(COLLECTION, ids=[1])

    call = client.calls[0]
    assert call["path"].endswith("/payload/all")
    assert "keys" not in call["json"]


# ------------------------------------------------------------------
# 빈 것은 SDK 가 막는다 (형식은 서버가 판정한다)
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("set_payload", {"payload": {"a": 1}}),
        ("overwrite_payload", {"payload": {"a": 1}}),
        ("delete_payload_keys", {"keys": ["a"]}),
        ("clear_payload", {}),
    ],
)
def test_empty_ids_rejected_without_calling_server(make_collection, method, kwargs):
    col, client = make_collection()

    with pytest.raises(ValueError, match="ids"):
        getattr(col, method)(COLLECTION, ids=[], **kwargs)

    assert client.calls == []


@pytest.mark.parametrize("method", ["set_payload", "overwrite_payload"])
def test_empty_payload_rejected_without_calling_server(make_collection, method):
    """빈 payload 는 아무것도 바꾸지 않으면서 성공으로 보인다."""
    col, client = make_collection()

    with pytest.raises(ValueError, match="payload"):
        getattr(col, method)(COLLECTION, ids=[1], payload={})

    assert client.calls == []


def test_empty_keys_rejected_without_calling_server(make_collection):
    col, client = make_collection()

    with pytest.raises(ValueError, match="keys"):
        col.delete_payload_keys(COLLECTION, ids=[1], keys=[])

    assert client.calls == []


def test_id_format_is_left_to_the_server(make_collection):
    """형식 판정은 서버 몫이다.

    SDK 가 u64 범위나 UUID 모양을 흉내 내면 서버가 규칙을 넓힐 때마다 정당한
    요청을 막는다. 여기서는 서버가 422 로 거부할 값이라도 그대로 실려 나가는지만
    본다.
    """
    col, client = make_collection()

    col.set_payload(COLLECTION, ids=[-1, "not-a-uuid"], payload={"a": 1})

    assert client.calls[0]["json"]["ids"] == [-1, "not-a-uuid"]


def test_response_is_returned_as_is(make_collection):
    """status 와 count 중 무엇이 쓸모 있는지는 호출자마다 다르다."""
    body = {"status": "updated", "collection": COLLECTION, "count": 3}
    col, _ = make_collection(body)

    assert col.set_payload(COLLECTION, ids=[1, 2, 3], payload={"a": 1}) == body
