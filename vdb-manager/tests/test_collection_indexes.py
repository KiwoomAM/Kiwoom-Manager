# -*- coding: utf-8 -*-
"""자주 조건으로 쓰는 payload 필드에 index 를 만들고 보고 지우는 세 연산.

서버(KiwoomAM/Kiwoom-Handler#73)가 별도 자원으로 연 `/indexes` 에 대응한다.
Qdrant 에 index 전용 조회 경로가 없어 서버가 컬렉션 정보에서 투영하지만, 그것은
서버가 감싸는 물건의 구현 세부라 SDK 에는 드러나지 않는다.
"""

from __future__ import annotations

import warnings

import pytest

COLLECTION = "fnguide_reports_hybrid"
INDEXES_PATH = f"/v1/collections/{COLLECTION}/indexes"
SLASH_FIELD = "종목/분류명"


# ------------------------------------------------------------------
# 만들기
# ------------------------------------------------------------------


def test_create_payload_index_posts_field_and_schema(make_collection):
    col, client = make_collection({"status": "created", "field": "source_pdf"})

    col.create_payload_index(COLLECTION, field="source_pdf", schema="keyword")

    call = client.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == INDEXES_PATH
    assert call["json"] == {"field": "source_pdf", "schema": "keyword"}


def test_schema_choice_is_left_to_the_server(make_collection):
    """허용 목록을 SDK 가 복제하지 않는다.

    서버가 여덟 종류로 고정하고 그 밖을 422 로 거부한다. SDK 가 같은 목록을 들고
    있으면 서버가 종류를 늘렸을 때 SDK 만 옛 목록으로 막는다.
    """
    col, client = make_collection()

    col.create_payload_index(COLLECTION, field="published_at", schema="datetime")

    assert client.calls[0]["json"]["schema"] == "datetime"


# ------------------------------------------------------------------
# 보기
# ------------------------------------------------------------------


def test_list_payload_indexes_returns_only_the_mapping(make_collection):
    """호출자가 서버 응답이 어떤 키로 감싸 보내는지 알지 않아도 되게 한다."""
    col, client = make_collection(
        {
            "collection_name": COLLECTION,
            "indexes": {"source_pdf": "keyword", "published_at": "datetime"},
        }
    )

    result = col.list_payload_indexes(COLLECTION)

    assert result == {"source_pdf": "keyword", "published_at": "datetime"}
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["path"] == INDEXES_PATH


def test_no_indexes_is_an_empty_mapping(make_collection):
    """index 가 하나도 없는 것은 오류가 아니다.

    운영 컬렉션의 실제 상태가 이것이다 — 2026-09-18 실측에서 index 가 0개였다.
    """
    col, _ = make_collection({"collection_name": COLLECTION, "indexes": {}})

    assert col.list_payload_indexes(COLLECTION) == {}


# ------------------------------------------------------------------
# 지우기
# ------------------------------------------------------------------


def test_delete_payload_index_sends_field_in_body(make_collection):
    """필드명을 경로가 아니라 body 로 보낸다.

    운영 payload 에 슬래시가 든 키가 있어 경로 세그먼트로 두면 라우팅이 쪼개진다.
    """
    col, client = make_collection({"status": "deleted", "field": "source_pdf"})

    col.delete_payload_index(COLLECTION, field="source_pdf")

    call = client.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == INDEXES_PATH
    assert call["json"] == {"field": "source_pdf"}


# ------------------------------------------------------------------
# 빈 것은 SDK 가 막는다
# ------------------------------------------------------------------


def test_empty_schema_rejected_without_calling_server(make_collection):
    """빈 것을 막는다는 규칙을 field 에만 적용하면 반만 지킨 것이 된다."""
    col, client = make_collection()

    with pytest.raises(ValueError, match="schema"):
        col.create_payload_index(COLLECTION, field="source_pdf", schema="")

    assert client.calls == []


@pytest.mark.parametrize("method", ["create_payload_index", "delete_payload_index"])
def test_empty_field_rejected_without_calling_server(make_collection, method):
    col, client = make_collection()
    kwargs = {"schema": "keyword"} if method == "create_payload_index" else {}

    with pytest.raises(ValueError, match="field"):
        getattr(col, method)(COLLECTION, field="", **kwargs)

    assert client.calls == []


# ------------------------------------------------------------------
# 지울 수 없는 index 를 만들 때 알린다
# ------------------------------------------------------------------


def test_slash_in_field_warns_when_creating(make_collection):
    """되돌릴 수 없게 만드는 행위는 만드는 쪽이다.

    이름에 슬래시가 든 필드는 index 를 만들 수는 있으나 지울 수 없다
    (KiwoomAM/Kiwoom-Handler#74). 삭제 시점에 알려 주면 이미 늦다.
    """
    col, client = make_collection()

    with pytest.warns(UserWarning, match=SLASH_FIELD):
        col.create_payload_index(COLLECTION, field=SLASH_FIELD, schema="keyword")

    assert client.calls[0]["json"]["field"] == SLASH_FIELD


def test_slash_warning_names_the_upstream_issue(make_collection):
    """경고는 KiwoomAM/Kiwoom-Handler#74 가 고쳐지면 걷어낼 임시 조치다."""
    col, _ = make_collection()

    with pytest.warns(UserWarning, match=r"Kiwoom-Handler#74"):
        col.create_payload_index(COLLECTION, field=SLASH_FIELD, schema="keyword")


def test_ordinary_field_does_not_warn(make_collection):
    col, _ = make_collection()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        col.create_payload_index(COLLECTION, field="source_pdf", schema="keyword")


def test_deleting_a_slash_field_does_not_warn(make_collection):
    """경고는 만드는 쪽에만 둔다.

    지우는 시점에는 이미 선택이 끝났다. 여기서 또 경고하면 손쓸 수 없는 일을 두 번
    말하는 소음이 되고, 정작 막을 수 있었던 자리의 경고가 묻힌다.
    """
    col, _ = make_collection()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        col.delete_payload_index(COLLECTION, field=SLASH_FIELD)
