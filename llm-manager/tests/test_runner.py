"""runner.py 테스트 — 에러 발생 시 MongoDB 저장 + re-raise."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_handler.exceptions import HandlerError

from llm_request.runner import (
    llm_request,
    llm_request_async,
    llm_request_stream,
    llm_request_stream_async,
)

from conftest import SAMPLE_LLM_INPUT, SAMPLE_RESPONSE, SAMPLE_STREAM_CHUNKS


# ── sync call ─────────────────────────────────────────────────


class TestLlmRequestErrorSave:
    """llm_request: 에러 시 save_error 호출 후 re-raise."""

    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.sync_call", side_effect=HandlerError("fail", 503, "provider"))
    def test_error_saved_and_reraised(self, mock_call, mock_save_error, mock_collection):
        with pytest.raises(HandlerError):
            llm_request(SAMPLE_LLM_INPUT, cache_mode=False)

        mock_save_error.assert_called_once()
        args = mock_save_error.call_args
        assert isinstance(args[0][2], HandlerError)

    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.sync_call", side_effect=RuntimeError("unexpected"))
    def test_runtime_error_saved(self, mock_call, mock_save_error, mock_collection):
        with pytest.raises(RuntimeError):
            llm_request(SAMPLE_LLM_INPUT, cache_mode=False)

        mock_save_error.assert_called_once()

    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.sync_call", return_value=SAMPLE_RESPONSE)
    def test_success_does_not_call_save_error(self, mock_call, mock_save_error, mock_collection):
        res, meta = llm_request(SAMPLE_LLM_INPUT, cache_mode=False)

        mock_save_error.assert_not_called()
        assert res.text == "응답입니다"

    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.sync_call", side_effect=HandlerError("fail", 500))
    def test_save_mode_off_skips_save_error(self, mock_call, mock_save_error):
        with pytest.raises(HandlerError):
            llm_request(SAMPLE_LLM_INPUT, cache_mode=False, save_mode=False)

        mock_save_error.assert_not_called()


# ── async call ────────────────────────────────────────────────


class TestLlmRequestAsyncErrorSave:
    """llm_request_async: 에러 시 save_error 호출 후 re-raise."""

    @pytest.mark.asyncio
    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.aio_call", new_callable=AsyncMock, side_effect=HandlerError("fail", 503))
    async def test_async_error_saved_and_reraised(self, mock_call, mock_save_error, mock_collection):
        with pytest.raises(HandlerError):
            await llm_request_async(SAMPLE_LLM_INPUT, cache_mode=False)

        mock_save_error.assert_called_once()

    @pytest.mark.asyncio
    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.aio_call", new_callable=AsyncMock, return_value=SAMPLE_RESPONSE)
    async def test_async_success_no_save_error(self, mock_call, mock_save_error, mock_collection):
        res, meta = await llm_request_async(SAMPLE_LLM_INPUT, cache_mode=False)

        mock_save_error.assert_not_called()
        assert res.text == "응답입니다"


# ── sync stream ───────────────────────────────────────────────


class TestLlmRequestStreamErrorSave:
    """llm_request_stream: 스트림 도중 에러 시 save_error 호출."""

    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.sync_stream")
    def test_stream_error_saved(self, mock_stream, mock_save_error, mock_collection):
        def _failing_stream(**kwargs):
            yield SAMPLE_STREAM_CHUNKS[0]
            raise HandlerError("stream fail", 500)

        mock_stream.side_effect = _failing_stream

        stream_iter, meta = llm_request_stream(SAMPLE_LLM_INPUT, cache_mode=False)

        with pytest.raises(HandlerError):
            list(stream_iter)

        mock_save_error.assert_called_once()

    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.sync_stream")
    def test_stream_success_no_save_error(self, mock_stream, mock_save_error, mock_collection):
        mock_stream.return_value = iter(SAMPLE_STREAM_CHUNKS)

        stream_iter, meta = llm_request_stream(SAMPLE_LLM_INPUT, cache_mode=False)
        chunks = list(stream_iter)

        mock_save_error.assert_not_called()
        assert len(chunks) == 2


# ── async stream ──────────────────────────────────────────────


class TestLlmRequestStreamAsyncErrorSave:
    """llm_request_stream_async: 비동기 스트림 도중 에러 시 save_error 호출."""

    @pytest.mark.asyncio
    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.aio_stream")
    async def test_async_stream_error_saved(self, mock_stream, mock_save_error, mock_collection):
        async def _failing_stream(**kwargs):
            yield SAMPLE_STREAM_CHUNKS[0]
            raise HandlerError("stream fail", 500)

        mock_stream.return_value = _failing_stream()

        stream_iter, meta = await llm_request_stream_async(SAMPLE_LLM_INPUT, cache_mode=False)

        with pytest.raises(HandlerError):
            async for _ in stream_iter:
                pass

        mock_save_error.assert_called_once()

    @pytest.mark.asyncio
    @patch("llm_request.runner.save_error")
    @patch("llm_request.runner.aio_stream")
    async def test_async_stream_success_no_save_error(self, mock_stream, mock_save_error, mock_collection):
        async def _ok_stream(**kwargs):
            for c in SAMPLE_STREAM_CHUNKS:
                yield c

        mock_stream.return_value = _ok_stream()

        stream_iter, meta = await llm_request_stream_async(SAMPLE_LLM_INPUT, cache_mode=False)
        chunks = [c async for c in stream_iter]

        mock_save_error.assert_not_called()
        assert len(chunks) == 2
