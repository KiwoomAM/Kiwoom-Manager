"""runner._apply_resolved 단위 테스트.

로직 설명은 _apply_resolved docstring 참고.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from llm_manager._internal.file_processing.resolver import ResolvedFiles
from llm_manager._internal.handler_client.models import LLMResponse, Part
from llm_manager._internal.runner import _apply_resolved, _build_server_call_input, _Prepared
from llm_manager.models import FileInput

# ── 헬퍼 ──────────────────────────────────────────────────────


def _make_inline_part() -> Part:
    return Part.from_bytes(data=b"x", mime_type="image/png")


def _make_uri_part(uri: str = "https://example.com/test.pdf") -> Part:
    return Part.from_uri(uri, mime_type="application/pdf")


# ── cached_content 없는 경로 ──────────────────────────────────


class TestApplyResolvedWithoutContextCache:
    """기본 경로 — cached_content 없음. 모든 parts 가 contents 에 합쳐짐."""

    def test_inline_and_uri_parts_both_injected_into_contents(self):
        resolved = ResolvedFiles(
            inline_parts=[_make_inline_part()],
            uri_parts=[_make_uri_part()],
            cached_content=None,
        )
        llm_input = {"contents": "분석해줘", "model": "gemini-2.5-flash"}

        result = _apply_resolved(llm_input, resolved)

        # inline + uri + text 순서로 합쳐짐
        assert len(result["contents"]) == 3
        assert result["contents"][-1] == "분석해줘"
        assert "cached_content" not in result

    def test_no_parts_keeps_only_text(self):
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        llm_input = {"contents": "텍스트만"}

        result = _apply_resolved(llm_input, resolved)

        assert result["contents"] == ["텍스트만"]

    def test_system_and_tools_preserved_when_no_cache(self):
        """cached_content 없으면 system/tools/tool_config 는 그대로 보존."""
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[_make_uri_part()],
            cached_content=None,
        )
        llm_input = {
            "contents": "x",
            "system": "너는 금융 전문가",
            "tools": [{"name": "calculator"}],
            "tool_config": "auto",
        }

        result = _apply_resolved(llm_input, resolved)

        assert result["system"] == "너는 금융 전문가"
        assert result["tools"] == [{"name": "calculator"}]
        assert result["tool_config"] == "auto"


# ── cached_content 있는 경로 — 중복 제거 ──────────────────────


class TestApplyResolvedWithContextCache:
    """context cache 사용 경로 — 중복 필드 제거가 핵심."""

    def test_cached_content_set_and_uri_parts_excluded_from_contents(self):
        """cached_content 있으면 uri_parts 는 cache 안에 있으니 contents 에서 제외."""
        resolved = ResolvedFiles(
            inline_parts=[_make_inline_part()],
            uri_parts=[_make_uri_part()],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "분석해줘", "model": "gemini-2.5-flash"}

        result = _apply_resolved(llm_input, resolved)

        assert result["cached_content"] == "cachedContents/abc"
        # inline_parts + text 만 (uri_parts 제외)
        assert len(result["contents"]) == 2
        assert result["contents"][-1] == "분석해줘"

    def test_cached_content_removes_system_tools_tool_config(self):
        """cached_content 있으면 system/tools/tool_config 제거 (cache 에 이미 포함)."""
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[_make_uri_part()],
            cached_content="cachedContents/abc",
        )
        llm_input = {
            "contents": "x",
            "model": "gemini-2.5-flash",
            "system": "너는 금융 전문가",
            "tools": [{"name": "calculator"}],
            "tool_config": "auto",
        }

        result = _apply_resolved(llm_input, resolved)

        assert "system" not in result
        assert "tools" not in result
        assert "tool_config" not in result
        assert result["cached_content"] == "cachedContents/abc"
        assert result["model"] == "gemini-2.5-flash"  # model 은 유지

    def test_cached_content_preserves_inline_parts(self):
        """inline_parts 는 cache 에 안 들어가므로 cached_content 여도 유지."""
        inline = _make_inline_part()
        resolved = ResolvedFiles(
            inline_parts=[inline],
            uri_parts=[_make_uri_part()],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "질문"}

        result = _apply_resolved(llm_input, resolved)

        assert inline in result["contents"]
        assert "질문" in result["contents"]


# ── 공통: file_checksums 제거 ─────────────────────────────────


class TestApplyResolvedCommon:
    def test_file_checksums_always_removed(self):
        """cached_content 유무 관계없이 file_checksums 는 제거."""
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        llm_input = {"contents": "x", "file_checksums": ["abc123"]}

        result = _apply_resolved(llm_input, resolved)

        assert "file_checksums" not in result

    def test_file_checksums_removed_with_cache(self):
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "x", "file_checksums": ["abc123"]}

        result = _apply_resolved(llm_input, resolved)

        assert "file_checksums" not in result


# ── 순서 invariant ────────────────────────────────────────────


class TestApplyResolvedOrder:
    """docstring 주장: inline_parts + uri_parts + text_contents 순서.

    len() / contents[-1] 만 확인하던 기존 테스트를 보강 — 각 위치의 객체까지 어서션.
    """

    def test_order_without_cache_is_inline_then_uri_then_text(self):
        inline1 = Part.from_bytes(data=b"a", mime_type="image/png")
        inline2 = Part.from_bytes(data=b"b", mime_type="image/jpeg")
        uri1 = Part.from_uri("https://example.com/a.pdf", mime_type="application/pdf")
        uri2 = Part.from_uri("https://example.com/b.pdf", mime_type="application/pdf")
        resolved = ResolvedFiles(
            inline_parts=[inline1, inline2],
            uri_parts=[uri1, uri2],
            cached_content=None,
        )
        llm_input = {"contents": "질문"}

        result = _apply_resolved(llm_input, resolved)

        # 정확히 inline1, inline2, uri1, uri2, "질문" 순서
        assert len(result["contents"]) == 5
        assert result["contents"][0] is inline1
        assert result["contents"][1] is inline2
        assert result["contents"][2] is uri1
        assert result["contents"][3] is uri2
        assert result["contents"][4] == "질문"

    def test_order_with_cache_excludes_uri_parts_preserves_inline_text(self):
        """cached_content 있으면 uri_parts 제외 — inline → text 순서만 남음."""
        inline1 = Part.from_bytes(data=b"a", mime_type="image/png")
        uri1 = Part.from_uri("https://example.com/a.pdf", mime_type="application/pdf")
        resolved = ResolvedFiles(
            inline_parts=[inline1],
            uri_parts=[uri1],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "질문"}

        result = _apply_resolved(llm_input, resolved)

        assert len(result["contents"]) == 2
        assert result["contents"][0] is inline1
        assert result["contents"][1] == "질문"


# ── mutation 안전성 ──────────────────────────────────────────


class TestApplyResolvedMutationSafety:
    """_apply_resolved 는 원본 `llm_input` dict 를 변경하지 않아야 한다.

    runner._do_llm_call 의 404-retry 경로에서 `prep.llm_input` 을 재사용하므로
    1차 호출이 원본을 훼손하면 retry 가 깨진다. `dict(llm_input)` 이 shallow copy
    이므로 최소한 "키 제거" / "값 재할당" 이 원본에 전파되지 않는지 검증.
    """

    def test_original_llm_input_unchanged_without_cache(self):
        original = {
            "contents": "분석",
            "system": "system text",
            "tools": [{"name": "calc"}],
            "file_checksums": ["abc"],
            "model": "gemini-2.5-flash",
        }
        snapshot = {k: v for k, v in original.items()}
        resolved = ResolvedFiles(
            inline_parts=[Part.from_bytes(data=b"x", mime_type="image/png")],
            uri_parts=[],
            cached_content=None,
        )

        _apply_resolved(original, resolved)

        # 원본 dict 의 키·값 그대로 유지
        assert original == snapshot
        assert "file_checksums" in original  # call_input 에서만 pop, 원본엔 남음

    def test_original_llm_input_unchanged_with_cache(self):
        """cache 경로에서 system / tools / tool_config / file_checksums 를 pop 해도 원본 dict 는 보존."""
        original = {
            "contents": "분석",
            "system": "system text",
            "tools": [{"name": "calc"}],
            "tool_config": "auto",
            "file_checksums": ["abc"],
            "model": "gemini-2.5-flash",
        }
        snapshot = {k: v for k, v in original.items()}
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[Part.from_uri("https://example.com/x.pdf", mime_type="application/pdf")],
            cached_content="cachedContents/abc",
        )

        _apply_resolved(original, resolved)

        assert original == snapshot
        assert "system" in original
        assert "tools" in original
        assert "tool_config" in original


# ── call-site wiring ─────────────────────────────────────────


class TestBuildServerCallInput:
    """_build_server_call_input 이 _apply_resolved 를 거쳐 call_input 을 구성하는지.

    runner 의 _do_llm_call / _do_llm_stream / 404-retry 경로 모두 이 함수 또는
    _apply_resolved 를 직접 거치므로, wiring 테스트로 3개 호출부의 기본 동작을
    한 군데서 보장.
    """

    async def test_no_files_returns_llm_input_copy(self):
        """files=None → llm_input 을 그대로 복사해 반환 (shallow, 원본 공유 X)."""
        prep = _Prepared(
            llm_input={"contents": "x", "model": "gemini-2.5-flash"},
            meta={},
            cache_mode=False,
            save_mode=False,
            ttl_seconds=None,
            requested_at=datetime.now(UTC),
            dc=None,
            cached_output=None,
            route_reason=None,
            files=None,
            context_cache_ttl=None,
            standby_ttl_seconds=600,
        )

        result = await _build_server_call_input(prep)

        assert result == {"contents": "x", "model": "gemini-2.5-flash"}
        assert result is not prep.llm_input  # shallow copy

    async def test_with_files_routes_through_apply_resolved(self, monkeypatch):
        """files 있을 때 resolve_files 결과가 _apply_resolved 를 거쳐 나옴.

        resolve_files 를 가짜로 두고 cached_content 가 세팅된 ResolvedFiles 를 반환 →
        결과 call_input 에 cached_content 세팅 + system 제거 + contents 에 text 유지 확인.
        """
        fake_resolved = ResolvedFiles(
            inline_parts=[Part.from_bytes(data=b"x", mime_type="image/png")],
            uri_parts=[Part.from_uri("https://example.com/a.pdf", mime_type="application/pdf")],
            cached_content="cachedContents/abc",
        )

        async def _fake_resolve_files(*args, **kwargs):
            return fake_resolved

        monkeypatch.setattr("llm_manager._internal.runner.resolve_files", _fake_resolve_files)

        prep = _Prepared(
            llm_input={
                "contents": "질문",
                "model": "gemini-2.5-flash",
                "system": "should be stripped by cache path",
            },
            meta={},
            cache_mode=True,
            save_mode=True,
            ttl_seconds=None,
            requested_at=datetime.now(UTC),
            dc=None,
            cached_output=None,
            route_reason=None,
            files=[
                FileInput(
                    file_name="a.png",
                    file_size=100,
                    file_mtime=1,
                    mime_type="image/png",
                    extension="png",
                    data=b"x",
                )
            ],
            context_cache_ttl=3600,
            standby_ttl_seconds=600,
        )

        result = await _build_server_call_input(prep)

        # _apply_resolved cache 경로 검증
        assert result["cached_content"] == "cachedContents/abc"
        assert "system" not in result
        # uri_parts 는 제외되지만 inline_part + text 는 포함
        assert len(result["contents"]) == 2
        assert result["contents"][-1] == "질문"


# ── contents 타입 가드 ────────────────────────────────────────


class TestApplyResolvedContentsTypeGuard:
    """contents 가 str / list 가 아니면 명시적 `TypeError`.

    이전엔 `else: text_contents = []` 로 silent drop — caller 의 입력 오류(오타 등)가
    조용히 덮여 LLM 호출이 빈 텍스트로 나가 디버깅이 어려웠음. fail-fast 로 교정.
    """

    def test_dict_contents_raises(self):
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        with pytest.raises(TypeError, match="str 또는 list"):
            _apply_resolved({"contents": {"text": "hi"}}, resolved)

    def test_none_contents_raises(self):
        """key 자체가 없는 경우는 `get(..., "")` 로 빈 문자열 fallback 이지만,
        명시적으로 None 을 넘기면 실수로 간주해 raise."""
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        with pytest.raises(TypeError, match="str 또는 list"):
            _apply_resolved({"contents": None}, resolved)

    def test_int_contents_raises(self):
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        with pytest.raises(TypeError, match="str 또는 list"):
            _apply_resolved({"contents": 123}, resolved)

    def test_bytes_contents_raises(self):
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        with pytest.raises(TypeError, match="str 또는 list"):
            _apply_resolved({"contents": b"raw bytes"}, resolved)

    def test_missing_contents_key_uses_empty_string_fallback(self):
        """contents key 가 없으면 `get(..., "")` 로 빈 문자열 → str 경로라 통과 (silent OK).

        이건 의도적 — key 누락은 흔한 케이스이고 빈 텍스트로 처리하는 게 합리적.
        타입 가드는 "잘못된 타입이 들어왔을 때" 만 담당.
        """
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        result = _apply_resolved({"model": "gemini-2.5-flash"}, resolved)
        assert result["contents"] == []


# ── Phase 1 — file_meta 응답 노출 (live 경로) ──────────────────


class TestBuildLiveMetaFileMeta:
    """_build_live_meta 가 file_meta 키를 조건부로 노출하는지."""

    def _make_prep(self, *, files):
        from llm_manager._internal.done_check import DoneCheckResult
        from llm_manager._internal.runner import _Prepared

        return _Prepared(
            llm_input={"contents": "test", "model": "gemini-2.5-flash"},
            meta={"service_name": "test", "model": "gemini-2.5-flash"},
            cache_mode=True, save_mode=True,
            ttl_seconds=None,
            requested_at=datetime.now(UTC),
            dc=DoneCheckResult(
                collection="agent_llm",
                cache_key="k" * 64,
                canonical_json="{}",
                cached=None,
                cache_error=False,
            ),
            cached_output=None,
            route_reason="cache_miss",
            files=files,
            context_cache_ttl=None,
            standby_ttl_seconds=600,
        )

    def test_files_none_omits_file_meta_key(self):
        from llm_manager._internal.runner import _build_live_meta

        prep = self._make_prep(files=None)
        meta = _build_live_meta(prep)

        assert "file_meta" not in meta

    def test_files_present_exposes_file_path(self):
        from llm_manager._internal.runner import _build_live_meta
        from llm_manager.models import FileInput

        fi = FileInput(
            file_name="report.pdf",
            file_size=12345,
            file_mtime=1700000000,
            mime_type="application/pdf",
            extension=".pdf",
            data=b"\x00\x01\x02",
            file_path="abc-uuid.pdf",  # register_files 가 채운 상태 시뮬레이션
        )
        prep = self._make_prep(files=[fi])

        meta = _build_live_meta(prep)

        assert "file_meta" in meta
        assert meta["file_meta"][0]["file_path"] == "abc-uuid.pdf"
        assert "data" not in meta["file_meta"][0]  # bytes 제외


# ── context_cache_id 응답 노출 (live 경로) ──────────────────────


class TestBuildLiveMetaContextCacheId:
    """_build_live_meta 가 context_cache_id 키를 조건부로 노출하는지."""

    def _make_prep(self, *, context_cache_id):
        from llm_manager._internal.done_check import DoneCheckResult
        from llm_manager._internal.runner import _Prepared

        return _Prepared(
            llm_input={"contents": "test", "model": "gemini-2.5-flash"},
            meta={"service_name": "test", "model": "gemini-2.5-flash"},
            cache_mode=True, save_mode=True,
            ttl_seconds=None,
            requested_at=datetime.now(UTC),
            dc=DoneCheckResult(
                collection="agent_llm",
                cache_key="k" * 64,
                canonical_json="{}",
                cached=None,
                cache_error=False,
            ),
            cached_output=None,
            route_reason="cache_miss",
            files=None,
            context_cache_ttl=None,
            standby_ttl_seconds=600,
            context_cache_id=context_cache_id,
        )

    def test_none_omits_context_cache_id_key(self):
        """ResolvedFiles 가 context_cache_id 를 set 하지 않으면 응답 키 부재."""
        from llm_manager._internal.runner import _build_live_meta

        prep = self._make_prep(context_cache_id=None)
        meta = _build_live_meta(prep)

        assert "context_cache_id" not in meta

    def test_present_exposes_context_cache_id(self):
        """Context Cache 생성/HIT 시 hash_key 가 meta 에 노출."""
        from llm_manager._internal.runner import _build_live_meta

        hash_key = "a" * 64
        prep = self._make_prep(context_cache_id=hash_key)
        meta = _build_live_meta(prep)

        assert meta["context_cache_id"] == hash_key


# ── Phase 1 — file_meta 응답 노출 (cache hit 경로) ──────────────


class TestPrepareCachedMetaFileMeta:
    """prepare()의 cache hit 분기가 doc 의 file_meta 를 cached_meta 에 노출하는지."""

    def test_cached_doc_with_file_meta_exposed(self, monkeypatch):
        from llm_manager._internal import runner
        from llm_manager._internal.done_check import DoneCheckResult

        cached_doc = {
            "result": {
                "text": "cached!",
                "model": "gemini-2.5-flash",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "finish_reason": "STOP",
                "request_id": "r",
            },
            "metadata": {"service_name": "t", "model": "gemini-2.5-flash"},
            "file_meta": [{
                "file_name": "x.pdf", "file_size": 1, "file_mtime": 0,
                "mime_type": "application/pdf", "extension": ".pdf",
                "file_path": "abc-uuid.pdf",
            }],
        }

        def fake_done_check(llm_input, *, lookup_cache=True, database=None):
            return DoneCheckResult(
                collection="agent_llm",
                cache_key="k" * 64,
                canonical_json="{}",
                cached=cached_doc,
                cache_error=False,
            )

        monkeypatch.setattr(runner, "done_check", fake_done_check)

        prep = runner.prepare(
            {"contents": "test", "model": "gemini-2.5-flash"},
            cache_mode=True, save_mode=False,
        )

        assert prep.cached_output is not None
        _, meta = prep.cached_output
        assert "file_meta" in meta
        assert meta["file_meta"][0]["file_path"] == "abc-uuid.pdf"

    def test_legacy_cached_doc_without_file_meta_omits_key(self, monkeypatch):
        """Phase 1 적용 전 저장된 옛 doc — file_meta 키 자체 없음 → 응답에도 키 없음."""
        from llm_manager._internal import runner
        from llm_manager._internal.done_check import DoneCheckResult

        cached_doc = {
            "result": {
                "text": "cached", "model": "gemini-2.5-flash",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "finish_reason": "STOP", "request_id": "r",
            },
            "metadata": {"service_name": "t", "model": "gemini-2.5-flash"},
            # file_meta 키 의도적 부재
        }

        def fake_done_check(llm_input, *, lookup_cache=True, database=None):
            return DoneCheckResult(
                collection="agent_llm",
                cache_key="k" * 64,
                canonical_json="{}",
                cached=cached_doc,
                cache_error=False,
            )

        monkeypatch.setattr(runner, "done_check", fake_done_check)

        prep = runner.prepare(
            {"contents": "test", "model": "gemini-2.5-flash"},
            cache_mode=True, save_mode=False,
        )

        assert prep.cached_output is not None
        _, meta = prep.cached_output
        assert "file_meta" not in meta


# ── Phase 2 — _prepare_files 헬퍼 (files + stored_files) ────────


class TestPrepareFiles:
    """Phase 3: _prepare_files 가 stored_files 만 받음 (Method 1 폐기)."""

    async def test_stored_files_only(self, monkeypatch):
        """stored_files=[..] → resolve_stored_files 호출 → placeholder list 반환."""
        from llm_manager._internal import runner
        from llm_manager.models import FileInput, StoredFile

        async def fake_resolve(refs):
            return [FileInput(
                file_name="y.pdf", file_size=2, file_mtime=0,
                mime_type="application/pdf", extension=".pdf",
                data=b"",
                file_path="primary-uuid.pdf",
                resolved_via="primary",
            )]

        monkeypatch.setattr(runner, "resolve_stored_files", fake_resolve)

        refs = [StoredFile(
            file_path="primary-uuid.pdf",
            file_name="y.pdf", file_size=2, file_mtime=0,
            mime_type="application/pdf", extension=".pdf",
        )]

        combined = await runner._prepare_files(refs)

        assert combined is not None
        assert len(combined) == 1
        assert combined[0].resolved_via == "primary"

    async def test_none_returns_none(self):
        """stored_files=None → combined=None (파일 없는 호출)."""
        from llm_manager._internal import runner

        combined = await runner._prepare_files(None)
        assert combined is None


# ── _to_stream_chunk (캐시 히트 시 LLMResponse → StreamChunk 변환) ──


def test_to_stream_chunk_preserves_url_context():
    """캐시 히트로 재생되는 스트림에서도 url_context 가 grounding 처럼 보존된다."""
    from llm_manager._internal.handler_client.models import UrlContextMetadata, UrlMetadata
    from llm_manager._internal.runner import _to_stream_chunk

    response = LLMResponse(
        text="기사 요약입니다.",
        model="gemini-3-flash-preview",
        request_id="req_cache1",
        url_context=UrlContextMetadata(
            url_metadata=[
                UrlMetadata(
                    retrieved_url="https://example.com/a",
                    url_retrieval_status="URL_RETRIEVAL_STATUS_SUCCESS",
                )
            ]
        ),
    )

    chunk = _to_stream_chunk(response)

    assert chunk.url_context is not None
    assert chunk.url_context.url_metadata[0].retrieved_url == "https://example.com/a"
