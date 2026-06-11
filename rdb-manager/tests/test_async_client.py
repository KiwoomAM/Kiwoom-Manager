"""AsyncClient 단위 테스트 — httpx.MockTransport로 핸들러 응답 모의."""

import json

import httpx
import pytest

from rdb_manager._client import AsyncClient
from rdb_manager.exceptions import HandlerConnectionError, HandlerError


def make_client(handler):
    """MockTransport를 주입한 AsyncClient 생성."""
    client = AsyncClient(base_url="http://test")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


async def test_query_to_dataframe_request_and_dtype_restore():
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

    df = await make_client(handler).query_to_dataframe(
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


async def test_fetch_all_and_dict_and_one():
    def handler(request):
        path = request.url.path
        if path == "/v1/query/all":
            return httpx.Response(200, json={"rows": [[1, "a"]], "request_id": "r"})
        if path == "/v1/query/all-dict":
            return httpx.Response(200, json={"rows": [{"a": 1}], "request_id": "r"})
        if path == "/v1/query/one":
            return httpx.Response(200, json={"row": [9], "request_id": "r"})
        raise AssertionError(path)

    client = make_client(handler)
    assert await client.fetch_all("SELECT 1") == [(1, "a")]
    assert await client.fetch_all_dict("SELECT 1") == [{"a": 1}]
    assert await client.fetch_one("SELECT 1") == (9,)


async def test_fetch_one_none():
    def handler(request):
        return httpx.Response(200, json={"row": None, "request_id": "r"})

    assert await make_client(handler).fetch_one("SELECT 1") is None


async def test_query_body_omits_params_when_none():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rows": [], "request_id": "r"})

    await make_client(handler).fetch_all("SELECT 1")
    assert captured["body"] == {"query": "SELECT 1"}


async def test_dml_endpoints_path_order_and_rowcount():
    paths = []
    bodies = []

    def handler(request):
        import json as _json
        paths.append(request.url.path)
        bodies.append(_json.loads(request.content))
        return httpx.Response(200, json={"rowcount": 4, "request_id": "r"})

    client = make_client(handler)
    assert await client.execute_dml("UPDATE t SET x=1", {}) == 4
    assert await client.execute_insert("INSERT ...") == 4
    assert await client.execute_update("UPDATE ...") == 4
    assert paths == ["/v1/dml/execute", "/v1/dml/insert", "/v1/dml/update"]
    assert bodies[0] == {"sql": "UPDATE t SET x=1", "params": {}}


async def test_execute_many_empty_short_circuits():
    def handler(request):
        raise AssertionError("빈 params_list는 요청 금지")

    assert await make_client(handler).execute_many_dml("INSERT ...", []) == 0


async def test_execute_many_sends_list():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rowcount": 2, "request_id": "r"})

    assert await make_client(handler).execute_many_dml("INSERT", [{"v": 1}, {"v": 2}]) == 2
    assert captured["path"] == "/v1/dml/execute-many"
    assert captured["body"] == {"sql": "INSERT", "params_list": [{"v": 1}, {"v": 2}]}


async def test_connect_error_maps():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(HandlerConnectionError):
        await make_client(handler).fetch_all("SELECT 1")


async def test_http_error_maps():
    def handler(request):
        return httpx.Response(500, json={"error": "boom", "detail": "trace"})

    with pytest.raises(HandlerError) as exc:
        await make_client(handler).execute_dml("X")
    assert exc.value.status_code == 500
    assert exc.value.detail == "trace"


async def test_http_400_maps_with_detail():
    def handler(request):
        return httpx.Response(400, json={"error": "bad sql", "detail": "syntax"})

    with pytest.raises(HandlerError) as exc:
        await make_client(handler).execute_dml("BAD")
    assert exc.value.status_code == 400
    assert exc.value.detail == "syntax"


async def test_check_connection_true_false():
    def handler_ok(request):
        return httpx.Response(200, json={"connected": True})

    def handler_fail(request):
        raise httpx.ConnectError("down")

    assert await make_client(handler_ok).check_database_connection() is True
    assert await make_client(handler_fail).check_database_connection() is False
