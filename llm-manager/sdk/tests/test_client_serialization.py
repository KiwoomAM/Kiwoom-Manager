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
