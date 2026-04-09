"""orchestrator.py 테스트 — run_many, run_many_stream 병렬 실행."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from llm_handler.exceptions import HandlerError
from llm_handler.models import LLMResponse, StreamChunk, TokenUsage

from app.logic.orchestrator import run_many, run_many_stream

from conftest import SAMPLE_RESPONSE, SAMPLE_STREAM_CHUNKS


def _make_state(contents: str = "hello", **overrides) -> dict:
    state = {
        "llm_input": {"contents": contents, "model": "flash"},
        "cache_mode": False,
        "save_mode": False,
    }
    state.update(overrides)
    return state


# ── run_many ──────────────────────────────────────────────────


class TestRunMany:

    @pytest.mark.asyncio
    @patch(
        "app.logic.runner.aio_call",
        new_callable=AsyncMock,
        return_value=SAMPLE_RESPONSE,
    )
    async def test_returns_all_results(self, mock_call):
        states = [_make_state(f"q{i}") for i in range(3)]
        results = []
        async for res, meta in run_many(states):
            results.append((res, meta))

        assert len(results) == 3
        indices = sorted(m["_index"] for _, m in results)
        assert indices == [0, 1, 2]

    @pytest.mark.asyncio
    @patch(
        "app.logic.runner.aio_call",
        new_callable=AsyncMock,
        return_value=SAMPLE_RESPONSE,
    )
    async def test_meta_contains_state(self, mock_call):
        state = _make_state("hello")
        async for res, meta in run_many([state]):
            assert meta["_state"] is state
            assert meta["_index"] == 0

    @pytest.mark.asyncio
    @patch(
        "app.logic.runner.aio_call",
        new_callable=AsyncMock,
        side_effect=HandlerError("fail", 500),
    )
    async def test_error_propagates(self, mock_call):
        with pytest.raises(HandlerError):
            async for _ in run_many([_make_state()]):
                pass

    @pytest.mark.asyncio
    async def test_missing_llm_input_raises(self):
        bad_state = {"cache_mode": False}
        with pytest.raises(ValueError, match="llm_input"):
            async for _ in run_many([bad_state]):
                pass


# ── run_many_stream ───────────────────────────────────────────


class TestRunManyStream:

    @pytest.mark.asyncio
    @patch("app.logic.runner.aio_stream")
    async def test_stream_events(self, mock_stream):
        async def _fake_stream(**kwargs):
            for c in SAMPLE_STREAM_CHUNKS:
                yield c

        mock_stream.return_value = _fake_stream()

        events = []
        async for event in run_many_stream([_make_state()]):
            events.append(event)

        types = [e["_type"] for e in events]
        assert "chunk" in types
        assert "done" in types

    @pytest.mark.asyncio
    @patch("app.logic.runner.aio_stream")
    async def test_stream_error_event(self, mock_stream):
        async def _fail_stream(**kwargs):
            yield SAMPLE_STREAM_CHUNKS[0]
            raise HandlerError("stream fail", 500)

        mock_stream.return_value = _fail_stream()

        events = []
        async for event in run_many_stream([_make_state()]):
            events.append(event)

        error_events = [e for e in events if e["_type"] == "error"]
        assert len(error_events) == 1
        assert isinstance(error_events[0]["error"], HandlerError)

    @pytest.mark.asyncio
    @patch("app.logic.runner.aio_stream")
    async def test_multiple_streams_all_indexed(self, mock_stream):
        call_count = 0

        async def _fake_stream(**kwargs):
            nonlocal call_count
            call_count += 1
            for c in SAMPLE_STREAM_CHUNKS:
                yield c

        mock_stream.side_effect = lambda **kw: _fake_stream(**kw)

        indices = set()
        async for event in run_many_stream([_make_state(f"q{i}") for i in range(3)]):
            indices.add(event["_index"])

        assert indices == {0, 1, 2}
