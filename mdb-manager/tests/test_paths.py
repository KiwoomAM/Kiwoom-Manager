# -*- coding: utf-8 -*-
"""body 빌더 단위 테스트 — TTL 지원 검증."""

from mdb_handler._paths import build_insert_many_body, build_insert_one_body


def test_insert_one_body_without_ttl_omits_key():
    body = build_insert_one_body({"a": 1})
    assert body == {"document": {"a": 1}}
    assert "ttl" not in body


def test_insert_one_body_with_ttl_includes_key():
    body = build_insert_one_body({"a": 1}, ttl=60)
    assert body == {"document": {"a": 1}, "ttl": 60}


def test_insert_many_body_without_ttl_omits_key():
    body = build_insert_many_body([{"a": 1}, {"b": 2}])
    assert body == {"documents": [{"a": 1}, {"b": 2}], "ordered": True}
    assert "ttl" not in body


def test_insert_many_body_with_ttl_includes_key():
    body = build_insert_many_body([{"a": 1}], ttl=120, ordered=False)
    assert body == {"documents": [{"a": 1}], "ordered": False, "ttl": 120}


def test_build_envelope_insert_body_minimal():
    from mdb_handler._paths import build_envelope_insert_body

    body = build_envelope_insert_body(type_="report")
    assert body == {"type": "report", "metadata": {}, "data": {}}


def test_build_envelope_insert_body_full():
    from mdb_handler._paths import build_envelope_insert_body

    body = build_envelope_insert_body(
        type_="report",
        metadata={"source": "u1"},
        data={"summary": "x"},
        ttl=60,
    )
    assert body == {
        "type": "report",
        "metadata": {"source": "u1"},
        "data": {"summary": "x"},
        "ttl": 60,
    }


def test_build_envelope_insert_body_omits_ttl_when_none():
    from mdb_handler._paths import build_envelope_insert_body

    body = build_envelope_insert_body(type_="r", ttl=None)
    assert "ttl" not in body


def test_envelope_find_body_minimal():
    from mdb_handler._paths import build_envelope_find_body

    assert build_envelope_find_body() == {"filter": {}}


def test_envelope_find_body_full():
    from mdb_handler._paths import build_envelope_find_body

    body = build_envelope_find_body(
        {"k": 1}, projection={"k": 1}, sort=[["k", -1]], limit=10, skip=2
    )
    assert body == {
        "filter": {"k": 1},
        "projection": {"k": 1},
        "sort": [["k", -1]],
        "limit": 10,
        "skip": 2,
    }


def test_envelope_find_one_body_minimal():
    from mdb_handler._paths import build_envelope_find_one_body

    assert build_envelope_find_one_body() == {"filter": {}}


def test_envelope_update_body_without_ttl():
    from mdb_handler._paths import build_envelope_update_body

    body = build_envelope_update_body({"k": 1}, {"$set": {"x": 1}})
    assert body == {"filter": {"k": 1}, "update": {"$set": {"x": 1}}}


def test_envelope_update_body_with_ttl():
    from mdb_handler._paths import build_envelope_update_body

    body = build_envelope_update_body({"k": 1}, {"$set": {"x": 1}}, ttl=3600)
    assert body == {
        "filter": {"k": 1},
        "update": {"$set": {"x": 1}},
        "ttl": 3600,
    }


def test_envelope_delete_body():
    from mdb_handler._paths import build_envelope_delete_body

    assert build_envelope_delete_body({"k": 1}) == {"filter": {"k": 1}}


def test_envelope_update_body_omits_upsert_keys_by_default():
    from mdb_handler._paths import build_envelope_update_body

    body = build_envelope_update_body({"k": 1}, {"$set": {"x": 1}})
    assert "upsert" not in body
    assert "type" not in body


def test_envelope_update_body_with_upsert_and_type():
    from mdb_handler._paths import build_envelope_update_body

    body = build_envelope_update_body(
        {"k": 1},
        {"$set": {"x": 1}, "$setOnInsert": {"data.k": "v"}},
        upsert=True,
        type_="report",
    )
    assert body == {
        "filter": {"k": 1},
        "update": {"$set": {"x": 1}, "$setOnInsert": {"data.k": "v"}},
        "upsert": True,
        "type": "report",
    }


def test_envelope_update_body_with_upsert_and_type_and_ttl():
    from mdb_handler._paths import build_envelope_update_body

    body = build_envelope_update_body(
        {"k": 1},
        {"$set": {"x": 1}},
        ttl=60,
        upsert=True,
        type_="report",
    )
    assert body == {
        "filter": {"k": 1},
        "update": {"$set": {"x": 1}},
        "upsert": True,
        "type": "report",
        "ttl": 60,
    }


def test_envelope_update_body_upsert_false_omits_key():
    from mdb_handler._paths import build_envelope_update_body

    body = build_envelope_update_body({"k": 1}, {"$set": {"x": 1}}, upsert=False)
    assert "upsert" not in body
