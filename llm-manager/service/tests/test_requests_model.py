"""service/app/models/requests.py — Phase 2 모델 변경 검증."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestStoredFile:
    """StoredFile — file-manager 에 등록된 파일 참조 모델 (6 필드)."""

    def test_accepts_6_required_fields(self):
        from app.models.requests import StoredFile

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
        from app.models.requests import StoredFile

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
    """FileInput.resolved_via — Phase 2 추적 필드."""

    def test_default_none(self):
        from app.models.requests import FileInput

        fi = FileInput(
            file_name="x.pdf", file_size=1, file_mtime=0,
            mime_type="application/pdf", extension=".pdf",
            data=b"\x00",
        )
        assert fi.resolved_via is None

    def test_accepts_upload_primary_fallback(self):
        from app.models.requests import FileInput

        for value in ("upload", "primary", "fallback"):
            fi = FileInput(
                file_name="x.pdf", file_size=1, file_mtime=0,
                mime_type="application/pdf", extension=".pdf",
                data=b"\x00", resolved_via=value,
            )
            assert fi.resolved_via == value


class TestLLMRequestBodyStoredFiles:
    """LLMRequestBody.stored_files — Phase 2 신규 인자."""

    def test_default_none(self):
        from app.models.requests import LLMRequestBody

        body = LLMRequestBody(llm_input={"contents": "x"})
        assert body.stored_files is None

    def test_accepts_list_of_stored_files(self):
        from app.models.requests import LLMRequestBody, StoredFile

        body = LLMRequestBody(
            llm_input={"contents": "x"},
            stored_files=[
                StoredFile(
                    file_path="a.pdf", file_name="a", file_size=1, file_mtime=0,
                    mime_type="application/pdf", extension=".pdf",
                )
            ],
        )
        assert body.stored_files[0].file_path == "a.pdf"
