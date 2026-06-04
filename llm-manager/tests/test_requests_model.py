"""llm_request/models.py — 입력 모델 검증 (StoredFile / FileInput)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestStoredFile:
    """StoredFile — file-manager 에 등록된 파일 참조 모델 (6 필드)."""

    def test_accepts_6_required_fields(self):
        from llm_request.models import StoredFile

        sf = StoredFile(
            file_path="abc.pdf",
            file_name="x.pdf",
            file_size=1,
            file_mtime=0,
            mime_type="application/pdf",
            extension=".pdf",
        )
        assert sf.file_path == "abc.pdf"
        assert sf.mime_type == "application/pdf"

    def test_extra_keys_forbid_raises(self):
        """P2-4: extra='forbid' 라 server-fill 필드 (resolved_via 등) 는 ValidationError."""
        from llm_request.models import StoredFile

        with pytest.raises(ValidationError):
            StoredFile(
                file_path="abc.pdf",
                file_name="x.pdf",
                file_size=1,
                file_mtime=0,
                mime_type="application/pdf",
                extension=".pdf",
                resolved_via="primary",  # server-fill 필드 → forbid
            )


class TestFileInputResolvedVia:
    """FileInput.resolved_via — fetch 경로 추적 필드 (내부 타입)."""

    def test_default_none(self):
        from llm_request.models import FileInput

        fi = FileInput(
            file_name="x.pdf", file_size=1, file_mtime=0,
            mime_type="application/pdf", extension=".pdf",
            data=b"\x00",
        )
        assert fi.resolved_via is None

    def test_accepts_primary_fallback(self):
        """resolved_via 가 'primary' / 'fallback' 받음."""
        from llm_request.models import FileInput

        for value in ("primary", "fallback"):
            fi = FileInput(
                file_name="x.pdf", file_size=1, file_mtime=0,
                mime_type="application/pdf", extension=".pdf",
                data=b"\x00", resolved_via=value,
            )
            assert fi.resolved_via == value


class TestFileInputDataDefault:
    """FileInput.data default b'' (lazy fetch 전제)."""

    def test_data_default_is_empty_bytes(self):
        from llm_request.models import FileInput

        fi = FileInput(
            file_name="x.pdf", file_size=1, file_mtime=0,
            mime_type="application/pdf", extension=".pdf",
            # data 인자 생략
        )
        assert fi.data == b""
