"""SyncClient 단위 테스트 — httpx.MockTransport로 핸들러 응답 모의."""

import json

import httpx
import pytest

from rdb_manager._sync_client import SyncClient
from rdb_manager.exceptions import HandlerConnectionError, HandlerError


def make_client(handler):
    """MockTransport를 주입한 SyncClient 생성."""
    client = SyncClient(base_url="http://test")
    client._http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


def test_query_to_dataframe_request_and_dtype_restore():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "columns": ["id", "ts"],
            "rows": [[1, "2026-01-01T00:00:00"]],
            "dtypes": {"id": "int64", "ts": "datetime64[ns]"},
            "request_id": "r1",
        })

    df = make_client(handler).query_to_dataframe(
        "SELECT * FROM t WHERE id = %(id)s", {"id": 1}
    )
    assert captured["path"] == "/v1/query/dataframe"
    assert captured["body"] == {
        "query": "SELECT * FROM t WHERE id = %(id)s",
        "params": {"id": 1},
    }
    assert list(df.columns) == ["id", "ts"]
    assert str(df["id"].dtype) == "int64"
    assert str(df["ts"].dtype) == "datetime64[ns]"


def test_fetch_all_returns_tuples():
    def handler(request):
        assert request.url.path == "/v1/query/all"
        return httpx.Response(200, json={"rows": [[1, "a"], [2, "b"]], "request_id": "r"})

    assert make_client(handler).fetch_all("SELECT 1") == [(1, "a"), (2, "b")]


def test_fetch_all_dict_passthrough():
    def handler(request):
        assert request.url.path == "/v1/query/all-dict"
        return httpx.Response(200, json={"rows": [{"a": 1}], "request_id": "r"})

    assert make_client(handler).fetch_all_dict("SELECT 1") == [{"a": 1}]


def test_fetch_one_row_and_none():
    def handler_row(request):
        return httpx.Response(200, json={"row": [1, "a"], "request_id": "r"})

    def handler_none(request):
        return httpx.Response(200, json={"row": None, "request_id": "r"})

    assert make_client(handler_row).fetch_one("SELECT 1") == (1, "a")
    assert make_client(handler_none).fetch_one("SELECT 1") is None


def test_query_body_omits_params_when_none():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rows": [], "request_id": "r"})

    make_client(handler).fetch_all("SELECT 1")
    assert captured["body"] == {"query": "SELECT 1"}


def test_execute_dml_returns_rowcount():
    def handler(request):
        assert request.url.path == "/v1/dml/execute"
        assert json.loads(request.content) == {"sql": "UPDATE t SET x=1", "params": {}}
        return httpx.Response(200, json={"rowcount": 5, "request_id": "r"})

    assert make_client(handler).execute_dml("UPDATE t SET x=1", {}) == 5


def test_execute_many_empty_short_circuits_without_request():
    def handler(request):
        raise AssertionError("빈 params_list는 HTTP 요청을 보내면 안 됨")

    assert make_client(handler).execute_many_dml("INSERT ...", []) == 0


def test_execute_many_sends_params_list():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rowcount": 2, "request_id": "r"})

    rc = make_client(handler).execute_many_dml("INSERT t VALUES (%(v)s)", [{"v": 1}, {"v": 2}])
    assert rc == 2
    assert captured["path"] == "/v1/dml/execute-many"
    assert captured["body"] == {"sql": "INSERT t VALUES (%(v)s)", "params_list": [{"v": 1}, {"v": 2}]}


def test_insert_and_update_endpoints():
    def handler(request):
        assert request.url.path in ("/v1/dml/insert", "/v1/dml/update")
        return httpx.Response(200, json={"rowcount": 1, "request_id": "r"})

    client = make_client(handler)
    assert client.execute_insert("INSERT ...") == 1
    assert client.execute_update("UPDATE ...") == 1


def test_connect_error_maps_to_handler_connection_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(HandlerConnectionError):
        make_client(handler).fetch_all("SELECT 1")


def test_http_400_maps_to_handler_error_with_detail():
    def handler(request):
        return httpx.Response(400, json={"error": "bad sql", "detail": "syntax"})

    with pytest.raises(HandlerError) as exc:
        make_client(handler).execute_dml("BAD")
    assert exc.value.status_code == 400
    assert exc.value.detail == "syntax"


def test_check_database_connection_true_and_false():
    def handler_ok(request):
        assert request.url.path == "/v1/check"
        return httpx.Response(200, json={"connected": True})

    def handler_fail(request):
        raise httpx.ConnectError("down")

    assert make_client(handler_ok).check_database_connection() is True
    assert make_client(handler_fail).check_database_connection() is False
