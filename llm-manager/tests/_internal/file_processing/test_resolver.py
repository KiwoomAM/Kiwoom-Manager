"""resolver.py 단위 테스트.

부분 섞기 분류 (classify_files), 비파일 바이트 추정,
resolve_files 의 model None 검증 + 승급 정책 등.

dedup standby 통합 테스트는 test_resolver_standby.py 참고.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_request._internal.file_processing import resolver
from llm_request._internal.file_processing.resolver import (
    ResolvedFiles,
    _estimate_non_file_bytes,
    _process_inline,
    _reuse_files_api_uris,
    _upload_and_cache_file,
    classify_files,
    resolve_files,
)
from llm_request._internal.handler_client.models import CacheRef, FileRef
from llm_request.exceptions import FileUploadError
from llm_request.models import FileInput

# ── 헬퍼 ──────────────────────────────────────────────────────


def _file(
    file_name: str = "doc.pdf",
    mime_type: str = "application/pdf",
    file_size: int = 1024,
    extension: str = "pdf",
) -> FileInput:
    """data 는 메모리 부담 줄이려고 항상 1KB 이하 placeholder 사용."""
    return FileInput(
        file_name=file_name,
        file_size=file_size,
        file_mtime=0,
        mime_type=mime_type,
        extension=extension,
        data=b"x",
    )


# ── classify_files ────────────────────────────────────────────


class TestClassifyFiles:
    def test_video_forced_to_files_api(self):
        """비디오는 inline 불가 — 항상 files_api로."""
        video = _file(file_name="v.mp4", mime_type="video/mp4", file_size=1_000_000)
        image = _file(file_name="i.png", mime_type="image/png", file_size=1_000_000)

        inline, files_api = classify_files([video, image], llm_input={})

        assert video in files_api
        assert image in inline

    def test_image_audio_present_uses_20mb_limit(self):
        """image/audio 가 있으면 inline 합계 한도 20MB로 엄격."""
        image = _file(file_name="i.png", mime_type="image/png", file_size=10 * 1024 * 1024)
        pdf = _file(file_name="d.pdf", mime_type="application/pdf", file_size=15 * 1024 * 1024)
        # 작은 것부터 정렬 → image(10MB) 먼저 inline. pdf(15MB)는 합계 25MB > 20MB → files_api

        inline, files_api = classify_files([image, pdf], llm_input={})

        assert image in inline
        assert pdf in files_api

    def test_pdf_text_only_uses_50mb_limit(self):
        """PDF/text만 있으면 inline 한도 50MB로 완화."""
        pdf_a = _file(file_name="a.pdf", mime_type="application/pdf", file_size=20 * 1024 * 1024)
        pdf_b = _file(file_name="b.pdf", mime_type="application/pdf", file_size=20 * 1024 * 1024)
        # 합계 40MB ≤ 50MB → 둘 다 inline

        inline, files_api = classify_files([pdf_a, pdf_b], llm_input={})

        assert pdf_a in inline
        assert pdf_b in inline
        assert files_api == []

    def test_total_over_limit_pushes_largest_to_files_api(self):
        """합계 초과 시 큰 파일이 files_api로 (작은 것부터 순회 후 한도 넘는 순간 그것만)."""
        small = _file(file_name="s.pdf", mime_type="application/pdf", file_size=10 * 1024 * 1024)
        large = _file(file_name="l.pdf", mime_type="application/pdf", file_size=45 * 1024 * 1024)
        # 정렬 후 small inline → total=10MB. large(+45=55MB) > 50MB → files_api

        inline, files_api = classify_files([small, large], llm_input={})

        assert small in inline
        assert large in files_api

    def test_empty_input(self):
        """빈 입력 → 두 풀 모두 비어."""
        inline, files_api = classify_files([], llm_input={})
        assert inline == []
        assert files_api == []


# ── _estimate_non_file_bytes ──────────────────────────────────


class TestEstimateNonFileBytes:
    def test_normal_dict(self):
        """정상 직렬화 — bytes > 0."""
        size = _estimate_non_file_bytes({"contents": "안녕하세요", "model": "gemini-2.5-flash"})
        assert size > 0

    def test_unserializable_returns_zero(self):
        """직렬화 실패 시 0 반환 (낙관적 fallback). circular ref → ValueError."""
        bad: dict = {}
        bad["self"] = bad
        assert _estimate_non_file_bytes(bad) == 0


# ── _process_inline ───────────────────────────────────────────


class TestProcessInline:
    async def test_returns_part_per_file(self):
        """입력 N개 → Part N개 반환 (Part.from_bytes 변환)."""
        files = [
            _file(file_name="a.png", mime_type="image/png", file_size=100),
            _file(file_name="b.pdf", mime_type="application/pdf", file_size=100),
        ]
        parts = await _process_inline(files)
        assert len(parts) == 2

    async def test_empty_input_returns_empty(self):
        parts = await _process_inline([])
        assert parts == []


# ── resolve_files (통합) ──────────────────────────────────────


class TestResolveFiles:
    """resolve_files 통합 — classify + process + 승급 + model 검증."""

    async def test_empty_files_returns_empty_resolved(self, fake_redis):
        """빈 입력 → ResolvedFiles 모두 비어."""
        result = await resolve_files(
            [], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=None,
        )
        assert result.inline_parts == []
        assert result.uri_parts == []
        assert result.cached_content is None
        assert result.context_cache_id is None

    async def test_model_none_with_context_cache_ttl_raises(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """I-Q7 회귀 방지: context_cache_ttl 지정 + model None → ValueError."""
        # files_api 경로로 가도록 큰 PDF (전원 inline → 승급 정책으로 files_api)
        fi = _file(file_name="ctx.pdf", file_size=30 * 1024 * 1024)

        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        with pytest.raises(ValueError) as exc:
            await resolve_files(
                [fi], llm_input={}, model=None,
                system_instruction=None,
                context_cache_ttl=3600,
                standby_ttl_seconds=10,
            )
        assert "model" in str(exc.value)

    async def test_model_none_without_context_cache_ttl_ok(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """context_cache_ttl 없으면 model None 도 OK — context_cache 분기 자체 미진입."""
        # 작은 PDF → inline pool로 분류 (승급 안 됨)
        fi = _file(file_name="small.pdf", file_size=1 * 1024 * 1024)

        result = await resolve_files(
            [fi], llm_input={}, model=None,
            system_instruction=None,
            context_cache_ttl=None,
            standby_ttl_seconds=10,
        )
        assert len(result.inline_parts) == 1
        assert result.cached_content is None

    async def test_promotion_all_inline_to_files_api_when_ttl_set(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """context_cache_ttl 지정 + 전원 inline 후보 → 전원 files_api로 승급."""
        # 작은 PDF 1개 (그대로면 inline). ttl 줘서 승급 발동
        fi = _file(file_name="small.pdf", file_size=1 * 1024 * 1024)

        upload_count = 0

        async def fake_upload(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            return FileRef(
                name=f"files/x{upload_count}", uri=f"https://example.com/x{upload_count}",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        async def fake_create_cache(file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None):
            return CacheRef(name="cachedContents/x", model=model)
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.create_context_cache",
            fake_create_cache,
        )

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None,
            context_cache_ttl=3600,
            standby_ttl_seconds=10,
        )

        # 승급 확인 — inline 비어야 하고 uri_parts에 들어가야 함
        assert result.inline_parts == []
        assert len(result.uri_parts) == 1
        assert result.cached_content == "cachedContents/x"
        assert result.context_cache_id is not None
        assert len(result.context_cache_id) == 64  # SHA-256 hex
        assert upload_count == 1


# ── #2 Phase 1 — Files API 경로 fi.data 회수 ──────────────────


class TestFilesApiBytesRelease:
    """Files API 경로 처리 후 fi.data 가 b'' 로 비워지는지 검증.

    docs/16.memory-cleanup-plan.md §3.2 / §5.1 참조.
    """

    async def test_live_upload_clears_fi_data(
        self, fake_redis, monkeypatch,
    ):
        """_upload_and_cache_file 종료 후 fi.data == b'' (cache MISS 경로)."""
        fi = _file(file_name="live.pdf", file_size=30 * 1024 * 1024)
        fi.data = b"original-bytes-not-empty"

        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        uri_parts: list = []
        file_refs: list = []
        await _upload_and_cache_file(fi, "checksum-live", uri_parts, file_refs)

        assert fi.data == b""
        assert len(uri_parts) == 1

    def test_cached_reuse_clears_fi_data(self):
        """_reuse_files_api_uris 종료 후 fi.data == b'' (cache HIT 경로)."""
        fi = _file(file_name="cached.pdf", file_size=30 * 1024 * 1024)
        fi.data = b"original-bytes-not-empty"

        existing = {"uris": ["https://example.com/cached-x"]}
        uri_parts: list = []
        file_refs: list = []
        _reuse_files_api_uris(fi, existing, uri_parts, file_refs)

        assert fi.data == b""
        assert len(uri_parts) == 1

    # NOTE: test_upload_with_empty_data_raises (Phase 1 가드) — Phase 3 에서 삭제.
    # Phase 3 의 lazy fetch 가 fi.data 비어있을 때 fetch_with_fallback 으로 채움 → raise 안 함.


class TestProcessInlineLazyFetch:
    """Phase 3: _process_inline 이 fi.data 비어있으면 fetch_with_fallback 호출."""

    async def test_empty_data_triggers_lazy_fetch(self, monkeypatch):
        """fi.data == b"" → fetch_with_fallback 호출 → bytes 채워짐 → Part 생성."""

        fi = _file(file_name="lazy.pdf", file_size=100)
        fi.data = b""
        fi.file_path = "abc.pdf"

        # FileManagerHandler() 도 mock
        fm_instance = MagicMock()
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        # fetch_with_fallback mock
        fetch_count = 0

        async def fake_fetch(fm, fi_arg):
            nonlocal fetch_count
            fetch_count += 1
            assert fm is fm_instance
            assert fi_arg is fi
            return b"\x00\x01\x02"

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.fetch_with_fallback",
            fake_fetch,
        )

        parts = await _process_inline([fi])

        assert fetch_count == 1
        assert fi.data == b"\x00\x01\x02"
        assert len(parts) == 1

    async def test_existing_data_skips_fetch(self, monkeypatch):
        """fi.data 가 채워있으면 (404 복구 경로) fetch 안 함."""

        fi = _file(file_name="filled.pdf", file_size=100)
        fi.data = b"\x10\x20"

        fetch_called = False

        async def fake_fetch(fm, fi_arg):
            nonlocal fetch_called
            fetch_called = True
            return b"shouldnt be called"

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.fetch_with_fallback",
            fake_fetch,
        )

        parts = await _process_inline([fi])

        assert not fetch_called
        assert fi.data == b"\x10\x20"
        assert len(parts) == 1


class TestUploadAndCacheFileLazyFetch:
    """Phase 3: _upload_and_cache_file 의 leader 분기에서 fi.data 비어있으면 fetch."""

    async def test_empty_data_triggers_lazy_fetch(self, fake_redis, monkeypatch):

        fi = _file(file_name="lazy_upload.pdf", file_size=30 * 1024 * 1024)
        fi.data = b""
        fi.file_path = "uuid.pdf"

        # FileManagerHandler() mock
        fm_instance = MagicMock()
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        # fetch_with_fallback mock
        fetch_count = 0

        async def fake_fetch(fm, fi_arg):
            nonlocal fetch_count
            fetch_count += 1
            return b"fetched-bytes"

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.fetch_with_fallback",
            fake_fetch,
        )

        # upload_file mock
        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        uri_parts: list = []
        file_refs: list = []
        await _upload_and_cache_file(fi, "checksum-lazy", uri_parts, file_refs)

        assert fetch_count == 1
        assert len(uri_parts) == 1


class TestResolvedViaPropagation:
    """C2 회귀 보호 — fetch 검증된 분기만 resolved_via='primary' 갱신, 그 외 None 유지."""

    async def test_inline_lazy_fetch_sets_primary(self, monkeypatch):
        """_process_inline 이 fetch 직후 resolved_via=None → 'primary' 로 갱신."""
        fi = _file(file_name="inline.pdf", file_size=100)
        fi.data = b""
        fi.file_path = "abc.pdf"
        assert fi.resolved_via is None

        fm_instance = MagicMock()
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        async def fake_fetch(fm, fi_arg):
            return b"\x00\x01"

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.fetch_with_fallback",
            fake_fetch,
        )

        await _process_inline([fi])
        assert fi.resolved_via == "primary"

    async def test_inline_fallback_preserved(self, monkeypatch):
        """fetch_with_fallback 이 'fallback' 으로 갱신했으면 _process_inline 이 'primary' 로 덮지 않음."""
        fi = _file(file_name="stale.pdf", file_size=100)
        fi.data = b""
        fi.file_path = "old.pdf"

        fm_instance = MagicMock()
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        async def fake_fetch(fm, fi_arg):
            fi_arg.resolved_via = "fallback"  # fetch_with_fallback 이 갱신한 상황 시뮬
            return b"\x00\x01"

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.fetch_with_fallback",
            fake_fetch,
        )

        await _process_inline([fi])
        assert fi.resolved_via == "fallback"

    def test_cache_hit_leaves_resolved_via_none(self):
        """_reuse_files_api_uris (Files API Redis HIT) 분기는 fetch 안 하므로 resolved_via 갱신 안 함."""
        fi = _file(file_name="cached.pdf", file_size=30 * 1024 * 1024)
        fi.data = b"original-bytes"
        assert fi.resolved_via is None

        existing = {"uris": ["https://example.com/cached"]}
        _reuse_files_api_uris(fi, existing, [], [])

        assert fi.resolved_via is None  # 검증되지 않음 — silent 'primary' 박제 방지


# ── ResolvedFiles ─────────────────────────────────────────────


def test_resolved_files_has_context_cache_id_field():
    rf = ResolvedFiles(
        inline_parts=[],
        uri_parts=[],
        cached_content="cachedContents/xxx",
        context_cache_id="abc123",
    )
    assert rf.context_cache_id == "abc123"


def test_resolved_files_context_cache_id_defaults_none():
    rf = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
    assert rf.context_cache_id is None


# ── llm-handler 에러 pass-through (FileUploadError 로 변형 금지) ────


class TestUploadErrorPassthrough:
    """upload_file 가 던진 llm-handler 에러는 분류 보존하며 그대로 전파,
    진짜 로컬 실패만 FileUploadError 로 wrap."""

    async def test_handler_error_passes_through(self, monkeypatch):
        from llm_request._internal.handler_client.exceptions import HandlerError

        async def fake_upload(path, mime_type):
            raise HandlerError("Gemini 차단", status_code=422, error_type="content_blocked")

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        fi = _file()
        with pytest.raises(HandlerError) as exc_info:
            await _upload_and_cache_file(fi, "checksum-x", [], [])
        assert exc_info.value.error_type == "content_blocked"
        assert exc_info.value.status_code == 422

    async def test_handler_connection_error_passes_through(self, monkeypatch):
        from llm_request._internal.handler_client.exceptions import HandlerConnectionError

        async def fake_upload(path, mime_type):
            raise HandlerConnectionError()

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        fi = _file()
        with pytest.raises(HandlerConnectionError):
            await _upload_and_cache_file(fi, "checksum-x", [], [])

    async def test_local_error_still_wrapped_as_file_upload_error(self, monkeypatch):
        async def fake_upload(path, mime_type):
            raise OSError("disk full")

        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

        fi = _file()
        with pytest.raises(FileUploadError):
            await _upload_and_cache_file(fi, "checksum-x", [], [])


class TestContextCacheTooSmallFailSoft:
    """캐시 생성이 min_total_token_count 미달(400)이면 None 반환(fail-soft),
    그 외 에러는 그대로 raise."""

    def _ref(self):
        return FileRef(name="files/x", uri="https://example.com/x",
                       mime_type="application/pdf", state="ACTIVE")

    async def test_too_small_returns_none_and_skips_redis_save(self, monkeypatch):
        from llm_request._internal.handler_client.exceptions import HandlerError

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError(
                "Gemini 캐시 생성 에러: 400 INVALID_ARGUMENT. Cached content is too "
                "small. total_token_count=561, min_total_token_count=1024",
                status_code=400, error_type="provider",
            )
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.create_context_cache", fake_create,
        )

        save_called = False
        def fake_save(hash_key, doc, ttl):
            nonlocal save_called
            save_called = True
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.cache_store.save_context_cache",
            fake_save,
        )

        result = await resolver._create_and_save_context_cache(
            "a" * 64, ["cs1"], [self._ref()],
            model="gemini-2.5-flash", system_instruction=None, ttl=3600,
        )
        assert result is None
        assert save_called is False

    async def test_other_400_still_raises(self, monkeypatch):
        from llm_request._internal.handler_client.exceptions import HandlerError

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError("400 INVALID_ARGUMENT: unsupported mime",
                               status_code=400, error_type="provider")
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.create_context_cache", fake_create,
        )

        with pytest.raises(HandlerError):
            await resolver._create_and_save_context_cache(
                "a" * 64, ["cs1"], [self._ref()],
                model="gemini-2.5-flash", system_instruction=None, ttl=3600,
            )

    async def test_503_still_raises(self, monkeypatch):
        from llm_request._internal.handler_client.exceptions import HandlerError

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError("503 service unavailable",
                               status_code=503, error_type="provider")
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.create_context_cache", fake_create,
        )

        with pytest.raises(HandlerError):
            await resolver._create_and_save_context_cache(
                "a" * 64, ["cs1"], [self._ref()],
                model="gemini-2.5-flash", system_instruction=None, ttl=3600,
            )


class TestContextCacheIdGating:
    """context_cache_id 는 실제 캐시가 있을 때(cached_content not None)만 노출.
    캐시 미생성(too-small fail-soft / ttl 미지정)이면 None — 삭제할 캐시 없음."""

    def _video(self):
        # 비디오는 크기 무관 항상 files_api 경로 (split 도 안 탐) — 게이팅 검증용
        return _file(file_name="clip.mp4", mime_type="video/mp4",
                     file_size=5 * 1024 * 1024, extension="mp4")

    def _ok_upload(self, monkeypatch):
        async def fake_upload(path, mime_type):
            return FileRef(name="files/y", uri="https://example.com/y",
                           mime_type=mime_type, state="ACTIVE")
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.upload_file", fake_upload,
        )

    async def test_too_small_skip_nulls_context_cache_id(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        from llm_request._internal.handler_client.exceptions import HandlerError

        self._ok_upload(monkeypatch)

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError(
                "400 INVALID_ARGUMENT. Cached content is too small. "
                "total_token_count=561, min_total_token_count=1024",
                status_code=400, error_type="provider",
            )
        monkeypatch.setattr(
            "llm_request._internal.file_processing.resolver.create_context_cache", fake_create,
        )

        result = await resolve_files(
            [self._video()], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )
        assert result.cached_content is None
        assert result.context_cache_id is None  # 캐시 없으니 id도 없음
        assert len(result.uri_parts) == 1       # Files API URI 로 전송

    async def test_no_ttl_files_api_has_no_context_cache_id(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        self._ok_upload(monkeypatch)

        result = await resolve_files(
            [self._video()], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=None, standby_ttl_seconds=10,
        )
        assert result.cached_content is None
        assert result.context_cache_id is None
        assert len(result.uri_parts) == 1
