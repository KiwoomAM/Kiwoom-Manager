"""SDK _client — Phase 2 stored_files 직렬화 검증."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_request.models import StoredFile


class TestRequestStoredFilesSerialization:
    """_client.request — stored_files 가 JSON body 로 직렬화되어 전달."""

    async def test_stored_files_in_request_body(self):
        from llm_request._client import AsyncClient

        # mock httpx response
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": {
                "text": "x", "model": "m",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "finish_reason": "STOP", "request_id": "r",
            },
            "meta": {"source": "live", "cache_hit": False},
        }

        # client 인스턴스 직접 만들고 _request 만 mock
        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._request = AsyncMock(return_value=mock_resp)

        await client.request(
            llm_input={"contents": "t"},
            stored_files=[StoredFile(
                file_path="abc.pdf",
                file_name="x.pdf",
                file_size=1,
                file_mtime=0,
                mime_type="application/pdf",
                extension=".pdf",
            )],
        )

        # _request 호출 시 body 에 stored_files 직렬화돼 들어갔는지
        call_args = client._request.call_args
        body = call_args.kwargs["json"]
        assert "stored_files" in body
        assert len(body["stored_files"]) == 1
        assert body["stored_files"][0]["file_path"] == "abc.pdf"
        assert body["stored_files"][0]["mime_type"] == "application/pdf"

    async def test_stored_files_omitted_when_none(self):
        """stored_files=None 이면 body 에 키 자체 없음."""
        from llm_request._client import AsyncClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": {
                "text": "x", "model": "m",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "finish_reason": "STOP", "request_id": "r",
            },
            "meta": {"source": "live", "cache_hit": False},
        }

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._request = AsyncMock(return_value=mock_resp)

        await client.request(llm_input={"contents": "t"})

        body = client._request.call_args.kwargs["json"]
        assert "stored_files" not in body


class TestExpireContextCache:
    """_client.delete_cache — body 직렬화 + 응답 파싱."""

    async def test_sends_correct_payload(self):
        from llm_request._client import AsyncClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"expired": ["h1"], "failed": []}

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._request = AsyncMock(return_value=mock_resp)

        result = await client.delete_cache(
            ["h1", "h2"], grace_seconds=30,
        )

        call_args = client._request.call_args
        assert call_args.args == ("POST", "/v1/context-cache/expire")
        assert call_args.kwargs["json"] == {
            "cache_ids": ["h1", "h2"],
            "grace_seconds": 30,
        }
        assert result == {"expired": ["h1"], "failed": []}

    async def test_default_grace_seconds_3(self):
        from llm_request._client import AsyncClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"expired": [], "failed": []}

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._request = AsyncMock(return_value=mock_resp)

        await client.delete_cache(["h1"])

        assert client._request.call_args.kwargs["json"]["grace_seconds"] == 3


class TestErrorDetailRelay:
    """_request 가 wire detail 을 ServiceError.detail 로 캡처 — llm-handler 의
    handler_internal sub-cause slug 가 llm-request caller 까지 보존."""

    async def test_request_captures_detail_from_error_body(self):
        from llm_request._client import AsyncClient
        from llm_request.exceptions import ServiceError

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {
            "error": "내부 서버 오류 (handler 자원 부족)",
            "detail": "pool_timeout",
            "error_type": "handler_internal",
            "request_id": "r",
        }

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ServiceError) as exc_info:
            await client._request("POST", "/v1/request", json={})

        assert exc_info.value.status_code == 500
        assert exc_info.value.error_type == "handler_internal"
        assert exc_info.value.detail == "pool_timeout"


class TestDeleteCacheInput:
    """delete_cache_async — 단건/리스트 정규화 + 로컬 입력 검증 (한글 raise)."""

    async def test_empty_list_raises_korean_valueerror(self):
        import llm_request

        with pytest.raises(ValueError, match="cache_id"):
            await llm_request.delete_cache_async([])

    async def test_grace_seconds_below_range_raises(self):
        import llm_request

        with pytest.raises(ValueError, match="1~3600"):
            await llm_request.delete_cache_async("h1", grace_seconds=0)

    async def test_grace_seconds_above_range_raises(self):
        import llm_request

        with pytest.raises(ValueError, match="1~3600"):
            await llm_request.delete_cache_async("h1", grace_seconds=4000)

    async def test_single_str_normalized_to_list(self, monkeypatch):
        import llm_request

        captured = {}
        mock_client = MagicMock()

        async def fake_delete(ids, *, grace_seconds):
            captured["ids"] = ids
            captured["grace"] = grace_seconds
            return {"expired": ids, "failed": []}

        mock_client.delete_cache = fake_delete

        async def fake_get_client():
            return mock_client

        monkeypatch.setattr(llm_request, "_get_client", fake_get_client)

        result = await llm_request.delete_cache_async("h1")

        assert captured["ids"] == ["h1"]  # 단건 문자열 → 리스트로 정규화
        assert captured["grace"] == 3
        assert result.expired == ["h1"]

    async def test_request_detail_none_when_absent(self):
        from llm_request._client import AsyncClient
        from llm_request.exceptions import ServiceError

        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "bad", "error_type": "validation"}

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ServiceError) as exc_info:
            await client._request("POST", "/v1/request", json={})

        assert exc_info.value.detail is None


class TestStreamErrorRaise:
    """스트림 중 error 키 이벤트 → ServiceError raise (llm-handler 와 동일 계약)."""

    async def test_stream_error_event_raises_service_error(self):
        from llm_request._client import AsyncClient
        from llm_request.exceptions import ServiceError

        async def _lines():
            yield 'data: {"type": "chunk", "text": "\\ubd80\\ubd84"}'
            yield 'data: {"error": "\\uc804\\uc1a1 \\ub2e8\\uc808", "error_type": "transport", "status_code": 502, "detail": null}'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.aiter_lines = lambda: _lines()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._http.stream = MagicMock(return_value=mock_ctx)

        stream, _meta = await client.request_stream(llm_input={"contents": "x"})

        collected = []
        with pytest.raises(ServiceError) as exc_info:
            async for ev in stream:
                collected.append(ev)

        assert collected and collected[0]["text"] == "부분"  # 정상 chunk 는 먼저 전달
        assert exc_info.value.error_type == "transport"
        assert exc_info.value.status_code == 502

    async def test_stream_error_event_preserves_detail(self):
        from llm_request._client import AsyncClient
        from llm_request.exceptions import ServiceError

        async def _lines():
            yield 'data: {"error": "x", "error_type": "handler_internal", "status_code": 500, "detail": "pool_timeout"}'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.aiter_lines = lambda: _lines()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        client = AsyncClient.__new__(AsyncClient)
        client._http = MagicMock()
        client._http.stream = MagicMock(return_value=mock_ctx)

        stream, _meta = await client.request_stream(llm_input={"contents": "x"})

        with pytest.raises(ServiceError) as exc_info:
            async for _ev in stream:
                pass

        assert exc_info.value.detail == "pool_timeout"
