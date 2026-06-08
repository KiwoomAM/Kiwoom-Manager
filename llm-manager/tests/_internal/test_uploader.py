"""file_processing/uploader.py 테스트.

llm-handler SDK 호출을 mock으로 검증한다.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import fitz
import pytest

from llm_manager._internal.file_processing.uploader import (
    create_context_cache,
    upload_pdf_parts,
)
from llm_manager._internal.handler_client.models import CacheRef, FileRef

# ── 헬퍼 ─────────────────────────────────────────────────────


def _create_pdf(path: str, num_pages: int = 10) -> str:
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


# ── upload_pdf_parts ─────────────────────────────────────────


class TestUploadPdfParts:

    @pytest.mark.asyncio
    async def test_small_pdf_no_split(self, tmp_path):
        """50MB 이하 PDF는 분할 없이 1개 업로드."""
        pdf_path = str(tmp_path / "small.pdf")
        _create_pdf(pdf_path, num_pages=5)

        with patch(
            "llm_manager._internal.file_processing.uploader.upload_file",
            new_callable=AsyncMock,
            return_value=_fake_file_ref(),
        ):
            refs = await upload_pdf_parts(pdf_path)
            assert len(refs) == 1

    @pytest.mark.asyncio
    async def test_split_pdf_uploads_all_parts(self, tmp_path):
        """분할된 파트 수만큼 업로드 호출."""
        pdf_path = str(tmp_path / "multi.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        # 아주 작은 target으로 강제 분할
        target_mb = max((file_size / (1024 * 1024)) / 3, 0.001)

        call_count = 0

        async def _mock_upload(path, **kwargs):
            nonlocal call_count
            call_count += 1
            return _fake_file_ref(uri=f"https://example.com/files/part{call_count}")

        with patch(
            "llm_manager._internal.file_processing.uploader.upload_file",
            side_effect=_mock_upload,
        ), patch(
            "llm_manager._internal.file_processing.splitter._MERGE_LIMIT_MB", 0,
        ):
            refs = await upload_pdf_parts(pdf_path, target_mb=target_mb)
            assert len(refs) >= 2
            assert call_count == len(refs)

    @pytest.mark.asyncio
    async def test_temp_files_cleaned_up(self, tmp_path):
        """업로드 후 임시 파일이 삭제된다."""
        pdf_path = str(tmp_path / "cleanup.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        target_mb = max((file_size / (1024 * 1024)) / 3, 0.001)

        with patch(
            "llm_manager._internal.file_processing.uploader.upload_file",
            new_callable=AsyncMock,
            return_value=_fake_file_ref(),
        ):
            await upload_pdf_parts(pdf_path, target_mb=target_mb)

        # tmp_path 내에 원본 PDF만 남아있어야 함
        remaining = list(tmp_path.glob("*.pdf"))
        assert len(remaining) == 1
        assert remaining[0].name == "cleanup.pdf"

    @pytest.mark.asyncio
    async def test_temp_files_cleaned_on_error(self, tmp_path):
        """업로드 실패해도 임시 파일 삭제 보장."""
        pdf_path = str(tmp_path / "error.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        target_mb = max((file_size / (1024 * 1024)) / 3, 0.001)

        with patch(
            "llm_manager._internal.file_processing.uploader.upload_file",
            new_callable=AsyncMock,
            side_effect=RuntimeError("업로드 실패"),
        ):
            with pytest.raises(RuntimeError):
                await upload_pdf_parts(pdf_path, target_mb=target_mb)

        # 임시 파일이 정리됐는지 확인 (시스템 temp 디렉토리에 생성되므로 tmp_path에는 원본만)
        remaining = list(tmp_path.glob("*.pdf"))
        assert len(remaining) == 1


# ── create_context_cache ─────────────────────────────────────


class TestCreateContextCache:

    @pytest.mark.asyncio
    async def test_success(self):
        with patch(
            "llm_manager._internal.file_processing.uploader._aio_create_cache",
            new_callable=AsyncMock,
            return_value=_fake_cache_ref(),
        ) as mock:
            ref = await create_context_cache(
                [_fake_file_ref()],
                model="gemini-2.5-flash",
                system_instruction="test",
                ttl=1800,
            )

            assert ref.name == "cachedContents/xyz"
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_503(self):
        """503 에러 시 재시도."""
        call_count = 0

        async def _flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("503 Service Unavailable")
            return _fake_cache_ref()

        with patch(
            "llm_manager._internal.file_processing.uploader._aio_create_cache",
            side_effect=_flaky,
        ):
            ref = await create_context_cache(
                [_fake_file_ref()], model="gemini-2.5-flash",
            )
            assert ref.name == "cachedContents/xyz"
            assert call_count == 3  # 2회 실패 + 1회 성공

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable(self):
        """retryable이 아닌 에러는 즉시 raise."""
        with patch(
            "llm_manager._internal.file_processing.uploader._aio_create_cache",
            new_callable=AsyncMock,
            side_effect=ValueError("잘못된 입력"),
        ):
            with pytest.raises(ValueError, match="잘못된 입력"):
                await create_context_cache(
                    [_fake_file_ref()], model="gemini-2.5-flash",
                )

    @pytest.mark.asyncio
    async def test_tools_and_tool_config_passed_to_aio_create_cache(self):
        """Critical 회귀 방지: tools/tool_config 가 llm_manager._internal.handler_client 까지 전달되는지.

        이전 버그: uploader 가 tools/tool_config 를 받지도 않아 request 의 tools 가
        cache 에 반영되지 않은 채 runner 가 'cache 에 있다' 가정으로 pop → silent drop.
        """
        captured: dict = {}

        async def _capture(file_refs, **kwargs):
            captured.update(kwargs)
            return _fake_cache_ref()

        with patch(
            "llm_manager._internal.file_processing.uploader._aio_create_cache",
            side_effect=_capture,
        ):
            await create_context_cache(
                [_fake_file_ref()],
                model="gemini-2.5-flash",
                system_instruction="sys",
                ttl=600,
                tools=[{"name": "calculator"}],
                tool_config="auto",
            )

        assert captured["tools"] == [{"name": "calculator"}]
        assert captured["tool_config"] == "auto"
        # llm_manager._internal.handler_client.aio.create_cache 는 system_instruction 이 아닌 system 키 사용
        assert captured["system"] == "sys"
        assert captured["ttl"] == 600

    @pytest.mark.asyncio
    async def test_tools_default_none_passed_through(self):
        """tools/tool_config 미지정 시 None 으로 명시 전달 (기본값 의존 X)."""
        captured: dict = {}

        async def _capture(file_refs, **kwargs):
            captured.update(kwargs)
            return _fake_cache_ref()

        with patch(
            "llm_manager._internal.file_processing.uploader._aio_create_cache",
            side_effect=_capture,
        ):
            await create_context_cache(
                [_fake_file_ref()], model="gemini-2.5-flash",
            )

        assert captured["tools"] is None
        assert captured["tool_config"] is None
