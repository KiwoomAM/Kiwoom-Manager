"""file_handler/resolver.py 테스트.

uploader는 mock, cache_store는 메모리 fallback으로 동작.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import fitz
import pytest

from llm_handler.models import CacheRef, FileRef

from llm_request.file_handler import cache_store
from llm_request.file_handler.resolver import resolve_files


# ── 헬퍼 ─────────────────────────────────────────────────────


def _create_file(path: str, size_bytes: int, mime: str = "application/pdf") -> str:
    """지정 크기의 더미 파일 생성."""
    with open(path, "wb") as f:
        f.write(b"x" * size_bytes)
    return path


def _create_small_pdf(path: str, num_pages: int = 3) -> str:
    """작은 PDF 생성 (inline 대상)."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    doc.save(path)
    doc.close()
    return path


def _fake_file_ref(uri: str = "https://example.com/files/abc") -> FileRef:
    return FileRef(name="files/abc", uri=uri, mime_type="application/pdf", state="ACTIVE")


def _fake_cache_ref(name: str = "cachedContents/xyz") -> CacheRef:
    return CacheRef(name=name, model="gemini-2.5-flash")


def _meta(checksum: str, filename: str, size: int, mime: str) -> dict:
    return {"checksum": checksum, "filename": filename, "size": size, "mime_type": mime}


# ── inline (≤20MB) ───────────────────────────────────────────


class TestInline:

    @pytest.mark.asyncio
    async def test_small_file_uses_inline(self, tmp_path):
        """≤20MB 파일은 inline Part로 처리."""
        pdf_path = _create_small_pdf(str(tmp_path / "small.pdf"))
        size = os.path.getsize(pdf_path)
        meta = _meta("sha256:small", "small.pdf", size, "application/pdf")

        resolved = await resolve_files([meta], [pdf_path])

        assert resolved.cached_content is None
        assert len(resolved.contents_parts) == 1
        assert resolved.contents_parts[0].type == "inline"


# ── Files API (>20MB) ────────────────────────────────────────


class TestFilesApi:

    @pytest.mark.asyncio
    async def test_large_file_uploads(self, tmp_path):
        """>20MB 파일은 Files API 업로드."""
        file_path = _create_file(str(tmp_path / "large.bin"), 25 * 1024 * 1024)
        meta = _meta("sha256:large", "large.bin", 25 * 1024 * 1024, "application/octet-stream")

        with patch(
            "llm_request.file_handler.uploader._aio_upload",
            new_callable=AsyncMock,
            return_value=_fake_file_ref(),
        ):
            resolved = await resolve_files([meta], [file_path])

        assert resolved.cached_content is None
        assert len(resolved.contents_parts) == 1
        assert resolved.contents_parts[0].type == "file"

    @pytest.mark.asyncio
    async def test_files_api_redis_cache_hit(self, tmp_path):
        """Redis에 기존 URI가 있으면 업로드 스킵."""
        file_path = _create_file(str(tmp_path / "cached.bin"), 25 * 1024 * 1024)
        meta = _meta("sha256:cached", "cached.bin", 25 * 1024 * 1024, "application/octet-stream")

        # Redis에 미리 저장
        cache_store.save_files_api("sha256:cached", {
            "file_checksum": "sha256:cached",
            "file_name": "cached.bin",
            "file_size": 25 * 1024 * 1024,
            "mime_type": "application/octet-stream",
            "uris": ["https://cached.com/files/existing"],
            "split": False,
        })

        # upload가 호출되지 않아야 함
        with patch(
            "llm_request.file_handler.uploader._aio_upload",
            new_callable=AsyncMock,
        ) as mock_upload:
            resolved = await resolve_files([meta], [file_path])
            mock_upload.assert_not_called()

        assert resolved.contents_parts[0].file_uri == "https://cached.com/files/existing"


# ── Context Cache (opt-in) ───────────────────────────────────


class TestContextCache:

    @pytest.mark.asyncio
    async def test_context_cache_created_with_cache_ttl(self, tmp_path):
        """cache_ttl이 있으면 context cache 생성."""
        file_path = _create_file(str(tmp_path / "ctx.bin"), 25 * 1024 * 1024)
        meta = _meta("sha256:ctx", "ctx.bin", 25 * 1024 * 1024, "application/octet-stream")

        with patch(
            "llm_request.file_handler.uploader._aio_upload",
            new_callable=AsyncMock,
            return_value=_fake_file_ref(),
        ), patch(
            "llm_request.file_handler.uploader._aio_create_cache",
            new_callable=AsyncMock,
            return_value=_fake_cache_ref("cachedContents/new"),
        ):
            resolved = await resolve_files(
                [meta], [file_path],
                model="gemini-2.5-flash",
                cache_ttl=1800,
            )

        assert resolved.cached_content == "cachedContents/new"
        assert resolved.contents_parts == []

    @pytest.mark.asyncio
    async def test_context_cache_reused_from_redis(self, tmp_path):
        """Redis에 기존 context cache가 있으면 재사용."""
        file_path = _create_file(str(tmp_path / "reuse.bin"), 25 * 1024 * 1024)
        meta = _meta("sha256:reuse", "reuse.bin", 25 * 1024 * 1024, "application/octet-stream")

        # Redis에 context cache 미리 저장
        hash_key = cache_store.make_context_cache_key(
            ["sha256:reuse"], model="gemini-2.5-flash",
        )
        cache_store.save_context_cache(hash_key, {
            "file_checksums": ["sha256:reuse"],
            "model": "gemini-2.5-flash",
            "cached_content_name": "cachedContents/existing",
        })

        # upload/create_cache 호출 없이 Redis에서 가져와야 함
        with patch(
            "llm_request.file_handler.uploader._aio_upload",
            new_callable=AsyncMock,
        ) as mock_upload, patch(
            "llm_request.file_handler.uploader._aio_create_cache",
            new_callable=AsyncMock,
        ) as mock_cache:
            resolved = await resolve_files(
                [meta], [file_path], model="gemini-2.5-flash",
            )
            mock_upload.assert_not_called()
            mock_cache.assert_not_called()

        assert resolved.cached_content == "cachedContents/existing"

    @pytest.mark.asyncio
    async def test_context_cache_failure_falls_back(self, tmp_path):
        """context cache 생성 실패 시 contents_parts로 fallback."""
        file_path = _create_file(str(tmp_path / "fail.bin"), 25 * 1024 * 1024)
        meta = _meta("sha256:fail", "fail.bin", 25 * 1024 * 1024, "application/octet-stream")

        with patch(
            "llm_request.file_handler.uploader._aio_upload",
            new_callable=AsyncMock,
            return_value=_fake_file_ref(),
        ), patch(
            "llm_request.file_handler.uploader._aio_create_cache",
            new_callable=AsyncMock,
            side_effect=RuntimeError("create_cache 실패"),
        ):
            resolved = await resolve_files(
                [meta], [file_path],
                model="gemini-2.5-flash",
                cache_ttl=1800,
            )

        assert resolved.cached_content is None
        assert len(resolved.contents_parts) == 1


# ── 혼합 파일 ────────────────────────────────────────────────


class TestMixed:

    @pytest.mark.asyncio
    async def test_inline_plus_files_api(self, tmp_path):
        """작은 파일(inline) + 큰 파일(Files API) 혼합."""
        small_path = _create_small_pdf(str(tmp_path / "small.pdf"))
        large_path = _create_file(str(tmp_path / "large.bin"), 25 * 1024 * 1024)

        small_size = os.path.getsize(small_path)
        meta_small = _meta("sha256:s", "small.pdf", small_size, "application/pdf")
        meta_large = _meta("sha256:l", "large.bin", 25 * 1024 * 1024, "application/octet-stream")

        with patch(
            "llm_request.file_handler.uploader._aio_upload",
            new_callable=AsyncMock,
            return_value=_fake_file_ref(),
        ):
            resolved = await resolve_files(
                [meta_small, meta_large],
                [small_path, large_path],
            )

        assert len(resolved.contents_parts) == 2
        # 첫 번째: inline, 두 번째: file URI
        assert resolved.contents_parts[0].type == "inline"
        assert resolved.contents_parts[1].type == "file"
