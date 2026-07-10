"""runner.py agent_llm standby 통합 테스트.

동시 요청 시 LLM 중복 호출이 방지되는지 실제 asyncio 동시성으로 검증.

Mock:
- runner.aio_call / runner.aio_stream: 카운터 + LLMResponse/StreamChunk 반환
- dedup.RedisManager: FakeRedisHandler (fake_redis fixture)
- MongoDB: mock_collection (tests/conftest.py) — store/done_check/_recheck 자동 연결

공유 fixture:
- tests/logic/conftest.py: fake_redis, fast_backoff
- tests/conftest.py: mock_collection
"""

from __future__ import annotations

import asyncio

from llm_manager._internal import runner
from llm_manager._internal.handler_client.models import (
    CodeBlock,
    CodeResult,
    Content,
    FunctionCall,
    Grounding,
    GroundingChunk,
    LLMResponse,
    StreamChunk,
    TokenUsage,
    ToolCallInfo,
    ToolResponseInfo,
    UrlContextMetadata,
    UrlMetadata,
)

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
            "task_name": "test",
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
            "task_name": "test",
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
            "task_name": "test",
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
        # leader stream 종료 시 handler done event 의 assistant_message 로 저장됨 (PR #593 §2).
        # live text("chunk1-chunk2") 와 동일하게 두어 cached chunk text 검증이 일치하도록.
        monkeypatch.setattr(runner, "last_assistant_message", lambda: Content.model_validate({
            "role": "model",
            "content": [{"type": "text", "text": "chunk1-chunk2"}],
        }))

        llm_input = {
            "contents": "stream dedup test",
            "model": "gemini-2.5-flash",
            "task_name": "test",
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


# ── stream assistant_message 저장 회귀 ────────────────────────────


class TestStreamAssistantMessageSave:
    """_do_llm_stream 이 handler done event 의 assistant_message 로 저장하는지 회귀 (PR #593 §2).

    handler 가 한 턴 전체를 done event 의 assistant_message 로 모아 보내므로, llm-manager 는
    chunk 별 누적 없이 그대로 받아 저장한다. last_assistant_message 를 mock 해서 주입.
    """

    async def test_assistant_message_saved_with_text_derived(
        self, mock_collection, fake_redis, monkeypatch,
    ):
        """done event 의 assistant_message 가 cache 에 저장되고, text 가 part 에서 derive 됨."""
        async def fake_aio_stream(**kwargs):
            yield StreamChunk(text="안녕", finish_reason=None, usage=None)
            yield StreamChunk(
                text="하세요", finish_reason="STOP",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

        monkeypatch.setattr(runner, "aio_stream", fake_aio_stream)
        monkeypatch.setattr(runner, "last_assistant_message", lambda: Content.model_validate({
            "role": "model",
            "content": [{"type": "text", "text": "안녕하세요"}],
        }))

        llm_input = {"contents": "x", "model": "gemini-2.5-flash", "task_name": "test"}
        stream, _meta = await runner.llm_request_stream_async(
            llm_input, cache_mode=True, save_mode=True,
            standby_ttl_seconds=10, database="test_db",
        )
        async for _ in stream:
            pass

        assert len(mock_collection) == 1
        saved = next(iter(mock_collection.values()))
        assert saved["result"]["text"] == "안녕하세요"  # part 에서 derive
        assert saved["result"]["assistant_message"]["content"][0]["text"] == "안녕하세요"

    async def test_function_call_preserved_in_assistant_message(
        self, mock_collection, fake_redis, monkeypatch,
    ):
        """function_call part 가 assistant_message 안에 그대로 보존됨 (5필드 분리 저장 대신)."""
        async def fake_aio_stream(**kwargs):
            yield StreamChunk(
                text="", finish_reason="STOP",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

        monkeypatch.setattr(runner, "aio_stream", fake_aio_stream)
        monkeypatch.setattr(runner, "last_assistant_message", lambda: Content.model_validate({
            "role": "model",
            "content": [
                {"type": "function_call",
                 "function_call": {"name": "lookup", "args": {"q": "x"}, "id": "fc-1"}},
            ],
        }))

        llm_input = {"contents": "y", "model": "gemini-2.5-flash", "task_name": "test"}
        stream, _meta = await runner.llm_request_stream_async(
            llm_input, cache_mode=True, save_mode=True,
            standby_ttl_seconds=10, database="test_db",
        )
        async for _ in stream:
            pass

        saved = next(iter(mock_collection.values()))
        parts = saved["result"]["assistant_message"]["content"]
        assert parts[0]["function_call"]["name"] == "lookup"

    async def test_no_done_event_skips_save(
        self, mock_collection, fake_redis, monkeypatch,
    ):
        """done event 미수신(빈 응답 등) 시 캐시 저장 안 함."""
        async def fake_aio_stream(**kwargs):
            yield StreamChunk(text="부분", finish_reason=None, usage=None)

        monkeypatch.setattr(runner, "aio_stream", fake_aio_stream)
        monkeypatch.setattr(runner, "last_assistant_message", lambda: None)

        llm_input = {"contents": "z", "model": "gemini-2.5-flash", "task_name": "test"}
        stream, _meta = await runner.llm_request_stream_async(
            llm_input, cache_mode=True, save_mode=True,
            standby_ttl_seconds=10, database="test_db",
        )
        async for _ in stream:
            pass

        assert len(mock_collection) == 0  # 저장 안 됨


# ── stream candidate-level 메타 저장 회귀 (grounding/url_context 등) ──


class TestStreamCandidateMetaSave:
    """스트림 저장 시 llm 이 뱉은 candidate-level 메타를 통째로 보존하는지 검증 (PR #66 리뷰 2).

    스트림엔 완성 응답 객체가 없어 _finalize_stream 이 청크 조각으로 재조립하는데, 기존엔 text/
    usage/finish_reason/assistant_message 만 담고 grounding·url_context·function_calls·tool_calls·
    tool_responses·code_blocks·code_results 를 통째로 흘렸다. 이 필드들은 서로 다른 청크에 흩어져
    오므로(tool_call 은 자기 청크, grounding·url_context 는 마지막 청크), '마지막 청크만 읽기'가
    아니라 청크 누적이어야 함을 함께 검증한다.
    """

    async def test_meta_scattered_across_chunks_all_saved(
        self, mock_collection, fake_redis, monkeypatch,
    ):
        async def fake_aio_stream(**kwargs):
            # 1번째 청크: grounding + function_calls 첫 조각 (last 아님)
            yield StreamChunk(
                text="검색 결과: ", finish_reason=None, usage=None,
                function_calls=[FunctionCall(name="get_price", args={"sym": "005930"}, id="fc-1")],
                grounding=Grounding(
                    web_search_queries=["삼성전자 주가"],
                    chunks=[GroundingChunk(title="mk.co.kr", uri="https://vertex.../redir/a")],
                ),
            )
            # 2번째 청크: function_calls 를 1번 청크와 나눠 실음(누적 검증) + tool 계열
            yield StreamChunk(
                text="87,500원", finish_reason=None, usage=None,
                function_calls=[FunctionCall(name="get_news", args={"sym": "005930"}, id="fc-2")],
                tool_calls=[ToolCallInfo(tool_type="GOOGLE_SEARCH_WEB", id="tc-1")],
                tool_responses=[ToolResponseInfo(tool_type="GOOGLE_SEARCH_WEB", id="tc-1")],
                code_blocks=[CodeBlock(language="PYTHON", code="print(1)", id="cb-1")],
                code_results=[CodeResult(outcome="OUTCOME_OK", output="1", id="cr-1")],
            )
            # 마지막 청크: url_context + finish_reason/usage (그 외 필드는 전부 None)
            yield StreamChunk(
                text="입니다.", finish_reason="STOP",
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                url_context=UrlContextMetadata(url_metadata=[
                    UrlMetadata(
                        retrieved_url="https://example.com/a",
                        url_retrieval_status="URL_RETRIEVAL_STATUS_SUCCESS",
                    ),
                ]),
            )

        monkeypatch.setattr(runner, "aio_stream", fake_aio_stream)
        monkeypatch.setattr(runner, "last_assistant_message", lambda: Content.model_validate({
            "role": "model",
            "content": [{"type": "text", "text": "검색 결과: 87,500원입니다."}],
        }))

        llm_input = {"contents": "삼성전자 주가", "model": "gemini-2.5-flash", "task_name": "test"}
        stream, _meta = await runner.llm_request_stream_async(
            llm_input, cache_mode=True, save_mode=True,
            standby_ttl_seconds=10, database="test_db",
        )
        async for _ in stream:
            pass

        assert len(mock_collection) == 1
        result = next(iter(mock_collection.values()))["result"]

        # function_calls 는 1·2번 청크에 나뉘어 왔으므로 둘 다 남아야 함(extend 누적)
        assert [fc["name"] for fc in result["function_calls"]] == ["get_price", "get_news"]
        assert result["grounding"]["web_search_queries"] == ["삼성전자 주가"]
        assert result["tool_calls"][0]["tool_type"] == "GOOGLE_SEARCH_WEB"
        assert result["tool_responses"][0]["tool_type"] == "GOOGLE_SEARCH_WEB"
        assert result["code_blocks"][0]["code"] == "print(1)"
        assert result["code_results"][0]["output"] == "1"
        assert result["url_context"]["url_metadata"][0]["retrieved_url"] == "https://example.com/a"


# ── _StreamMeta 필드별 누적 규칙 (PR #66 재리뷰) ──


class TestStreamMetaAccumulation:
    """청크마다 하나씩 흩어져 오는 리스트 5종은 extend, 턴당 한 번 오는 grounding·url_context 는
    마지막 non-None 덮어쓰기. parallel fc N개 → N chunks(1개씩) 계약(llm-handler)."""

    def test_list_fields_extend_across_chunks(self):
        from llm_manager._internal.runner import _StreamMeta

        m = _StreamMeta()
        m.update(StreamChunk(text="", function_calls=[FunctionCall(name="get_price", id="fc-1")]))
        m.update(StreamChunk(text="", function_calls=[FunctionCall(name="get_news", id="fc-2")]))
        m.update(StreamChunk(text="", function_calls=[FunctionCall(name="get_chart", id="fc-3")]))

        assert [fc.id for fc in m.function_calls] == ["fc-1", "fc-2", "fc-3"]

    def test_tool_calls_extend_across_chunks(self):
        from llm_manager._internal.runner import _StreamMeta

        m = _StreamMeta()
        m.update(StreamChunk(text="", tool_calls=[ToolCallInfo(tool_type="GOOGLE_SEARCH_WEB", id="tc-1")]))
        m.update(StreamChunk(text="", tool_calls=[ToolCallInfo(tool_type="URL_CONTEXT", id="tc-2")]))

        assert [tc.id for tc in m.tool_calls] == ["tc-1", "tc-2"]

    def test_grounding_url_context_last_non_none_wins(self):
        """단발성 컨테이너는 누적이 아니라 마지막 non-None 유지 — 뒤 None 청크가 덮지 않음."""
        from llm_manager._internal.runner import _StreamMeta

        m = _StreamMeta()
        m.update(StreamChunk(text="a"))  # 둘 다 None
        m.update(StreamChunk(
            text="b",
            grounding=Grounding(web_search_queries=["q"]),
            url_context=UrlContextMetadata(url_metadata=[
                UrlMetadata(retrieved_url="https://x", url_retrieval_status="URL_RETRIEVAL_STATUS_SUCCESS"),
            ]),
        ))
        m.update(StreamChunk(text="c"))  # 다시 None — 앞 값 유지되어야

        assert m.grounding is not None
        assert m.grounding.web_search_queries == ["q"]
        assert m.url_context is not None
        assert m.url_context.url_metadata[0].retrieved_url == "https://x"


# ── Phase 3 lazy fetch dedup 통합 ──────────────────────────


class TestPhase3LazyFetchDedup:
    """같은 stored_files 동시 요청 시 file-manager.get_file 이 1 회만 호출됨."""

    async def test_concurrent_stored_files_fetch_once(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        from unittest.mock import AsyncMock, MagicMock

        from llm_manager._internal import runner
        from llm_manager.models import StoredFile

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
            "llm_manager._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        # llm_manager._internal.handler_client.upload_file mock — Files API URI 1개만 반환
        async def fake_upload(path, mime_type):
            from llm_manager._internal.handler_client.models import FileRef
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
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
