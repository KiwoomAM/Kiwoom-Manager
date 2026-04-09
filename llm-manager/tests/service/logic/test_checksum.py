"""checksum.py 테스트 — file_checksum 유틸리티."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from app.logic.checksum import file_checksum


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """테스트용 가짜 PDF 파일 생성."""
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4 fake content for testing")
    return p


@pytest.fixture()
def sample_txt(tmp_path: Path) -> Path:
    """테스트용 텍스트 파일 생성."""
    p = tmp_path / "sample.txt"
    p.write_text("hello world", encoding="utf-8")
    return p


class TestFileChecksum:

    def test_returns_correct_keys(self, sample_pdf: Path):
        result = file_checksum(sample_pdf)
        assert "checksum" in result
        assert "filename" in result
        assert "size" in result
        assert "mime_type" in result

    def test_checksum_format(self, sample_pdf: Path):
        result = file_checksum(sample_pdf)
        assert re.match(r"^sha256:[0-9a-f]{64}$", result["checksum"])

    def test_filename_from_path(self, sample_pdf: Path):
        result = file_checksum(sample_pdf)
        assert result["filename"] == "sample.pdf"

    def test_size_matches(self, sample_pdf: Path):
        result = file_checksum(sample_pdf)
        assert result["size"] == sample_pdf.stat().st_size

    def test_mime_type_detected(self, sample_txt: Path):
        result = file_checksum(sample_txt)
        assert "text" in result["mime_type"]

    def test_same_content_same_checksum(self, tmp_path: Path):
        """같은 내용 → 다른 경로/파일명이라도 동일 checksum."""
        content = b"identical content"
        p1 = tmp_path / "file_a.bin"
        p2 = tmp_path / "file_b.bin"
        p1.write_bytes(content)
        p2.write_bytes(content)

        r1 = file_checksum(p1)
        r2 = file_checksum(p2)
        assert r1["checksum"] == r2["checksum"]

    def test_different_content_different_checksum(self, tmp_path: Path):
        p1 = tmp_path / "a.bin"
        p2 = tmp_path / "b.bin"
        p1.write_bytes(b"content A")
        p2.write_bytes(b"content B")

        r1 = file_checksum(p1)
        r2 = file_checksum(p2)
        assert r1["checksum"] != r2["checksum"]

    def test_filename_override(self, sample_pdf: Path):
        result = file_checksum(sample_pdf, filename="custom_name.pdf")
        assert result["filename"] == "custom_name.pdf"

    def test_mime_type_override(self, sample_pdf: Path):
        result = file_checksum(sample_pdf, mime_type="application/custom")
        assert result["mime_type"] == "application/custom"

    def test_string_path(self, sample_pdf: Path):
        """str 경로도 동작."""
        result = file_checksum(str(sample_pdf))
        assert re.match(r"^sha256:[0-9a-f]{64}$", result["checksum"])

    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            file_checksum("/nonexistent/path/file.pdf")
