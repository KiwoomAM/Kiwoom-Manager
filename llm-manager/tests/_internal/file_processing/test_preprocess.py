"""file_preprocessing (preprocess.py) 단위 테스트 — docs/26 §3.1/§4.2.

파일 전처리 전용 진입점:
- files_api 강제 업로드 → 파일별 (n, ?) URI 그룹
- context cache 모드 → dict {context_cache_id, cached_content_name}
- too-small fail-soft → URI 폴백 / model 누락 → ValueError

files_api 는 Redis 사전 seed 로 HIT 시켜 업로드/fetch 없이 로직만 검증.
context cache 생성은 공유 헬퍼(_resolve_or_create_context_cache) 경계에서 mock.
"""

from __future__ import annotations

import pytest

from llm_manager._internal.file_processing import cache_store, preprocess
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.stored_file_resolver import resolve_stored_files
from llm_manager.models import StoredFile

# ── 헬퍼 ──────────────────────────────────────────────────────


def _sf(file_path: str, file_name: str, size: int = 100) -> StoredFile:
    return StoredFile(
        file_path=file_path,
        file_name=file_name,
        file_size=size,
        file_mtime=1700000000,
        mime_type="application/pdf",
        extension=".pdf",
    )


async def _seed_files_api(sf: StoredFile, uris: list[str]) -> str:
    """sf 의 files_api Redis 레코드를 미리 저장 → checksum 반환 (업로드 우회)."""
    [fi] = await resolve_stored_files([sf])
    cs = make_file_checksum(fi)
    cache_store.save_files_api(cs, {
        "file_checksum": cs,
        "file_name": fi.file_name,
        "file_size": fi.file_size,
        "mime_type": fi.mime_type,
        "uris": uris,
        "split": len(uris) > 1,
    })
    return cs


# ── files_api 모드 ────────────────────────────────────────────


class TestFilePreprocessingFilesApiMode:
    async def test_groups_uris_per_file(self, fake_redis):
        """ctx_ttl 없음 → 파일별 (n, ?) URI 리스트. 분할 파일은 내부 리스트가 여러 개."""
        sf_a = _sf("a.pdf", "a.pdf")
        sf_b = _sf("b.pdf", "b.pdf")
        await _seed_files_api(sf_a, ["u-a1"])
        await _seed_files_api(sf_b, ["u-b1", "u-b2"])

        result = await preprocess.file_preprocessing_async(
            {"model": "gemini-2.5-flash"},
            stored_files=[sf_a, sf_b],
        )
        assert result == [["u-a1"], ["u-b1", "u-b2"]]

    async def test_empty_stored_files_returns_empty(self, fake_redis):
        result = await preprocess.file_preprocessing_async({}, stored_files=[])
        assert result == []


# ── context cache 모드 ────────────────────────────────────────


class TestFilePreprocessingContextCacheMode:
    async def test_returns_both_ids(self, fake_redis, monkeypatch):
        """ctx_ttl 양수 + 생성 성공 → {context_cache_id(hash_key), cached_content_name}."""
        sf = _sf("a.pdf", "a.pdf")
        cs = await _seed_files_api(sf, ["u-a1"])
        expected_hash = cache_store.make_context_cache_key(
            [cs], model="gemini-2.5-flash", system_instruction="s",
            tools=None, tool_config=None,
        )

        async def fake_create(hash_key, checksums, file_refs, **kw):
            return "cachedContents/xyz"
        monkeypatch.setattr(preprocess, "_resolve_or_create_context_cache", fake_create)

        result = await preprocess.file_preprocessing_async(
            {"model": "gemini-2.5-flash", "system": "s"},
            stored_files=[sf],
            context_cache_ttl=1800,
        )
        assert result == {
            "context_cache_id": expected_hash,
            "cached_content_name": "cachedContents/xyz",
        }

    async def test_too_small_falls_back_to_uris(self, fake_redis, monkeypatch):
        """생성이 None(too-small fail-soft) → (n, ?) URI 폴백."""
        sf = _sf("a.pdf", "a.pdf")
        await _seed_files_api(sf, ["u-a1"])

        async def fake_create(*a, **k):
            return None
        monkeypatch.setattr(preprocess, "_resolve_or_create_context_cache", fake_create)

        result = await preprocess.file_preprocessing_async(
            {"model": "gemini-2.5-flash"},
            stored_files=[sf],
            context_cache_ttl=1800,
        )
        assert result == [["u-a1"]]

    async def test_requires_model(self, fake_redis):
        """ctx 모드인데 model 없음 → ValueError (공유 헬퍼 가드)."""
        sf = _sf("a.pdf", "a.pdf")
        await _seed_files_api(sf, ["u-a1"])
        with pytest.raises(ValueError):
            await preprocess.file_preprocessing_async(
                {}, stored_files=[sf], context_cache_ttl=1800,
            )


# ── 공개 래퍼 (llm_manager.file_preprocessing) ────────────────


class TestFilePreprocessingPublicWrapper:
    async def test_delegates_with_resolved_timings(self, monkeypatch):
        """공개 _async 래퍼: ensure_configured 후 timings 해석 → core 로 위임."""
        import llm_manager
        from llm_manager._internal.file_processing import preprocess as pp

        monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)
        captured: dict = {}

        async def fake_core(llm_input, *, stored_files, context_cache_ttl, standby_ttl_seconds):
            captured.update(
                ctt=context_cache_ttl, st=standby_ttl_seconds, sf=stored_files,
            )
            return [["u"]]

        monkeypatch.setattr(pp, "file_preprocessing_async", fake_core)

        result = await llm_manager.file_preprocessing_async(
            {"model": "m"}, stored_files=["X"], timings={"context_cache_ttl": 1800},
        )
        assert result == [["u"]]
        assert captured["ctt"] == 1800
        assert captured["st"] == 600  # _DEFAULT_TIMINGS standby_ttl
        assert captured["sf"] == ["X"]

    def test_exported_in_all(self):
        import llm_manager
        assert "file_preprocessing" in llm_manager.__all__
        assert "file_preprocessing_async" in llm_manager.__all__
