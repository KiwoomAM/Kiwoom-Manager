"""file_handler/splitter.py 테스트."""

from __future__ import annotations

import os

import fitz  # PyMuPDF
import pytest

from llm_request.file_handler.splitter import split_pdf


# ── 헬퍼: 테스트용 PDF 생성 ─────────────────────────────────────


def _create_pdf(path: str, num_pages: int = 10, text: str = "Hello") -> str:
    """지정 페이지 수의 간단한 PDF를 생성한다."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} - page {i + 1}")
    doc.save(path)
    doc.close()
    return path


# ── 테스트 ───────────────────────────────────────────────────────


class TestSplitPdf:
    """split_pdf 함수 테스트."""

    def test_small_pdf_returns_original(self, tmp_path):
        """target_mb 이하 PDF는 분할하지 않고 원본 경로 반환."""
        pdf_path = str(tmp_path / "small.pdf")
        _create_pdf(pdf_path, num_pages=5)

        result = split_pdf(pdf_path, target_mb=50)

        assert result == [pdf_path]

    def test_split_creates_multiple_parts(self, tmp_path):
        """target_mb를 낮추면 여러 파트로 분할된다."""
        pdf_path = str(tmp_path / "multi.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        # target을 파일 크기의 절반 이하로 설정 → 최소 2파트
        target_mb = (file_size / (1024 * 1024)) / 2

        result = split_pdf(pdf_path, target_mb=max(target_mb, 0.001))

        assert len(result) >= 2
        # 모든 파트가 실제 파일로 존재
        for part_path in result:
            assert os.path.isfile(part_path)

        # 정리
        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    def test_all_parts_are_valid_pdf(self, tmp_path):
        """분할된 모든 파트가 유효한 PDF이다."""
        pdf_path = str(tmp_path / "valid.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        target_mb = (file_size / (1024 * 1024)) / 3

        result = split_pdf(pdf_path, target_mb=max(target_mb, 0.001))

        for part_path in result:
            doc = fitz.open(part_path)
            assert len(doc) > 0
            doc.close()

        # 정리
        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    def test_total_pages_preserved(self, tmp_path):
        """분할 후 전체 페이지 수가 보존된다."""
        num_pages = 30
        pdf_path = str(tmp_path / "pages.pdf")
        _create_pdf(pdf_path, num_pages=num_pages)

        file_size = os.path.getsize(pdf_path)
        target_mb = (file_size / (1024 * 1024)) / 4

        result = split_pdf(pdf_path, target_mb=max(target_mb, 0.001))

        total = 0
        for part_path in result:
            doc = fitz.open(part_path)
            total += len(doc)
            doc.close()

        assert total == num_pages

        # 정리
        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    def test_file_not_found_raises(self):
        """존재하지 않는 파일은 에러."""
        with pytest.raises(FileNotFoundError):
            split_pdf("/nonexistent/path.pdf")

    def test_single_page_pdf(self, tmp_path):
        """1페이지 PDF도 정상 처리."""
        pdf_path = str(tmp_path / "single.pdf")
        _create_pdf(pdf_path, num_pages=1)

        result = split_pdf(pdf_path, target_mb=50)
        assert result == [pdf_path]

    def test_max_depth_prevents_infinite_recursion(self, tmp_path):
        """_MAX_DEPTH 초과 시 재분할을 중단하고 현재 크기로 반환."""
        pdf_path = str(tmp_path / "depth.pdf")
        _create_pdf(pdf_path, num_pages=10)

        # _depth를 MAX 이상으로 설정 → 즉시 반환
        result = split_pdf(pdf_path, target_mb=0.0001, _depth=3)

        assert result == [pdf_path]
