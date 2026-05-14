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
