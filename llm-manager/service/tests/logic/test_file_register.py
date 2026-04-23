"""file_register.py 단위 테스트.

`register_files` — file-manager에 FileInput 리스트 등록.
check_cache hit/miss 분기 + 예외 wrap (CacheError / FileUploadError) 검증.

FileManagerHandler 는 외부(file-manager SDK) 패키지 클래스이므로 monkeypatch로 모킹.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import CacheError, FileUploadError
from app.logic.file_register import register_files
from app.models.requests import FileInput


def _file(
    file_name: str = "doc.pdf",
    file_size: int = 1024,
    file_mtime: int = 0,
    mime_type: str = "application/pdf",
    extension: str = "pdf",
    data: bytes = b"hello",
) -> FileInput:
    return FileInput(
        file_name=file_name,
        file_size=file_size,
        file_mtime=file_mtime,
        mime_type=mime_type,
        extension=extension,
        data=data,
    )


@pytest.fixture()
def mock_fm(monkeypatch):
    """FileManagerHandler() 호출 시 동일 mock 인스턴스 반환 (싱글톤 흉내)."""
    fm_instance = MagicMock()
    fm_instance.check_cache = AsyncMock()
    fm_instance.upload_file = AsyncMock()

    fm_class = MagicMock(return_value=fm_instance)
    monkeypatch.setattr("app.logic.file_register.FileManagerHandler", fm_class)
    return fm_instance


class TestRegisterFiles:
    async def test_cache_hit_skips_upload(self, mock_fm):
        """check_cache hit 이면 upload_file 호출 안 함."""
        mock_fm.check_cache.return_value = {"hit": True, "file_path": "abc.pdf"}

        await register_files([_file("doc.pdf")])

        mock_fm.check_cache.assert_awaited_once()
        mock_fm.upload_file.assert_not_awaited()

    async def test_cache_miss_triggers_upload(self, mock_fm):
        """check_cache miss 면 upload_file 을 5필드 메타로 호출."""
        mock_fm.check_cache.return_value = {"hit": False, "file_path": None}
        mock_fm.upload_file.return_value = "uuid.pdf"

        await register_files([_file("doc.pdf", data=b"abc")])

        mock_fm.check_cache.assert_awaited_once()
        mock_fm.upload_file.assert_awaited_once_with(
            b"abc",
            {
                "file_name": "doc.pdf",
                "file_size": 1024,
                "file_mtime": 0,
                "mime_type": "application/pdf",
                "extension": "pdf",
            },
        )

    async def test_check_cache_exception_wraps_to_cache_error(self, mock_fm):
        """check_cache 실패 → CacheError 로 wrap (file_name 메시지 포함)."""
        mock_fm.check_cache.side_effect = RuntimeError("connection failed")

        with pytest.raises(CacheError) as exc:
            await register_files([_file("doc.pdf")])
        assert "doc.pdf" in str(exc.value)
        assert "connection failed" in str(exc.value)

    async def test_upload_exception_wraps_to_file_upload_error(self, mock_fm):
        """upload_file 실패 → FileUploadError 로 wrap (file_name 메시지 포함)."""
        mock_fm.check_cache.return_value = {"hit": False, "file_path": None}
        mock_fm.upload_file.side_effect = RuntimeError("upload failed")

        with pytest.raises(FileUploadError) as exc:
            await register_files([_file("doc.pdf")])
        assert "doc.pdf" in str(exc.value)
        assert "upload failed" in str(exc.value)

    async def test_empty_list_no_calls(self, mock_fm):
        """빈 리스트 → check_cache/upload_file 호출 없음, 빈 리스트 반환."""
        result = await register_files([])

        assert result == []
        mock_fm.check_cache.assert_not_awaited()
        mock_fm.upload_file.assert_not_awaited()

    async def test_mixed_hit_miss(self, mock_fm):
        """여러 파일 — 일부 hit, 일부 miss → 각각 다르게 처리."""
        mock_fm.check_cache.side_effect = [
            {"hit": True, "file_path": "a.pdf"},
            {"hit": False, "file_path": None},
            {"hit": True, "file_path": "c.pdf"},
        ]
        mock_fm.upload_file.return_value = "b-uuid.pdf"

        await register_files([
            _file("a.pdf"),
            _file("b.pdf", data=b"bbb"),
            _file("c.pdf"),
        ])

        assert mock_fm.check_cache.await_count == 3
        assert mock_fm.upload_file.await_count == 1  # b만 upload
        mock_fm.upload_file.assert_awaited_once_with(
            b"bbb",
            {
                "file_name": "b.pdf",
                "file_size": 1024,
                "file_mtime": 0,
                "mime_type": "application/pdf",
                "extension": "pdf",
            },
        )
