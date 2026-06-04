"""file_processing/splitter.py 테스트."""

from __future__ import annotations

import os
import tempfile

import fitz  # PyMuPDF
import pytest

from llm_request._internal.file_processing.splitter import split_pdf

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

    @pytest.mark.parametrize("merge_limit", [0])
    def test_split_creates_multiple_parts(self, tmp_path, merge_limit):
        """target_mb를 낮추면 여러 파트로 분할된다."""
        from unittest.mock import patch

        pdf_path = str(tmp_path / "multi.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        # target을 파일 크기의 절반 이하로 설정 → 최소 2파트
        target_mb = (file_size / (1024 * 1024)) / 2

        with patch("llm_request._internal.file_processing.splitter._MERGE_LIMIT_MB", merge_limit):
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


# ── #3 Phase 2 — leak 방지 검증 ────────────────────────────────


class TestSplitPdfLeakPrevention:
    """예외 / 엣지 케이스에서 임시 파일 leak 이 없는지 검증.

    docs/16.memory-cleanup-plan.md §3.3 / §5.2 참조.
    """

    def test_save_failure_cleans_up_temp_files(self, tmp_path, monkeypatch):
        """part_doc.save() 중간 실패 시 누적된 임시 파일이 모두 정리된다."""
        pdf_path = str(tmp_path / "fail.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        target_mb = max((file_size / (1024 * 1024)) / 3, 0.001)  # 최소 3 파트

        # 셋업 완료 후 mock 적용 — 두 번째 save 호출에서 raise
        save_count = 0
        original_save = fitz.Document.save

        def failing_save(self, *args, **kwargs):
            nonlocal save_count
            save_count += 1
            if save_count == 2:
                raise RuntimeError("simulated save failure")
            return original_save(self, *args, **kwargs)

        monkeypatch.setattr(fitz.Document, "save", failing_save)

        # 만들어진 임시 파일 추적
        created_paths: list[str] = []
        original_tmpfile = tempfile.NamedTemporaryFile

        def tracking_tmpfile(*args, **kwargs):
            f = original_tmpfile(*args, **kwargs)
            created_paths.append(f.name)
            return f

        monkeypatch.setattr(
            "llm_request._internal.file_processing.splitter.tempfile.NamedTemporaryFile",
            tracking_tmpfile,
        )

        with pytest.raises(RuntimeError, match="simulated save failure"):
            split_pdf(pdf_path, target_mb=target_mb)

        # 만들어진 모든 임시 파일이 정리되어야 함 (leak 0)
        assert len(created_paths) >= 2, "테스트 전제: 최소 2개 임시 파일 생성"
        for p in created_paths:
            assert not os.path.exists(p), f"임시 파일 leak: {p}"

    def test_max_depth_recursion_no_unlinked_path_in_result(self, tmp_path):
        """Bug B 회귀 — _MAX_DEPTH 초과 재귀가 [tmp.name] 반환 시 result 의 파일이 실제 존재."""
        pdf_path = str(tmp_path / "deep.pdf")
        _create_pdf(pdf_path, num_pages=10)

        file_size = os.path.getsize(pdf_path)
        # 매우 작은 target + _depth=2 시작 → 첫 재귀가 _MAX_DEPTH(3) 초과
        target_mb = max((file_size / (1024 * 1024)) / 4, 0.001)

        result = split_pdf(pdf_path, target_mb=target_mb, _depth=2)

        # result 의 모든 파일이 실제 존재해야 (unlink 된 채 result 에 들어가면 안 됨)
        for part_path in result:
            assert os.path.isfile(part_path), f"result 의 파일이 존재하지 않음: {part_path}"

        # 정리
        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)
