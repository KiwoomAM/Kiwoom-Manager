"""router 엔드포인트 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def test_client():
    return TestClient(app)


def test_health_check(test_client):
    """헬스 체크 엔드포인트."""
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_done_check_cache_miss(test_client, mock_collection):
    """done-check: MDB 캐시 miss."""
    resp = test_client.post("/v1/done-check", json={
        "llm_input": {"contents": "테스트", "model": "gemini-2.5-flash"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cache_hit"] is False
    assert len(data["cache_key"]) == 64


def test_done_check_cache_hit(test_client, mock_collection):
    """done-check: MDB 캐시 hit."""
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {"text": "캐시된 응답"},
        "metadata": {},
    }

    resp = test_client.post("/v1/done-check", json={"llm_input": llm_input})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cache_hit"] is True
    assert data["cache_key"] == cache_key


def test_request_cache_hit(test_client, mock_collection):
    """request: MDB 캐시 히트 시 즉시 반환."""
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {
            "text": "캐시된 응답",
            "model": "gemini-2.5-flash",
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            "finish_reason": "STOP",
            "request_id": "req-001",
        },
        "metadata": {"model": "gemini-2.5-flash"},
    }

    resp = test_client.post("/v1/request", json={
        "llm_input": llm_input,
        "cache_mode": True,
        "save_mode": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["cache_hit"] is True
    assert data["result"]["text"] == "캐시된 응답"


def test_request_cache_off(test_client):
    """request: cache_mode=False, save_mode=False → LLM 호출."""
    mock_response = MagicMock()
    mock_response.text = "LLM 응답"
    mock_response.model = "gemini-2.5-flash"
    mock_response.finish_reason = "STOP"
    mock_response.request_id = "req-002"
    mock_response.usage = None
    mock_response.function_calls = None
    mock_response.model_dump.return_value = {
        "text": "LLM 응답",
        "model": "gemini-2.5-flash",
        "finish_reason": "STOP",
        "request_id": "req-002",
        "usage": None,
        "function_calls": None,
    }

    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, return_value=mock_response):
        resp = test_client.post("/v1/request", json={
            "llm_input": {"contents": "테스트", "model": "gemini-2.5-flash"},
            "cache_mode": False,
            "save_mode": False,
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["text"] == "LLM 응답"
    assert data["meta"]["cache_hit"] is False


def test_fetch_not_found(test_client, mock_collection):
    """fetch: 존재하지 않는 cache_key → 404 not_found (불투명한 500 대신 명확한 신호)."""
    resp = test_client.post("/v1/fetch", json={
        "cache_key": "0" * 64,
    })
    assert resp.status_code == 404
    data = resp.json()
    assert data["error_type"] == "not_found"


# ── llm-handler 에러 relay (변형 없이 그대로 caller 에게) ──────────


def _cache_off_body() -> dict:
    return {
        "llm_input": {"contents": "테스트", "model": "gemini-2.5-flash"},
        "cache_mode": False,
        "save_mode": False,
    }


def test_request_relays_handler_error_content_blocked(test_client):
    """llm-handler 의 ContentBlocked(422) 가 그대로 caller 에게 relay 된다."""
    from llm_handler.exceptions import HandlerError

    err = HandlerError("Gemini 가 응답을 차단 (finish_reason=SAFETY)", status_code=422, error_type="content_blocked")
    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, side_effect=err):
        resp = test_client.post("/v1/request", json=_cache_off_body())

    assert resp.status_code == 422
    data = resp.json()
    assert data["error_type"] == "content_blocked"
    assert "차단" in data["error"]


def test_request_relays_handler_error_transport_504(test_client):
    """transport(504) status_code/error_type 가 평탄화 없이 보존된다."""
    from llm_handler.exceptions import HandlerError

    err = HandlerError("전송 계층 실패: ReadTimeout", status_code=504, error_type="transport")
    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, side_effect=err):
        resp = test_client.post("/v1/request", json=_cache_off_body())

    assert resp.status_code == 504
    assert resp.json()["error_type"] == "transport"


def test_request_relays_handler_ops_502(test_client):
    """handler_ops(502, Gemini 401/403) relay — caller 가 운영팀 alert 라우팅 가능."""
    from llm_handler.exceptions import HandlerError

    err = HandlerError("Gemini 권한/인증 실패", status_code=502, error_type="handler_ops")
    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, side_effect=err):
        resp = test_client.post("/v1/request", json=_cache_off_body())

    assert resp.status_code == 502
    assert resp.json()["error_type"] == "handler_ops"


def test_request_relays_handler_connection_error_as_502(test_client):
    """llm-handler 서비스 도달 불가(status_code=0) → 502 transport (재시도 가능 신호)."""
    from llm_handler.exceptions import HandlerConnectionError

    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, side_effect=HandlerConnectionError()):
        resp = test_client.post("/v1/request", json=_cache_off_body())

    assert resp.status_code == 502
    data = resp.json()
    # 도달 불가 = 재시도 가능한 전송 계층 장애 → caller 가 transport 로 동일 재시도 분기.
    assert data["error_type"] == "transport"


def test_request_relays_handler_internal_detail(test_client):
    """handler_internal 의 detail(sub-cause slug)도 변형 없이 relay — 운영 alert 라우팅용."""
    from llm_handler.exceptions import HandlerError

    err = HandlerError(
        "내부 서버 오류 (handler 자원 부족)",
        status_code=500,
        error_type="handler_internal",
        detail="pool_timeout",
    )
    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, side_effect=err):
        resp = test_client.post("/v1/request", json=_cache_off_body())

    assert resp.status_code == 500
    data = resp.json()
    assert data["error_type"] == "handler_internal"
    assert data["detail"] == "pool_timeout"


def test_request_unhandled_exception_carries_detail(test_client):
    """llm-request 자체 미처리 예외 → 500 + request_internal + detail=exception class name.

    relay 되는 handler_internal(=llm-handler 내부)과 구분되는 별도 라벨.
    스트림/비스트림 경로 무관 동일 분류.
    """
    with patch("app.logic.runner.aio_call", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        resp = test_client.post("/v1/request", json=_cache_off_body())

    assert resp.status_code == 500
    data = resp.json()
    assert data["error_type"] == "request_internal"
    assert data["detail"] == "RuntimeError"


# ── Phase 1 — file_meta 응답 노출 (endpoint 통합) ──────────────


def test_request_cache_hit_exposes_file_meta_in_response(test_client, mock_collection):
    """/v1/request 응답 JSON 에 meta.file_meta 가 포함되는지 (cache hit 경로 end-to-end)."""
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "Phase1 통합 테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {
            "text": "캐시된 응답",
            "model": "gemini-2.5-flash",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "finish_reason": "STOP",
            "request_id": "r",
        },
        "metadata": {"model": "gemini-2.5-flash"},
        "file_meta": [{
            "file_name": "x.pdf", "file_size": 1, "file_mtime": 0,
            "mime_type": "application/pdf", "extension": ".pdf",
            "file_path": "abc-uuid.pdf",
        }],
    }

    resp = test_client.post("/v1/request", json={
        "llm_input": llm_input,
        "cache_mode": True, "save_mode": True,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert "file_meta" in data["meta"]
    assert data["meta"]["file_meta"][0]["file_path"] == "abc-uuid.pdf"


def test_request_cache_hit_legacy_doc_omits_file_meta(test_client, mock_collection):
    """cache hit + 옛 doc (file_meta 키 없음) → 응답에도 키 없음."""
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "Phase1 legacy 테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {
            "text": "캐시", "model": "gemini-2.5-flash",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "finish_reason": "STOP", "request_id": "r",
        },
        "metadata": {},
        # file_meta 키 의도적 부재
    }

    resp = test_client.post("/v1/request", json={
        "llm_input": llm_input,
        "cache_mode": True, "save_mode": True,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert "file_meta" not in data["meta"]


def test_fetch_endpoint_exposes_file_meta(test_client, mock_collection):
    """/v1/fetch 응답에 file_meta 노출."""
    cache_key = "fetch" + "0" * 59
    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {
            "text": "fetched", "model": "gemini-2.5-flash",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "finish_reason": "STOP", "request_id": "r",
        },
        "metadata": {},
        "file_meta": [{
            "file_name": "y.pdf", "file_size": 1, "file_mtime": 0,
            "mime_type": "application/pdf", "extension": ".pdf",
            "file_path": "fetched-uuid.pdf",
        }],
    }

    resp = test_client.post("/v1/fetch", json={"cache_key": cache_key})

    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["file_meta"][0]["file_path"] == "fetched-uuid.pdf"


# ── Phase 2 — router 가 stored_files 를 runner 로 전달하는지 ──────


def test_request_routes_stored_files_to_runner(test_client, monkeypatch):
    """/v1/request 가 body.stored_files 를 llm_request_async 로 전달."""
    from llm_handler.models import LLMResponse, TokenUsage

    captured = {}

    async def spy_llm_request_async(llm_input, **kwargs):
        captured["llm_input"] = llm_input
        captured.update(kwargs)
        return (
            LLMResponse(
                text="ok", model="gemini-2.5-flash",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                finish_reason="STOP", request_id="r",
            ),
            {"source": "live", "cache_hit": False},
        )

    # 라우터가 import 한 reference 를 patch
    monkeypatch.setattr("app.routers.request.llm_request_async", spy_llm_request_async)

    resp = test_client.post("/v1/request", json={
        "llm_input": {"contents": "test", "model": "gemini-2.5-flash"},
        "stored_files": [{
            "file_path": "abc.pdf", "file_name": "x.pdf", "file_size": 1, "file_mtime": 0,
            "mime_type": "application/pdf", "extension": ".pdf",
        }],
    })

    assert resp.status_code == 200
    assert captured["stored_files"] is not None
    assert len(captured["stored_files"]) == 1
    assert captured["stored_files"][0].file_path == "abc.pdf"
    assert captured["stored_files"][0].mime_type == "application/pdf"


def test_request_stream_routes_stored_files_to_runner(test_client, monkeypatch):
    """/v1/request/stream 동일 — body.stored_files 를 llm_request_stream_async 로 전달."""
    from llm_handler.models import StreamChunk, TokenUsage

    captured = {}

    async def spy_stream_async(llm_input, **kwargs):
        captured["llm_input"] = llm_input
        captured.update(kwargs)

        async def _empty_stream():
            yield StreamChunk(
                text="ok", finish_reason="STOP",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

        return _empty_stream(), {"source": "live", "cache_hit": False}

    monkeypatch.setattr("app.routers.request.llm_request_stream_async", spy_stream_async)

    resp = test_client.post("/v1/request/stream", json={
        "llm_input": {"contents": "test", "model": "gemini-2.5-flash"},
        "stored_files": [{
            "file_path": "abc.pdf", "file_name": "x.pdf", "file_size": 1, "file_mtime": 0,
            "mime_type": "application/pdf", "extension": ".pdf",
        }],
    })

    assert resp.status_code == 200
    assert captured["stored_files"] is not None
    assert captured["stored_files"][0].file_path == "abc.pdf"


# ── 스트림 mid-stream 에러 relay (in-band SSE error 이벤트) ────────


def _parse_sse_events(body: str) -> list[dict]:
    """SSE 응답 본문에서 [DONE] 제외한 data 이벤트 dict 목록 추출."""
    import json
    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            if payload != "[DONE]":
                out.append(json.loads(payload))
    return out


def test_stream_relays_mid_stream_handler_error(test_client, monkeypatch):
    """스트림 도중 HandlerError → error 키 SSE 이벤트로 relay, [DONE] 미발송."""
    from llm_handler.exceptions import HandlerError
    from llm_handler.models import StreamChunk

    async def spy_stream_async(llm_input, **kwargs):
        async def _gen():
            yield StreamChunk(text="부분", finish_reason=None, usage=None)
            raise HandlerError("Gemini 전송 단절", status_code=502, error_type="transport")

        return _gen(), {"source": "live", "cache_hit": False}

    monkeypatch.setattr("app.routers.request.llm_request_stream_async", spy_stream_async)

    resp = test_client.post("/v1/request/stream", json={
        "llm_input": {"contents": "x", "model": "gemini-2.5-flash"},
    })

    body = resp.text
    events = _parse_sse_events(body)
    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1
    assert error_events[0]["error_type"] == "transport"
    assert error_events[0]["status_code"] == 502
    assert "[DONE]" not in body  # 에러 시 done 미발송


def test_stream_relays_handler_internal_detail(test_client, monkeypatch):
    """스트림 도중 handler_internal 의 detail 슬러그도 그대로 relay."""
    from llm_handler.exceptions import HandlerError
    from llm_handler.models import StreamChunk

    async def spy_stream_async(llm_input, **kwargs):
        async def _gen():
            yield StreamChunk(text="부분", finish_reason=None, usage=None)
            raise HandlerError("자원 부족", status_code=500, error_type="handler_internal", detail="pool_timeout")

        return _gen(), {"source": "live", "cache_hit": False}

    monkeypatch.setattr("app.routers.request.llm_request_stream_async", spy_stream_async)

    resp = test_client.post("/v1/request/stream", json={
        "llm_input": {"contents": "x", "model": "gemini-2.5-flash"},
    })

    error_events = [e for e in _parse_sse_events(resp.text) if "error" in e]
    assert error_events[0]["error_type"] == "handler_internal"
    assert error_events[0]["detail"] == "pool_timeout"


def test_stream_handler_connection_error_becomes_transport(test_client, monkeypatch):
    """스트림 도중 llm-handler 도달 불가 → 502 transport (재시도 가능 신호)."""
    from llm_handler.exceptions import HandlerConnectionError
    from llm_handler.models import StreamChunk

    async def spy_stream_async(llm_input, **kwargs):
        async def _gen():
            yield StreamChunk(text="부분", finish_reason=None, usage=None)
            raise HandlerConnectionError()

        return _gen(), {"source": "live", "cache_hit": False}

    monkeypatch.setattr("app.routers.request.llm_request_stream_async", spy_stream_async)

    resp = test_client.post("/v1/request/stream", json={
        "llm_input": {"contents": "x", "model": "gemini-2.5-flash"},
    })

    error_events = [e for e in _parse_sse_events(resp.text) if "error" in e]
    assert error_events[0]["error_type"] == "transport"
    assert error_events[0]["status_code"] == 502


def test_stream_unhandled_exception_becomes_request_internal(test_client, monkeypatch):
    """스트림 도중 llm-request 자체 미처리 예외 → request_internal + detail=예외클래스명.

    비스트림 catch-all 과 동일 라벨 (경로 무관 일관), relay handler_internal 과는 구분.
    """
    from llm_handler.models import StreamChunk

    async def spy_stream_async(llm_input, **kwargs):
        async def _gen():
            yield StreamChunk(text="부분", finish_reason=None, usage=None)
            raise RuntimeError("boom")

        return _gen(), {"source": "live", "cache_hit": False}

    monkeypatch.setattr("app.routers.request.llm_request_stream_async", spy_stream_async)

    resp = test_client.post("/v1/request/stream", json={
        "llm_input": {"contents": "x", "model": "gemini-2.5-flash"},
    })

    error_events = [e for e in _parse_sse_events(resp.text) if "error" in e]
    assert error_events[0]["error_type"] == "request_internal"
    assert error_events[0]["detail"] == "RuntimeError"


# ── /v1/context-cache/expire ─────────────────────────────────


def test_expire_endpoint_returns_200(test_client, monkeypatch):
    """expire_caches monkeypatch — 라우터 자체만 검증."""
    from app.models.responses import ExpireContextCacheResponse, ExpireFailure

    async def fake_expire(ids, grace):
        return ExpireContextCacheResponse(
            expired=[ids[0]],
            failed=[ExpireFailure(cache_id=ids[1], error="x")] if len(ids) > 1 else [],
        )

    monkeypatch.setattr(
        "app.routers.request.expire_caches", fake_expire,
    )

    resp = test_client.post(
        "/v1/context-cache/expire",
        json={"cache_ids": ["h1", "h2"], "grace_seconds": 60},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["expired"] == ["h1"]
    assert body["failed"][0]["cache_id"] == "h2"


def test_expire_endpoint_validates_empty_ids(test_client):
    resp = test_client.post(
        "/v1/context-cache/expire",
        json={"cache_ids": [], "grace_seconds": 60},
    )
    assert resp.status_code == 422


def test_expire_endpoint_validates_grace_lower_bound(test_client):
    resp = test_client.post(
        "/v1/context-cache/expire",
        json={"cache_ids": ["h1"], "grace_seconds": 0},
    )
    assert resp.status_code == 422


def test_expire_endpoint_validates_grace_upper_bound(test_client):
    resp = test_client.post(
        "/v1/context-cache/expire",
        json={"cache_ids": ["h1"], "grace_seconds": 3601},
    )
    assert resp.status_code == 422


def test_expire_endpoint_default_grace_seconds(test_client, monkeypatch):
    """grace_seconds 생략 시 기본 60 으로 동작."""
    from app.models.responses import ExpireContextCacheResponse

    captured = {}

    async def fake_expire(ids, grace):
        captured["grace"] = grace
        return ExpireContextCacheResponse(expired=ids, failed=[])

    monkeypatch.setattr(
        "app.routers.request.expire_caches", fake_expire,
    )

    resp = test_client.post(
        "/v1/context-cache/expire",
        json={"cache_ids": ["h1"]},
    )
    assert resp.status_code == 200
    assert captured["grace"] == 60
