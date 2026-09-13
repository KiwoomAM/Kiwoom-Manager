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
from llm_manager._internal.handler_client._common import (
    STREAM_CONTAINER_FIELDS,
    STREAM_LIST_FIELDS,
)
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

    async def test_timeout_waits_for_slow_leader_instead_of_duplicating(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """A가 LLM을 오래 잡고 있으면 B는 timeout 후에도 **중복 호출하지 않는다**.

        (계약 변경 — docs/31 C2) 예전엔 timeout 즉시 자기가 호출했다(Y' Hybrid).
        이제는 timeout 에서도 SETNX 재획득을 거쳐, 성공한 하나만 leader 가 된다.
        여기선 A 가 standby 를 계속 들고 있어 B 의 재획득이 실패하므로, B 는 A 를
        기다렸다가 결과를 재사용한다.
        """
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

        assert call_count == 1, f"timeout 후 중복 LLM 호출 발생(herd), 실제={call_count}"

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

        # 불변식 — 위 일곱을 손으로 단언하는 것만으로는 여덟 번째 필드가 생겼을 때 못 잡는다.
        # `_finalize_stream` 도 필드를 손으로 나열하므로, 거기서 빠뜨리면 **캐시에 저장되는
        # 문서에서만 조용히 사라진다**(이슈 #99 와 같은 사고 모양). `_to_stream_chunk` 쪽에는
        # 같은 안전망이 있는데 이쪽에는 없어 대칭이 깨져 있었다 (PR #104 리뷰).
        dropped = [
            f for f in (*STREAM_LIST_FIELDS, *STREAM_CONTAINER_FIELDS)
            if result.get(f) is None
        ]
        assert dropped == [], f"_finalize_stream 이 저장에 옮기지 않은 필드: {dropped}"


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


# ── standby 진입 조건 = save_mode (docs/35 §5-B) ──────────────


class TestStandbyGatedBySaveMode:
    """standby 는 리더가 MDB 에 저장해야 값을 한다.

    `save_mode=False` 면 아무도 저장하지 않으므로 대기자는 기다린 뒤 자기가 다시 LLM 을
    부른다 — 호출 수는 그대로고 지연만 쌓인다. 그래서 진입 조건은 `dc` 존재 여부가 아니라
    `save_mode` 여야 한다. docs/35 §1.2·§3.3.
    """

    @staticmethod
    def _spy_acquire(monkeypatch, acquired):
        """agent_llm standby 선점 시도를 기록한다(원래 동작은 유지)."""
        orig = runner.dedup.try_acquire_standby

        async def spy(prefix, key, ttl):
            acquired.append(prefix)
            return await orig(prefix, key, ttl)

        monkeypatch.setattr(runner.dedup, "try_acquire_standby", spy)

    @staticmethod
    def _stub_call(monkeypatch, counter):
        async def slow_aio_call(**kwargs):
            counter.append(1)
            await asyncio.sleep(0.05)
            return _make_response("no standby")

        monkeypatch.setattr(runner, "aio_call", slow_aio_call)

    # ── §5-6: T/F 는 standby 를 타지 않는다 ──────────────────

    async def test_save_off_skips_standby(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """cache_mode=True, save_mode=False → agent_llm standby 선점 자체를 안 한다."""
        acquired: list[str] = []
        calls: list[int] = []
        self._spy_acquire(monkeypatch, acquired)
        self._stub_call(monkeypatch, calls)

        llm_input = {
            "contents": "standby gated by save_mode",
            "model": "gemini-2.5-flash",
            "task_name": "test",
        }

        async def _run():
            return await runner.llm_request_async(
                llm_input,
                cache_mode=True, save_mode=False,
                standby_ttl_seconds=10, database="test_db",
            )

        await asyncio.gather(_run(), _run())

        assert "agent_llm" not in acquired, (
            "save_mode=False 인데 standby 를 선점했다 — 대기자가 받아갈 저장분이 없어 "
            "기다린 뒤 자기가 다시 LLM 을 부르게 된다(직렬화)"
        )
        assert len(calls) == 2, "둘 다 곧장 LLM 을 불러야 한다"

    # ── §5-7: 스트림도 같다 ─────────────────────────────────

    async def test_stream_save_off_skips_standby(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """가드가 두 함수에 따로 있으므로 스트림도 따로 검증한다."""
        acquired: list[str] = []
        self._spy_acquire(monkeypatch, acquired)

        async def fake_stream(**kwargs):
            await asyncio.sleep(0.05)
            yield StreamChunk(text="ok", finish_reason="STOP")

        monkeypatch.setattr(runner, "aio_stream", fake_stream)

        llm_input = {
            "contents": "stream standby gated by save_mode",
            "model": "gemini-2.5-flash",
            "task_name": "test",
        }

        async def _run():
            chunks, _meta = await runner.llm_request_stream_async(
                llm_input,
                cache_mode=True, save_mode=False,
                standby_ttl_seconds=10, database="test_db",
            )
            async for _chunk in chunks:
                pass

        await asyncio.gather(_run(), _run())

        assert "agent_llm" not in acquired, (
            "스트림 save_mode=False 에서 standby 를 선점했다"
        )

    # ── §5-8: 회귀 — save_mode=True 조합은 그대로 탄다 ───────

    async def test_save_on_still_uses_standby(
        self, mock_collection, fake_redis, fast_backoff, monkeypatch,
    ):
        """T/T·F/T 는 리더가 저장하므로 standby 가 그대로 값을 한다."""
        for cache_mode in (True, False):
            acquired: list[str] = []
            calls: list[int] = []
            self._spy_acquire(monkeypatch, acquired)
            self._stub_call(monkeypatch, calls)

            llm_input = {
                "contents": f"standby stays on {cache_mode}",
                "model": "gemini-2.5-flash",
                "task_name": "test",
            }

            await runner.llm_request_async(
                llm_input,
                cache_mode=cache_mode, save_mode=True,
                standby_ttl_seconds=1, database="test_db",
            )

            assert "agent_llm" in acquired, (
                f"cache_mode={cache_mode}, save_mode=True 인데 standby 를 건너뛰었다"
            )


# ── sync 스트림이 실제 standby 를 푸는가 (docs/36 §5-8) ────────


class TestSyncStreamReleasesStandby:
    """공개 sync 래퍼가 **진짜 두 겹 구조를 지나** 락을 푸는지 본다.

    `tests/test_sync_stream.py` 의 단위 테스트는 `runner.llm_request_stream_async` 를 통째로
    목으로 갈아끼우므로 `_events()` → `_leader_stream` 두 겹을 지나지 않는다. 그래서 여기서
    **실제 runner + fake redis** 로 한 번 더 본다. 단정은 "제너레이터 finally 가 돌았다" 가
    아니라 **standby 키가 실제로 지워졌는가** 다 (docs/36 §4.1 의 교훈).

    ⚠️ **이 테스트만으로는 `_events()` 의 `try/finally` 를 고정하지 못한다.** 실측(돌연변이)으로
    확인했다 — 그 3줄을 지워도 여기는 통과한다. `_SyncStream.close()` 가 `aclose()` 뒤에
    `shutdown_asyncgens()` 를 부르는데 그것이 버려진 안쪽 제너레이터를 대신 닫아 주기 때문이다.
    즉 sync 경로에는 안전망이 둘이다. `_events()` 수정을 고정하는 것은 그 안전망이 없는 **async**
    경로 테스트(`tests/test_sync_stream.py::TestEventsClosePropagates`)다. 여기서는 의도적으로
    **메커니즘이 아니라 결과**(키가 지워졌는가)를 본다.
    """

    def test_close_releases_standby_key(self, mock_collection, fake_redis, monkeypatch):
        import llm_manager
        from llm_manager._internal.file_processing.dedup import _make_standby_key

        monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

        async def slow_stream(**kwargs):
            for i in range(5):
                yield StreamChunk(text=f"c{i}", finish_reason=None)

        monkeypatch.setattr(runner, "aio_stream", slow_stream)

        it, meta = llm_manager.llm_request_stream(
            {
                "contents": "sync stream releases standby",
                "model": "gemini-2.5-flash",
                "task_name": "test",
            },
            cache_mode=True, save_mode=True, database="test_db",
        )
        key = _make_standby_key("agent_llm", meta["cache_key"])

        next(it)   # 스트림 진행 중 — 리더가 락을 쥐고 있어야 한다
        assert fake_redis.get(key) is not None, "스트림 도중인데 standby 가 없다"

        it.close()
        assert fake_redis.get(key) is None, (
            "sync 스트림을 닫았는데 standby 키가 남았다 — 안쪽 finally 가 안 돈 것이다"
        )

    def test_full_drain_releases_standby_key(self, mock_collection, fake_redis, monkeypatch):
        """회귀 — 끝까지 소비하는 정상 경로도 당연히 풀려야 한다."""
        import llm_manager
        from llm_manager._internal.file_processing.dedup import _make_standby_key

        monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

        async def short_stream(**kwargs):
            yield StreamChunk(text="only", finish_reason="STOP")

        monkeypatch.setattr(runner, "aio_stream", short_stream)

        it, meta = llm_manager.llm_request_stream(
            {
                "contents": "sync stream full drain",
                "model": "gemini-2.5-flash",
                "task_name": "test",
            },
            cache_mode=True, save_mode=True, database="test_db",
        )
        key = _make_standby_key("agent_llm", meta["cache_key"])
        events = list(it)

        assert [e["text"] for e in events] == ["only"]
        assert fake_redis.get(key) is None


# ── 캐시 히트 경로 (docs/36 §5-7) ──────────────────────────────


class TestSyncStreamCacheHit:
    """캐시 히트는 chunk 1개짜리 스트림으로 재생된다 — sync 래퍼도 정상 동작해야 한다.

    이 경로에는 standby 토큰이 없다(`_cached_stream`). 그래서 정리 대상 자체가 없지만,
    "chunk 1개 + 즉시 종료" 라는 형태가 루프 회수와 어긋나지 않는지 확인한다.
    """

    def test_cached_replay_through_sync_wrapper(self, mock_collection, fake_redis, monkeypatch):
        import llm_manager

        monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

        calls = {"n": 0}

        async def counted_stream(**kwargs):
            calls["n"] += 1
            yield StreamChunk(text="cached me", finish_reason="STOP")

        monkeypatch.setattr(runner, "aio_stream", counted_stream)
        # 스트림 저장은 handler done event 의 assistant_message 로 이뤄진다 (PR #593 §2).
        # 이것이 없으면 1회차가 저장되지 않아 2회차가 캐시 히트가 안 된다.
        monkeypatch.setattr(runner, "last_assistant_message", lambda: Content.model_validate({
            "role": "model",
            "content": [{"type": "text", "text": "cached me"}],
        }))

        llm_input = {
            "contents": "cache hit through sync stream",
            "model": "gemini-2.5-flash",
            "task_name": "test",
        }

        # 1회차 — live. 저장까지 끝낸다.
        it1, meta1 = llm_manager.llm_request_stream(
            llm_input, cache_mode=True, save_mode=True, database="test_db",
        )
        first = list(it1)
        assert meta1["source"] == "live"
        assert [e["text"] for e in first] == ["cached me"]

        # 2회차 — 같은 입력이면 캐시에서 재생되고 LLM 을 다시 부르지 않는다.
        it2, meta2 = llm_manager.llm_request_stream(
            llm_input, cache_mode=True, save_mode=True, database="test_db",
        )
        second = list(it2)

        assert meta2["source"] == "cache", "두 번째 호출이 캐시를 안 탔다"
        assert calls["n"] == 1, f"캐시 히트인데 LLM 을 다시 불렀다 (호출 {calls['n']}회)"
        assert [e["text"] for e in second] == ["cached me"], "재생 내용이 다르다"
        assert it2._loop.is_closed(), "캐시 히트 경로에서 루프가 회수되지 않았다"
