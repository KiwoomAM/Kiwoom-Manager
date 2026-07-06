"""resolver.py 단위 테스트.

부분 섞기 분류 (classify_files), 비파일 바이트 추정,
resolve_files 의 model None 검증 + 승급 정책 등.

dedup standby 통합 테스트는 test_resolver_standby.py 참고.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_manager._internal.file_processing import resolver
from llm_manager._internal.file_processing.resolver import (
    ResolvedFiles,
    _estimate_non_file_bytes,
    _process_inline,
    _reuse_files_api_uris,
    _upload_and_cache_file,
    classify_files,
    resolve_files,
)
from llm_manager._internal.handler_client.models import CacheRef, FileRef
from llm_manager.exceptions import FileUploadError
from llm_manager.models import FileInput

# ── 헬퍼 ──────────────────────────────────────────────────────


def _file(
    file_name: str = "doc.pdf",
    mime_type: str = "application/pdf",
    file_size: int = 1024,
    extension: str = "pdf",
    page_count: int | None = None,
) -> FileInput:
    """data 는 메모리 부담 줄이려고 항상 1KB 이하 placeholder 사용."""
    return FileInput(
        file_name=file_name,
        file_size=file_size,
        file_mtime=0,
        mime_type=mime_type,
        extension=extension,
        data=b"x",
        page_count=page_count,
    )


class TestEstimateCacheTokens:
    """_estimate_cache_tokens — 모달리티별 합산 토큰 어림 (§4.4.1).

    PDF=258×페이지, 텍스트=file_size//3, system=len(system)//3.
    스코프 외(이미지/오디오 등)·PDF page_count 미상 → None (= skip 안 함).
    """

    def test_pdf_uses_258_per_page(self):
        pdf = _file(mime_type="application/pdf", page_count=2)
        assert resolver._estimate_cache_tokens([pdf], None) == 516

    def test_text_uses_file_size_over_3(self):
        txt = _file(file_name="a.txt", mime_type="text/plain", file_size=900, extension="txt")
        assert resolver._estimate_cache_tokens([txt], None) == 300

    def test_system_instruction_added(self):
        pdf = _file(mime_type="application/pdf", page_count=2)  # 516
        # system 600자 → 600 // 3 = 200
        assert resolver._estimate_cache_tokens([pdf], "x" * 600) == 716

    def test_out_of_scope_modality_returns_none(self):
        img = _file(file_name="i.png", mime_type="image/png", file_size=500)
        assert resolver._estimate_cache_tokens([img], None) is None

    def test_pdf_without_page_count_returns_none(self):
        pdf = _file(mime_type="application/pdf", page_count=None)
        assert resolver._estimate_cache_tokens([pdf], None) is None

    def test_sums_multiple_files(self):
        """여러 파일 합산 — 2장 PDF 2개 = 258×2×2 = 1032 (루프 누적 가드)."""
        pdf_a = _file(mime_type="application/pdf", page_count=2)
        pdf_b = _file(mime_type="application/pdf", page_count=2)
        assert resolver._estimate_cache_tokens([pdf_a, pdf_b], None) == 1032

    def test_any_out_of_scope_returns_none(self):
        """유효 PDF가 섞여 있어도 스코프 외(이미지) 하나면 전체 None (조기 return 가드)."""
        pdf = _file(mime_type="application/pdf", page_count=2)
        img = _file(file_name="i.png", mime_type="image/png", file_size=500)
        assert resolver._estimate_cache_tokens([pdf, img], None) is None


class TestCountPdfPages:
    """_count_pdf_pages — PDF 바이트에서 페이지 수 (fitz). 깨진 입력은 None."""

    def test_counts_real_pdf_pages(self):
        import fitz

        doc = fitz.open()
        for _ in range(3):
            doc.new_page()
        data = doc.tobytes()
        doc.close()
        assert resolver._count_pdf_pages(data) == 3

    def test_invalid_bytes_returns_none(self):
        assert resolver._count_pdf_pages(b"not a pdf at all") is None


# ── classify_files ────────────────────────────────────────────


class TestClassifyFiles:
    def test_small_video_goes_inline(self):
        """20MB 미만 비디오는 인라인 (Gemini 인라인 비디오 지원)."""
        video = _file(file_name="v.mp4", mime_type="video/mp4",
                      file_size=1_000_000, extension="mp4")
        image = _file(file_name="i.png", mime_type="image/png", file_size=1_000_000)
        inline, files_api = classify_files([video, image], llm_input={})
        assert video in inline
        assert image in inline
        assert files_api == []

    def test_large_video_goes_files_api(self):
        """20MB 초과 비디오는 합계 한도 초과로 files_api."""
        video = _file(file_name="big.mp4", mime_type="video/mp4",
                      file_size=25 * 1024 * 1024, extension="mp4")
        inline, files_api = classify_files([video], llm_input={})
        assert video in files_api
        assert inline == []

    def test_pdf_with_small_video_routes_video_inline(self):
        """혼합(§4.3): 소형 비디오가 끼면 한도 20MB → 비디오 inline, 큰 PDF files_api."""
        pdf = _file(file_name="d.pdf", mime_type="application/pdf",
                    file_size=45 * 1024 * 1024)
        video = _file(file_name="v.mp4", mime_type="video/mp4",
                      file_size=10 * 1024 * 1024, extension="mp4")
        inline, files_api = classify_files([pdf, video], llm_input={})
        assert video in inline
        assert pdf in files_api

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
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
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
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        async def fake_create_cache(file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None):
            return CacheRef(name="cachedContents/x", model=model)
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache",
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

    async def test_promotion_mixed_sizes_when_ttl_set(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """context_cache_ttl 지정 + 크기 혼합(작은 것 + 큰 것) → 작은 것도 files_api로 승급.

        classify_files 는 두 30MB PDF를 합계 60MB>50MB 라 하나는 inline, 하나는
        files_api 로 나눈다(부분 섞기). ttl 이 켜지면 캐시는 all-or-nothing 이어야
        하므로 작은 것도 승급돼 inline_pool 이 비어야 한다.
        (docs/30 — 수정 전엔 승급 조건 `not files_api_pool` 때문에 작은 것이 inline 에 잔류)
        """
        small = _file(file_name="a.pdf", file_size=30 * 1024 * 1024)
        large = _file(file_name="b.pdf", file_size=30 * 1024 * 1024)

        upload_count = 0

        async def fake_upload(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            return FileRef(
                name=f"files/x{upload_count}", uri=f"https://example.com/x{upload_count}",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        async def fake_create_cache(file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None):
            return CacheRef(name="cachedContents/x", model=model)
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache",
            fake_create_cache,
        )

        result = await resolve_files(
            [small, large], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None,
            context_cache_ttl=3600,
            standby_ttl_seconds=10,
        )

        # 혼합이어도 전원 승급 — inline 비고 두 파일 모두 files_api(캐시)로
        assert result.inline_parts == []
        assert len(result.uri_parts) == 2
        assert result.cached_content == "cachedContents/x"
        assert upload_count == 2

    async def test_no_promotion_when_ttl_none(self, fake_redis):
        """ttl 없으면 승급 안 함 — 크기 혼합이 그대로(작은 inline / 큰 files_api).

        docs/30 회귀 가드: 승급 조건 완화가 ttl-off 경로로 새지 않아야 한다.
        """
        small = _file(file_name="a.pdf", file_size=30 * 1024 * 1024, page_count=5)
        large = _file(file_name="b.pdf", file_size=30 * 1024 * 1024, page_count=5)

        inline_pool, files_api_pool = await resolver._resolve_pools(
            [small, large], llm_input={}, context_cache_ttl=None,
        )

        assert small in inline_pool
        assert large in files_api_pool


class TestTooSmallGate:
    """too-small 선제 게이트 (§4.4.1) — create 시도 전 모달리티 토큰 어림.

    page_count 기준 추정이 _CACHE_MIN_TOKENS 미만이면 create_context_cache 를
    호출하지 않고 Files API URI 로 폴백한다. 추정 ≥ 최소치면 기존 경로(create 시도).
    """

    @staticmethod
    def _patch_upload_and_cache(monkeypatch):
        """upload_file 모킹 + create_context_cache 호출 여부 추적기 반환."""
        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        calls = {"create": 0}

        async def fake_create_cache(
            file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None,
        ):
            calls["create"] += 1
            return CacheRef(name="cachedContents/x", model=model)
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache",
            fake_create_cache,
        )
        return calls

    async def test_small_pdf_skips_cache_create(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """2장 PDF → 258×2=516 < 1024 → create 미호출, URI 폴백."""
        fi = _file(file_name="small.pdf", file_size=1 * 1024 * 1024, page_count=2)
        calls = self._patch_upload_and_cache(monkeypatch)

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )

        assert calls["create"] == 0  # create 시도 자체를 안 함
        assert result.cached_content is None  # 캐시 없음
        assert result.context_cache_id is None
        assert len(result.uri_parts) == 1  # 그래도 Files API URI 로 전송

    async def test_large_pdf_attempts_cache_create(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """5장 PDF → 258×5=1290 ≥ 1024 → 기존 경로(create 시도)."""
        fi = _file(file_name="big.pdf", file_size=1 * 1024 * 1024, page_count=5)
        calls = self._patch_upload_and_cache(monkeypatch)

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )

        assert calls["create"] == 1
        assert result.cached_content == "cachedContents/x"

    async def test_unknown_page_count_attempts_create(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """page_count 미상(None) → 추정 불가 → 게이트 통과(create 시도, fail-soft 가 backstop)."""
        fi = _file(file_name="unknown.pdf", file_size=1 * 1024 * 1024, page_count=None)
        calls = self._patch_upload_and_cache(monkeypatch)

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )

        assert calls["create"] == 1
        assert result.cached_content == "cachedContents/x"

    async def test_real_pdf_stash_then_gate_skips(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """E2E: 실제 2장 PDF → 업로드가 page_count stash → 게이트가 추정(258×2=516<1024)으로 create skip.

        preset 없이 '업로드 stash → 게이트' 사슬 전체를 검증 (단위 테스트의 인위적 preset 보완).
        """
        import fitz

        doc = fitz.open()
        for _ in range(2):
            doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()

        # page_count 은 preset 하지 않음 — 업로드 경로가 계산해 stash 해야 한다
        fi = _file(
            file_name="real.pdf", mime_type="application/pdf", file_size=len(pdf_bytes)
        )
        fi.data = pdf_bytes
        calls = self._patch_upload_and_cache(monkeypatch)

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )

        assert fi.page_count == 2          # 업로드가 stash 했는지
        assert calls["create"] == 0        # 게이트가 그 값으로 skip
        assert result.cached_content is None
        assert len(result.uri_parts) == 1

    async def test_gate_passes_then_failsoft_absorbs_too_small(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """추정 ≥ 1024 로 게이트 통과 → 그러나 실제 create 가 too-small 400 → fail-soft(§6.2)가 흡수.

        게이트는 과대평가라 '통과시켰지만 실제론 미달'이 가능 → fail-soft 가 backstop 임을 e2e 로 확인.
        """
        from llm_manager._internal.handler_client.exceptions import HandlerError

        fi = _file(file_name="big.pdf", file_size=1 * 1024 * 1024, page_count=5)  # 1290 ≥ 1024 → 통과

        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        created = {"called": False}

        async def fake_create_cache(
            file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None,
        ):
            created["called"] = True
            raise HandlerError(
                "Gemini 캐시 생성 에러: 400 INVALID_ARGUMENT. Cached content is too small. "
                "total_token_count=561, min_total_token_count=1024",
                status_code=400, error_type="provider",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache",
            fake_create_cache,
        )

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )

        assert created["called"] is True      # 게이트 통과 → create 시도함
        assert result.cached_content is None   # fail-soft 가 흡수 (예외 전파 안 됨)
        assert len(result.uri_parts) == 1      # URI 폴백

    async def test_image_out_of_scope_attempts_create(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """이미지(스코프 외) → 추정 None → 게이트 무영향(통과) → create 시도."""
        fi = _file(file_name="i.png", mime_type="image/png", file_size=500)
        calls = self._patch_upload_and_cache(monkeypatch)

        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=3600, standby_ttl_seconds=10,
        )

        assert calls["create"] == 1
        assert result.cached_content == "cachedContents/x"


# ── #2 Phase 1 — Files API 경로 fi.data 회수 ──────────────────


class TestFilesApiBytesRelease:
    """Files API 경로 처리 후 fi.data 가 b'' 로 비워지는지 검증.

    docs/11.minor-features-and-backlog.md 참조.
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
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
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

    async def test_upload_stashes_pdf_page_count(self, fake_redis, monkeypatch):
        """PDF 업로드 시 fi.page_count 가 실제 페이지 수로 채워진다 (게이트용, §4.4.1)."""
        import fitz

        from llm_manager._internal.file_processing import cache_store

        doc = fitz.open()
        for _ in range(2):
            doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()

        fi = _file(
            file_name="p.pdf", mime_type="application/pdf", file_size=len(pdf_bytes)
        )
        fi.data = pdf_bytes

        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        await _upload_and_cache_file(fi, "checksum-pages", [], [])

        assert fi.page_count == 2
        # Redis 에도 저장돼야 HIT/병렬 재호출에서 복원 가능
        saved = cache_store.find_files_api("checksum-pages")
        assert saved is not None and saved.get("page_count") == 2

    def test_reuse_restores_page_count_from_redis(self):
        """HIT 재사용 시 Redis 의 page_count 를 fi 에 복원 (병렬/재호출 게이트용)."""
        fi = _file(file_name="c.pdf", file_size=1024)
        existing = {"uris": ["https://example.com/c"], "page_count": 2}
        _reuse_files_api_uris(fi, existing, [], [])
        assert fi.page_count == 2

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
            "llm_manager._internal.file_processing.resolver.FileManagerHandler",
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
            "llm_manager._internal.file_processing.resolver.fetch_with_fallback",
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
            "llm_manager._internal.file_processing.resolver.fetch_with_fallback",
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
            "llm_manager._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        # fetch_with_fallback mock
        fetch_count = 0

        async def fake_fetch(fm, fi_arg):
            nonlocal fetch_count
            fetch_count += 1
            return b"fetched-bytes"

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.fetch_with_fallback",
            fake_fetch,
        )

        # upload_file mock
        async def fake_upload(path, mime_type):
            return FileRef(
                name="files/x", uri="https://example.com/x",
                mime_type=mime_type, state="ACTIVE",
            )

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
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
            "llm_manager._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        async def fake_fetch(fm, fi_arg):
            return b"\x00\x01"

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.fetch_with_fallback",
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
            "llm_manager._internal.file_processing.resolver.FileManagerHandler",
            lambda: fm_instance,
        )

        async def fake_fetch(fm, fi_arg):
            fi_arg.resolved_via = "fallback"  # fetch_with_fallback 이 갱신한 상황 시뮬
            return b"\x00\x01"

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.fetch_with_fallback",
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
        from llm_manager._internal.handler_client.exceptions import HandlerError

        async def fake_upload(path, mime_type):
            raise HandlerError("Gemini 차단", status_code=422, error_type="content_blocked")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        fi = _file()
        with pytest.raises(HandlerError) as exc_info:
            await _upload_and_cache_file(fi, "checksum-x", [], [])
        assert exc_info.value.error_type == "content_blocked"
        assert exc_info.value.status_code == 422

    async def test_handler_connection_error_passes_through(self, monkeypatch):
        from llm_manager._internal.handler_client.exceptions import HandlerConnectionError

        async def fake_upload(path, mime_type):
            raise HandlerConnectionError()

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        fi = _file()
        with pytest.raises(HandlerConnectionError):
            await _upload_and_cache_file(fi, "checksum-x", [], [])

    async def test_local_error_still_wrapped_as_file_upload_error(self, monkeypatch):
        async def fake_upload(path, mime_type):
            raise OSError("disk full")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

        fi = _file()
        with pytest.raises(FileUploadError):
            await _upload_and_cache_file(fi, "checksum-x", [], [])

    async def test_pdf_page_too_large_passes_through(self, monkeypatch):
        """분할 불가 PDF(PdfPageTooLargeError)는 FileUploadError(502)로 뭉개지 말고
        422 그대로 전파 — 사용자에게 '처리 불가'가 명확히 닿아야 한다."""
        from llm_manager.exceptions import PdfPageTooLargeError

        async def fake_upload_parts(path, target_mb=50):
            raise PdfPageTooLargeError(page=1, size_mb=52.9)

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_pdf_parts",
            fake_upload_parts,
        )

        fi = _file(file_size=60 * 1024 * 1024)  # PDF split 경로 진입
        with pytest.raises(PdfPageTooLargeError) as exc_info:
            await _upload_and_cache_file(fi, "checksum-x", [], [])
        assert exc_info.value.status_code == 422
        assert exc_info.value.error_type == "pdf_page_too_large"


class TestContextCacheTooSmallFailSoft:
    """캐시 생성이 min_total_token_count 미달(400)이면 None 반환(fail-soft),
    그 외 에러는 그대로 raise."""

    def _ref(self):
        return FileRef(name="files/x", uri="https://example.com/x",
                       mime_type="application/pdf", state="ACTIVE")

    async def test_too_small_returns_none_and_skips_redis_save(self, monkeypatch):
        from llm_manager._internal.handler_client.exceptions import HandlerError

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError(
                "Gemini 캐시 생성 에러: 400 INVALID_ARGUMENT. Cached content is too "
                "small. total_token_count=561, min_total_token_count=1024",
                status_code=400, error_type="provider",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache", fake_create,
        )

        save_called = False
        def fake_save(hash_key, doc, ttl):
            nonlocal save_called
            save_called = True
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.cache_store.save_context_cache",
            fake_save,
        )

        result = await resolver._create_and_save_context_cache(
            "a" * 64, ["cs1"], [self._ref()],
            model="gemini-2.5-flash", system_instruction=None, ttl=3600,
        )
        assert result is None
        assert save_called is False

    async def test_other_400_still_raises(self, monkeypatch):
        from llm_manager._internal.handler_client.exceptions import HandlerError

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError("400 INVALID_ARGUMENT: unsupported mime",
                               status_code=400, error_type="provider")
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache", fake_create,
        )

        with pytest.raises(HandlerError):
            await resolver._create_and_save_context_cache(
                "a" * 64, ["cs1"], [self._ref()],
                model="gemini-2.5-flash", system_instruction=None, ttl=3600,
            )

    async def test_503_still_raises(self, monkeypatch):
        from llm_manager._internal.handler_client.exceptions import HandlerError

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError("503 service unavailable",
                               status_code=503, error_type="provider")
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache", fake_create,
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
        # 20MB 초과 비디오 → 합계 한도 초과로 files_api 경로 (split 도 안 탐) — 게이팅 검증용
        return _file(file_name="clip.mp4", mime_type="video/mp4",
                     file_size=25 * 1024 * 1024, extension="mp4")

    def _ok_upload(self, monkeypatch):
        async def fake_upload(path, mime_type):
            return FileRef(name="files/y", uri="https://example.com/y",
                           mime_type=mime_type, state="ACTIVE")
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload,
        )

    async def test_too_small_skip_nulls_context_cache_id(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        from llm_manager._internal.handler_client.exceptions import HandlerError

        self._ok_upload(monkeypatch)

        async def fake_create(file_refs, *, model, system_instruction, ttl,
                              tools=None, tool_config=None):
            raise HandlerError(
                "400 INVALID_ARGUMENT. Cached content is too small. "
                "total_token_count=561, min_total_token_count=1024",
                status_code=400, error_type="provider",
            )
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache", fake_create,
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


def _pdf_bytes(n_pages: int) -> bytes:
    import fitz
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


class TestFilesApiSplitGate:
    """_upload_and_cache_file: >1000p PDF는 upload_pdf_parts(분할)로 라우팅 (변경 C §5.3)."""

    async def test_over_1000_pages_routes_to_split(self, fake_redis, monkeypatch):
        """≤50MB지만 1500p → 단건 upload 아니라 upload_pdf_parts(분할)."""
        fi = _file(mime_type="application/pdf", file_size=1 * 1024 * 1024, page_count=1500)

        split_called = {"n": 0}
        single_called = {"n": 0}

        async def fake_parts(path, *a, **k):
            split_called["n"] += 1
            return [FileRef(name="files/p", uri="https://example.com/p",
                            mime_type="application/pdf", state="ACTIVE")]

        async def fake_single(path, mime_type):
            single_called["n"] += 1
            return FileRef(name="files/s", uri="https://example.com/s",
                           mime_type=mime_type, state="ACTIVE")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_pdf_parts", fake_parts)
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_single)

        await _upload_and_cache_file(fi, "cs-pages", [], [])
        assert split_called["n"] == 1
        assert single_called["n"] == 0


class TestExpandPdfsOverPageLimit:
    """_expand_pdfs_over_page_limit — >1000p PDF를 ≤1000p part FileInput들로 치환 (§5.4)."""

    async def test_over_limit_splits(self):
        data = _pdf_bytes(1100)
        fi = _file(mime_type="application/pdf", file_size=len(data))
        fi.data = data
        out = await resolver._expand_pdfs_over_page_limit([fi])
        assert len(out) == 2
        assert all(o.mime_type == "application/pdf" for o in out)
        assert all(o.page_count is not None and o.page_count <= 1000 for o in out)
        assert sum(o.page_count for o in out) == 1100
        assert all(o.data for o in out)

    async def test_under_limit_unchanged(self):
        data = _pdf_bytes(3)
        fi = _file(mime_type="application/pdf", file_size=len(data))
        fi.data = data
        out = await resolver._expand_pdfs_over_page_limit([fi])
        assert out == [fi]
        assert fi.page_count == 3

    async def test_non_pdf_unchanged(self):
        fi = _file(file_name="i.png", mime_type="image/png", file_size=500)
        out = await resolver._expand_pdfs_over_page_limit([fi])
        assert out == [fi]

    async def test_corrupt_pdf_unchanged(self):
        fi = _file(mime_type="application/pdf", file_size=10)
        fi.data = b"not a pdf"
        out = await resolver._expand_pdfs_over_page_limit([fi])
        assert out == [fi]  # _count_pdf_pages None → 분할 안 함 (fail-soft)

    async def test_uses_existing_page_count_no_reparse(self, monkeypatch):
        """page_count 이미 있으면 _count_pdf_pages 재호출 안 함 (리뷰 ②)."""
        fi = _file(mime_type="application/pdf", file_size=1000, page_count=3)
        fi.data = b"x"
        calls = {"n": 0}

        def fake_count(data):
            calls["n"] += 1
            return 3
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver._count_pdf_pages", fake_count)

        out = await resolver._expand_pdfs_over_page_limit([fi])
        assert calls["n"] == 0  # 이미 stash된 page_count(3) 사용 → 파싱 skip
        assert out == [fi]


class TestInlinePageSplitIntegration:
    """resolve_files: 인라인 >1000p PDF 전개 + Files API 폴백 (§5.4)."""

    async def test_inline_pdf_over_1000p_expands_inline(self, fake_redis):
        """1100p 소형 PDF → 전개된 2파트가 모두 인라인."""
        data = _pdf_bytes(1100)
        fi = _file(mime_type="application/pdf", file_size=len(data))
        fi.data = data
        result = await resolve_files(
            [fi], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=None, standby_ttl_seconds=10,
        )
        assert len(result.inline_parts) == 2
        assert result.uri_parts == []

    async def test_overflow_parts_spill_to_files_api(self, fake_redis, fast_backoff, monkeypatch):
        """전개 파트 합계가 인라인 한도 초과 → 재분류가 files_api로 폴백."""
        big_a = _file(file_name="d.pdf#part1", mime_type="application/pdf",
                      file_size=40 * 1024 * 1024, page_count=500)
        big_b = _file(file_name="d.pdf#part2", mime_type="application/pdf",
                      file_size=40 * 1024 * 1024, page_count=500)

        async def fake_expand(pool):
            return [big_a, big_b]
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver._expand_pdfs_over_page_limit",
            fake_expand)

        async def fake_upload(path, mime_type):
            return FileRef(name="files/x", uri="https://example.com/x",
                           mime_type=mime_type, state="ACTIVE")
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload)

        seed = _file(file_name="d.pdf", mime_type="application/pdf", file_size=1 * 1024 * 1024)
        result = await resolve_files(
            [seed], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=None, standby_ttl_seconds=10,
        )
        # 40+40=80MB > 50MB 한도 → 하나 inline, 하나 files_api 폴백
        assert len(result.inline_parts) == 1
        assert len(result.uri_parts) == 1


class Test404RecoveryExpansion:
    """404 복구가 Step 2.5 전개를 재현해 spilled part checksum까지 무효화 (리뷰 ①)."""

    async def test_recovery_invalidates_spilled_part_checksum(self, monkeypatch):
        from llm_manager._internal.keying import make_file_checksum

        big_a = _file(file_name="d.pdf#part1", mime_type="application/pdf",
                      file_size=40 * 1024 * 1024, page_count=500)
        big_b = _file(file_name="d.pdf#part2", mime_type="application/pdf",
                      file_size=40 * 1024 * 1024, page_count=500)

        async def fake_expand(pool):
            return [big_a, big_b]
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver._expand_pdfs_over_page_limit",
            fake_expand)

        deleted: list[str] = []
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.cache_store.delete_files_api",
            lambda cs: deleted.append(cs))
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.cache_store.delete_context_cache",
            lambda hk: None)

        # 재호출되는 resolve_files는 무력화 — 이 테스트는 '무효화 대상 checksum'만 검증
        async def fake_resolve(*a, **k):
            return ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.resolve_files", fake_resolve)

        seed = _file(file_name="d.pdf", mime_type="application/pdf", file_size=1 * 1024 * 1024)
        await resolver.resolve_files_after_404(
            [seed], llm_input={}, model="gemini-2.5-flash",
            system_instruction=None, context_cache_ttl=None, standby_ttl_seconds=10,
        )

        # spilled part(big_b)의 files_api checksum이 무효화 목록에 있어야 함
        assert make_file_checksum(big_b) in deleted
