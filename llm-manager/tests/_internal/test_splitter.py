"""file_processing/splitter.py 테스트."""

from __future__ import annotations

import os
import tempfile

import fitz  # PyMuPDF
import pytest

from llm_manager._internal.file_processing import splitter as splitter_mod
from llm_manager._internal.file_processing.splitter import split_pdf
from llm_manager.exceptions import PdfPageTooLargeError

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


def _create_nav_widget_pdf(path: str, num_pages: int = 3) -> str:
    """페이지마다 큰 이미지 + 다른 페이지로 점프하는 GoTo 위젯이 달린 PDF.

    인터랙티브 목차/네비게이션 버튼을 가진 실제 보고서(이슈 #21)의 구조를 재현한다.
    select() 는 Link 주석과 목적지는 재작성해 정리하지만 Widget 의 /AA(추가 액션)는
    손대지 않는다. 그래서 한 페이지만 남겨도
        page → /Annots → Widget → /AA → GoTo → 다른 page → 그 page 의 이미지
    사슬을 타고 문서 전체가 딸려와, 단일 페이지 파트가 원본 크기가 된다.
    """
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
        # 압축되지 않는 노이즈 이미지 → 페이지마다 뚜렷한 바이트 기여
        pix = fitz.Pixmap(fitz.csRGB, 600, 600, bytearray(os.urandom(600 * 600 * 3)), False)
        page.insert_image(fitz.Rect(0, 100, 500, 500), pixmap=pix)
    doc.save(path, deflate=True)
    doc.close()

    doc = fitz.open(path)
    try:
        for i in range(num_pages):
            widget_refs = []
            for j in range(num_pages):
                if j == i:
                    continue
                action = doc.get_new_xref()
                doc.update_object(action, f"<</S/GoTo/D[{doc[j].xref} 0 R/Fit]>>")
                widget = doc.get_new_xref()
                doc.update_object(
                    widget,
                    "<</Type/Annot/Subtype/Widget/FT/Btn/F 4/Rect[0 0 10 10]"
                    f"/AA<</D {action} 0 R>>>>",
                )
                widget_refs.append(widget)
            refs = " ".join(f"{r} 0 R" for r in widget_refs)
            doc.xref_set_key(doc[i].xref, "Annots", f"[{refs}]")
        doc.saveIncr()
    finally:
        doc.close()
    return path


def _saved_page_bytes(path: str, page: int) -> int:
    """path 의 page 한 장만 남겨 저장했을 때의 실제 바이트 수."""
    doc = fitz.open(path)
    try:
        doc.select([page])
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        doc.save(tmp.name, garbage=2, deflate=True)
    finally:
        doc.close()
    size = os.path.getsize(tmp.name)
    os.unlink(tmp.name)
    return size


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

        with patch("llm_manager._internal.file_processing.splitter._MERGE_LIMIT_MB", merge_limit):
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

    def test_single_page_exceeding_limit_raises(self, tmp_path):
        """단일 페이지가 한도(50MB)를 초과하면 더 쪼갤 수 없으니 거부한다.

        공유 객체 풀로 1페이지가 50MB를 넘는 병리적 PDF(이슈 #21) 대응. 여기서는
        target_mb를 아주 작게 줘서 '1페이지가 한도 초과' 상황을 재현한다.
        """
        pdf_path = str(tmp_path / "unsplittable.pdf")
        _create_pdf(pdf_path, num_pages=1)

        with pytest.raises(PdfPageTooLargeError):
            split_pdf(pdf_path, target_mb=0.00001)

    def test_nav_widget_pdf_strips_annots_and_splits(self, tmp_path, monkeypatch):
        """페이지마다 다른 페이지를 가리키는 GoTo 위젯이 있으면 단일 페이지 파트도 원본
        크기가 된다. 이때 거부하지 말고 주석을 떼어 재분할해 한도 이하 파트를 만든다.

        이슈 #21 후속 — 인터랙티브 목차/네비게이션 버튼을 가진 보고서(예: 현대글로비스
        2025 지속가능경영보고서)가 통째로 거부되던 회귀.
        """
        pdf_path = str(tmp_path / "nav.pdf")
        _create_nav_widget_pdf(pdf_path, num_pages=3)

        # 전제: 주석 사슬 탓에 단일 페이지 파트가 원본 전체 크기다
        file_size = os.path.getsize(pdf_path)
        single_bytes = _saved_page_bytes(pdf_path, 0)
        assert single_bytes > file_size * 0.9, "테스트 전제: 단일 페이지가 원본 크기로 부풀어야 함"

        # 한도를 '주석 제거 후 한 페이지'는 들어가고 '원본 전체'는 못 들어가게 잡는다
        target_mb = (file_size / (1024 * 1024)) / 2
        limit_bytes = target_mb * 1024 * 1024

        # 병합이 파트를 한도 위로 되돌리지 않게 끔 (기존 테스트와 동일한 처리)
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.splitter._MERGE_LIMIT_MB", 0
        )

        result = split_pdf(pdf_path, target_mb=target_mb)

        total_pages = 0
        for part_path in result:
            assert os.path.isfile(part_path)
            assert os.path.getsize(part_path) <= limit_bytes, "파트가 한도를 넘음"
            doc = fitz.open(part_path)
            total_pages += doc.page_count
            doc.close()
        assert total_pages == 3, "모든 페이지가 보존되어야 함"

        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    @pytest.mark.parametrize("indirect", [False, True], ids=["direct", "indirect"])
    def test_empty_annots_array_skips_retry(self, tmp_path, monkeypatch, indirect):
        """뗄 주석이 실제로 없으면(빈 /Annots 배열) 사본을 만들지 않고 바로 거부한다.

        /Annots 는 직접 배열('[]')로도, 간접 참조('9 0 R')로도 온다. 키 존재만 보면
        둘 다 '주석 있음'이 되어 사본을 뜨고 재분할하는 한 바퀴를 헛돈다 (PR #73 리뷰).
        """
        pdf_path = str(tmp_path / "empty_annots.pdf")
        _create_pdf(pdf_path, num_pages=1)

        doc = fitz.open(pdf_path)
        try:
            if indirect:
                xref = doc.get_new_xref()
                doc.update_object(xref, "[]")
                doc.xref_set_key(doc[0].xref, "Annots", f"{xref} 0 R")
            else:
                doc.xref_set_key(doc[0].xref, "Annots", "[]")
            doc.saveIncr()
        finally:
            doc.close()

        assert not splitter_mod._has_annots(pdf_path), "빈 배열은 '주석 없음'이어야 함"

        strip_calls: list[str] = []
        original_strip = splitter_mod._strip_annots_copy

        def spying_strip(path: str, created_temps: list[str]) -> str:
            strip_calls.append(path)
            return original_strip(path, created_temps)

        monkeypatch.setattr(splitter_mod, "_strip_annots_copy", spying_strip)

        with pytest.raises(PdfPageTooLargeError):
            split_pdf(pdf_path, target_mb=0.00001)

        assert strip_calls == [], "뗄 주석이 없으면 사본을 만들지 않아야 함"

    def test_oversized_group_bisects_to_fit(self, tmp_path, monkeypatch):
        """추정이 실제보다 작아 여러 페이지가 한 그룹으로 묶여도, 저장 후 한도를 넘으면
        페이지를 이등분해 모든 파트를 한도 이하로 맞춘다 (단일 페이지가 종료 floor).

        (구 _MAX_DEPTH 무한재귀 방지 테스트 대체 — 이제 재귀는 단일 페이지에서 종료.)
        """
        pdf_path = str(tmp_path / "bisect.pdf")
        _create_pdf(pdf_path, num_pages=8, text="data " * 200)

        # 단일 페이지 실제 크기 측정 → limit을 그 위로 잡아 reject 없이 bisect만 유도
        d = fitz.open(pdf_path)
        d.select([0])
        t = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        t.close()
        d.save(t.name, garbage=2, deflate=True)
        d.close()
        single_bytes = os.path.getsize(t.name)
        os.unlink(t.name)
        target_mb = (single_bytes * 1.5) / (1024 * 1024)
        limit_bytes = target_mb * 1024 * 1024

        # 추정을 균일·미세하게 → 8페이지가 한 그룹으로 묶임 → 저장 후 초과 → 이등분 재귀
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.splitter._estimate_page_sizes",
            lambda doc, fs: [1.0] * len(doc),
        )
        # 병합이 한도 위로 되돌리지 않게 끔
        monkeypatch.setattr(
            "llm_manager._internal.file_processing.splitter._MERGE_LIMIT_MB", 0
        )

        result = split_pdf(pdf_path, target_mb=target_mb)

        total = 0
        for part_path in result:
            assert os.path.getsize(part_path) <= limit_bytes
            doc = fitz.open(part_path)
            total += len(doc)
            doc.close()
        assert total == 8
        assert len(result) > 1  # 실제로 이등분됐는지

        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)


# ── #3 Phase 2 — leak 방지 검증 ────────────────────────────────


class TestSplitPdfLeakPrevention:
    """예외 / 엣지 케이스에서 임시 파일 leak 이 없는지 검증.

    docs/11.minor-features-and-backlog.md 참조.
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
            "llm_manager._internal.file_processing.splitter.tempfile.NamedTemporaryFile",
            tracking_tmpfile,
        )

        with pytest.raises(RuntimeError, match="simulated save failure"):
            split_pdf(pdf_path, target_mb=target_mb)

        # 만들어진 모든 임시 파일이 정리되어야 함 (leak 0)
        assert len(created_paths) >= 2, "테스트 전제: 최소 2개 임시 파일 생성"
        for p in created_paths:
            assert not os.path.exists(p), f"임시 파일 leak: {p}"

    def test_returned_parts_all_exist(self, tmp_path):
        """Bug B 회귀 — 분할 결과의 모든 경로가 실제 파일로 존재한다
        (재분할/병합 중 unlink 된 임시 경로가 result 에 남으면 안 됨)."""
        from unittest.mock import patch

        pdf_path = str(tmp_path / "exist.pdf")
        _create_pdf(pdf_path, num_pages=20)

        file_size = os.path.getsize(pdf_path)
        target_mb = max((file_size / (1024 * 1024)) / 3, 0.001)

        with patch("llm_manager._internal.file_processing.splitter._MERGE_LIMIT_MB", 0):
            result = split_pdf(pdf_path, target_mb=target_mb)

        assert len(result) >= 2
        for part_path in result:
            assert os.path.isfile(part_path), f"result 의 파일이 존재하지 않음: {part_path}"

        # 정리
        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    def test_strip_annots_save_failure_cleans_up_temp_files(self, tmp_path, monkeypatch):
        """주석 제거 사본의 save 가 실패해도 임시 파일이 남지 않는다 (PR #73 리뷰).

        _strip_annots_copy 는 save 이전에 임시 파일을 등록하므로, 사본 생성이 정리 루프
        바깥에 있으면 save 실패 시 빈 파일이 디스크에 남는다.
        """
        pdf_path = str(tmp_path / "nav_fail.pdf")
        _create_nav_widget_pdf(pdf_path, num_pages=3)
        target_mb = (os.path.getsize(pdf_path) / (1024 * 1024)) / 2

        # _has_annots 가 True 를 돌려준 시점 = 주석 제거 사본을 만들기 직전
        stripping = {"now": False}
        original_has_annots = splitter_mod._has_annots

        def flagging_has_annots(path: str) -> bool:
            result = original_has_annots(path)
            stripping["now"] = result
            return result

        monkeypatch.setattr(splitter_mod, "_has_annots", flagging_has_annots)

        original_save = fitz.Document.save

        def failing_save(self, *args, **kwargs):
            if stripping["now"]:  # 사본 save 에서만 실패
                raise RuntimeError("simulated strip save failure")
            return original_save(self, *args, **kwargs)

        monkeypatch.setattr(fitz.Document, "save", failing_save)

        created_paths: list[str] = []
        original_tmpfile = tempfile.NamedTemporaryFile

        def tracking_tmpfile(*args, **kwargs):
            f = original_tmpfile(*args, **kwargs)
            created_paths.append(f.name)
            return f

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.splitter.tempfile.NamedTemporaryFile",
            tracking_tmpfile,
        )

        with pytest.raises(RuntimeError, match="simulated strip save failure"):
            split_pdf(pdf_path, target_mb=target_mb)

        assert created_paths, "테스트 전제: 임시 파일이 최소 하나 생성되어야 함"
        for p in created_paths:
            assert not os.path.exists(p), f"임시 파일 leak: {p}"


# ── 변경 C — 페이지 캡 (max_pages) ──────────────────────────────


def _part_pages(result: list[str]) -> list[int]:
    counts = []
    for p in result:
        doc = fitz.open(p)
        counts.append(len(doc))
        doc.close()
    return counts


def _cleanup(result: list[str], original: str) -> None:
    for p in result:
        if p != original:
            os.unlink(p)


class TestPageCap:
    """split_pdf max_pages — 초과 시 ceil(pages/max_pages) 등분 (변경 C §5.2).

    실 한도는 1000p지만 거대 PDF 생성을 피하려고 max_pages를 작게 줘 등분 로직을 본다.
    """

    def test_at_max_pages_no_split(self, tmp_path):
        """정확히 max_pages면 분할 안 함."""
        pdf_path = _create_pdf(str(tmp_path / "p.pdf"), num_pages=10)
        assert split_pdf(pdf_path, max_pages=10) == [pdf_path]

    def test_page_cap_splits_when_over_max(self, tmp_path):
        """25p, max_pages=10 → ceil(25/10)=3 파트, 각 ≤10p, 합계 25p."""
        pdf_path = _create_pdf(str(tmp_path / "p.pdf"), num_pages=25)
        result = split_pdf(pdf_path, max_pages=10)
        try:
            assert len(result) == 3
            assert all(p != pdf_path for p in result)
            counts = _part_pages(result)
            assert all(c <= 10 for c in counts)
            assert sum(counts) == 25
        finally:
            _cleanup(result, pdf_path)

    def test_page_cap_equal_division(self, tmp_path):
        """20p, max_pages=10 → 2 파트 각 10p."""
        pdf_path = _create_pdf(str(tmp_path / "p.pdf"), num_pages=20)
        result = split_pdf(pdf_path, max_pages=10)
        try:
            assert sorted(_part_pages(result)) == [10, 10]
        finally:
            _cleanup(result, pdf_path)
