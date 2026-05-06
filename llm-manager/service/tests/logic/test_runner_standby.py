"""runner.py agent_llm standby 통합 테스트.

동시 요청 시 LLM 중복 호출이 방지되는지 실제 asyncio 동시성으로 검증.

Mock:
- runner.aio_call / runner.aio_stream: 카운터 + LLMResponse/StreamChunk 반환
- dedup.get_client: FakeRedisClient (fake_redis fixture)
- MongoDB: mock_collection (tests/conftest.py) — store/done_check/_recheck 자동 연결

공유 fixture:
- tests/logic/conftest.py: fake_redis, fast_backoff
- tests/conftest.py: mock_collection
"""

from __future__ import annotations

import asyncio

import pytest
from llm_handler.models import FunctionCall, LLMResponse, StreamChunk, TokenUsage

from app.logic import runner


# ── 헬퍼 ──────────────────────────────────────────────────────


def _make_response(text: str = "test response") -> LLMResponse:
    return LLMResponse(
        text=text,
        model="gemini-2.5-flash",
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        finish_reason="STOP",
        request_id="req-test",
    )


# ── agent_llm standby 통합 (non-stream) ──────────────────────


class TestAgentLlmStandby:
    """llm_request_async의 agent_llm standby 경로 검증."""

    async def test_concurrent_same_input_calls_llm_once(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """같은 llm_input 동시 요청 시 aio_call은 1회만 호출.

        - Task A: standby 선점 → aio_call → MDB save → release
        - Task B: SETNX 실패 → 대기 → MDB HIT → cached 반환
        """
        call_count = 0

        async def slow_aio_call(**kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)  # A가 standby 잡고 있을 시간
            return _make_response("answer from live call")

        monkeypatch.setattr(runner, "aio_call", slow_aio_call)

        llm_input = {
            "contents": "같은 질문",
            "model": "gemini-2.5-flash",
            "service_name": "test",
        }

        async def _run():
            return await runner.llm_request_async(
                llm_input,
                cache_mode=True, save_mode=True,
                standby_ttl_seconds=10, database="test_db",
            )

        results = await asyncio.gather(_run(), _run())

        assert call_count == 1, f"aio_call 중복 호출 (count={call_count})"

        # 정확히 하나는 live, 하나는 cache
        sources = sorted([r[1]["source"] for r in results])
        assert sources == ["cache", "live"]

        # 두 task 모두 같은 cache_key 공유
        cache_keys = {r[1]["cache_key"] for r in results}
        assert len(cache_keys) == 1

    async def test_timeout_falls_back_to_own_call(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """A가 LLM을 오래 잡으면 B는 timeout 후 자기가 호출 (Y' Hybrid)."""
        call_count = 0

        async def very_slow_call(**kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(1.5)  # standby_ttl(1)보다 김 → B는 timeout
            return _make_response(f"answer {call_count}")

        monkeypatch.setattr(runner, "aio_call", very_slow_call)

        llm_input = {
            "contents": "timeout test",
            "model": "gemini-2.5-flash",
            "service_name": "test",
        }

        async def _run():
            return await runner.llm_request_async(
                llm_input,
                cache_mode=True, save_mode=True,
                standby_ttl_seconds=1, database="test_db",
            )

        await asyncio.gather(_run(), _run())

        assert call_count == 2, f"timeout 후 양쪽 호출 예상, 실제={call_count}"

    async def test_cache_off_save_off_skips_standby(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """cache_mode=False, save_mode=False → dc 없음 → standby 경로 우회."""
        call_count = 0

        async def slow_aio_call(**kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return _make_response("no cache")

        monkeypatch.setattr(runner, "aio_call", slow_aio_call)

        llm_input = {
            "contents": "skip standby",
            "model": "gemini-2.5-flash",
            "service_name": "test",
        }

        async def _run():
            return await runner.llm_request_async(
                llm_input,
                cache_mode=False, save_mode=False,
                standby_ttl_seconds=10, database="test_db",
            )

        # 공유 캐시 없음 → dedup 없음 → 둘 다 LLM 호출
        await asyncio.gather(_run(), _run())
        assert call_count == 2


# ── agent_llm standby 통합 (stream) ──────────────────────────


class TestAgentLlmStreamStandby:
    """llm_request_stream_async의 agent_llm standby 경로 검증."""

    async def test_stream_wait_returns_cached_as_single_chunk(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """Stream 버전 — B는 cached 결과를 단일 StreamChunk로 yield."""
        stream_count = 0

        async def slow_aio_stream(**kwargs):
            nonlocal stream_count
            stream_count += 1
            await asyncio.sleep(0.1)  # A가 standby 잡고 있을 시간
            yield StreamChunk(text="chunk1-", finish_reason=None, usage=None)
            yield StreamChunk(
                text="chunk2",
                finish_reason="STOP",
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            )

        monkeypatch.setattr(runner, "aio_stream", slow_aio_stream)

        llm_input = {
            "contents": "stream dedup test",
            "model": "gemini-2.5-flash",
            "service_name": "test",
        }

        async def _run() -> list[StreamChunk]:
            chunks: list[StreamChunk] = []
            stream, _meta = await runner.llm_request_stream_async(
                llm_input,
                cache_mode=True, save_mode=True,
                standby_ttl_seconds=10, database="test_db",
            )
            async for chunk in stream:
                chunks.append(chunk)
            return chunks

        results = await asyncio.gather(_run(), _run())

        assert stream_count == 1, f"aio_stream 중복 호출 (count={stream_count})"

        # 한쪽은 2 chunk(live stream), 다른 쪽은 1 chunk(cached reconstruction)
        chunk_counts = sorted(len(r) for r in results)
        assert chunk_counts == [1, 2], f"chunk counts: {chunk_counts}"

        # Cached chunk의 text는 live stream 누적 text와 동일해야 함
        live_chunks = next(r for r in results if len(r) == 2)
        cached_chunks = next(r for r in results if len(r) == 1)

        live_text = "".join(c.text or "" for c in live_chunks)
        assert cached_chunks[0].text == live_text
        assert cached_chunks[0].finish_reason == "STOP"


# ── stream chunk 누적 시맨틱 회귀 ────────────────────────────


class TestStreamFunctionCallsAccumulation:
    """_do_llm_stream 의 function_calls 누적 분기 회귀.

    `if chunk.function_calls is not None: function_calls = chunk.function_calls`
    — 마지막 비-None 청크의 값을 채택하는 시맨틱이 깨지지 않는지 검증.
    기존 stream 테스트는 모든 청크 function_calls=None 이라 이 분기를 한 번도 실행하지 않음.
    """

    async def test_middle_chunk_function_calls_preserved_in_saved_response(
        self, mock_collection, fake_redis, monkeypatch,
    ):
        """중간 청크에만 function_calls 가 있고 마지막 청크가 None 이어도, 저장된 응답에 보존."""
        fc = FunctionCall(name="lookup", args={"q": "x"}, id="fc-1")

        async def fake_aio_stream(**kwargs):
            yield StreamChunk(text="a", finish_reason=None, usage=None, function_calls=None)
            yield StreamChunk(text="b", finish_reason=None, usage=None, function_calls=[fc])
            yield StreamChunk(
                text="c",
                finish_reason="STOP",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                function_calls=None,  # 마지막 청크는 None — 누적된 값이 보존되어야 함
            )

        monkeypatch.setattr(runner, "aio_stream", fake_aio_stream)

        llm_input = {
            "contents": "fc accumulation",
            "model": "gemini-2.5-flash",
            "service_name": "test",
        }

        stream, _meta = await runner.llm_request_stream_async(
            llm_input,
            cache_mode=True, save_mode=True,
            standby_ttl_seconds=10, database="test_db",
        )
        async for _ in stream:
            pass

        # mock_collection 은 store: dict[str, dict] — 저장된 doc 1개 있어야 함
        assert len(mock_collection) == 1
        saved = next(iter(mock_collection.values()))
        assert saved["result"]["function_calls"] == [fc.model_dump()]
        assert saved["result"]["text"] == "abc"


# ── Phase 3 lazy fetch dedup 통합 ──────────────────────────


class TestPhase3LazyFetchDedup:
    """같은 stored_files 동시 요청 시 file-manager.get_file 이 1 회만 호출됨."""

    async def test_concurrent_stored_files_fetch_once(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        from unittest.mock import AsyncMock, MagicMock

        from app.logic import runner
        from app.models.requests import StoredFile

        # fm.get_file mock + counter
        get_file_count = 0

        async def fake_get_file(file_path: str):
            nonlocal get_file_count
            get_file_count += 1
            await asyncio.sleep(0.05)  # leader fetch 시뮬레이션
            return (b"\x00\x01\x02", {})

        fm_instance = MagicMock()
        fm_instance.get_file = AsyncMock(side_effect=fake_get_file)
        monkeypatch.setattr(
            "app.logic.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        # llm_handler.upload_file mock — Files API URI 1개만 반환
        async def fake_upload(path, mime_type):
            from llm_handler.models import FileRef
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "app.logic.file_processing.resolver.upload_file", fake_upload,
        )

        # aio_call mock — LLM 응답
        monkeypatch.setattr(runner, "aio_call", AsyncMock(
            return_value=_make_response("ok"),
        ))

        # Files API 분기 가도록 — audio 25MB (image/audio inline 한도 20MB 초과, split 안 타는 mime)
        ref = StoredFile(
            file_path="shared.mp3",
            file_name="shared.mp3",
            file_size=25 * 1024 * 1024,
            file_mtime=0,
            mime_type="audio/mp3",
            extension=".mp3",
        )

        async def one_call():
            return await runner.llm_request_async(
                {"contents": "test", "model": "gemini-2.5-flash"},
                stored_files=[ref],
                cache_mode=True, save_mode=True,
                standby_ttl_seconds=10, database="test_db",
            )

        results = await asyncio.gather(*[one_call() for _ in range(10)])

        # leader 1 명만 fetch
        assert get_file_count == 1, f"expected 1 fetch, got {get_file_count}"
        assert len(results) == 10
        assert all(r[0].text == "ok" for r in results)
