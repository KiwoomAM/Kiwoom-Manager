"""validators.py 단위 테스트.

MIME 화이트리스트 검증과 2GB 크기 상한 검증 — runner 진입 직후
fail-fast 차단(prepare·MDB 조회 이전)이 의도된 동작.
"""

from __future__ import annotations

import pytest

from app.core.errors import FileTooLargeError, UnsupportedFileTypeError
from app.logic.file_processing.validators import (
    MAX_FILE_SIZE,
    SUPPORTED_MIME_TYPES,
    validate_file_sizes,
    validate_mime_types,
)
from app.models.requests import FileInput


def _file(
    file_name: str = "doc.pdf",
    mime_type: str = "application/pdf",
    file_size: int = 1024,
    extension: str = "pdf",
) -> FileInput:
    return FileInput(
        file_name=file_name,
        file_size=file_size,
        file_mtime=0,
        mime_type=mime_type,
        extension=extension,
        data=b"x",
    )


# ── validate_mime_types ───────────────────────────────────────


class TestValidateMimeTypes:
    def test_supported_types_pass(self):
        """화이트리스트 mime은 모두 통과."""
        files = [
            _file(mime_type="application/pdf"),
            _file(mime_type="image/png"),
            _file(mime_type="audio/mp3"),
            _file(mime_type="text/plain"),
            _file(mime_type="video/mp4"),
        ]
        validate_mime_types(files)  # 예외 없음

    def test_whitelist_constants_present(self):
        """화이트리스트가 frozenset으로 정의되어 있고 핵심 타입을 포함."""
        assert isinstance(SUPPORTED_MIME_TYPES, frozenset)
        assert "application/pdf" in SUPPORTED_MIME_TYPES
        assert "image/png" in SUPPORTED_MIME_TYPES
        assert "video/mp4" in SUPPORTED_MIME_TYPES

    def test_unsupported_raises(self):
        """화이트리스트 밖 mime은 UnsupportedFileTypeError."""
        files = [_file(mime_type="application/x-msdownload")]
        with pytest.raises(UnsupportedFileTypeError) as exc:
            validate_mime_types(files)
        assert "application/x-msdownload" in str(exc.value)

    def test_empty_list_passes(self):
        """빈 리스트는 통과."""
        validate_mime_types([])

    def test_partial_unsupported_raises_on_first_bad(self):
        """여러 파일 중 하나라도 거부면 raise (file_name 메시지 포함)."""
        files = [
            _file(file_name="ok.pdf", mime_type="application/pdf"),
            _file(file_name="bad.exe", mime_type="application/x-msdownload"),
            _file(file_name="ok2.png", mime_type="image/png"),
        ]
        with pytest.raises(UnsupportedFileTypeError) as exc:
            validate_mime_types(files)
        assert "bad.exe" in str(exc.value)


# ── validate_file_sizes ───────────────────────────────────────


class TestValidateFileSizes:
    def test_under_limit_passes(self):
        """2GB 이하는 통과 (100MB)."""
        validate_file_sizes([_file(file_size=100 * 1024 * 1024)])

    def test_at_limit_passes(self):
        """정확히 2GB는 통과 (경계는 inclusive)."""
        validate_file_sizes([_file(file_size=MAX_FILE_SIZE)])

    def test_over_limit_raises(self):
        """2GB 초과는 FileTooLargeError (file_name 메시지 포함)."""
        files = [_file(file_name="huge.pdf", file_size=MAX_FILE_SIZE + 1)]
        with pytest.raises(FileTooLargeError) as exc:
            validate_file_sizes(files)
        assert "huge.pdf" in str(exc.value)

    def test_zero_size_passes(self):
        """0바이트도 통과 — validators는 상한만 체크."""
        validate_file_sizes([_file(file_size=0)])

    def test_empty_list_passes(self):
        """빈 리스트는 통과."""
        validate_file_sizes([])
