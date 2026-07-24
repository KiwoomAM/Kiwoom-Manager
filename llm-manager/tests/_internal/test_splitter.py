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


def _create_tagged_pdf(path: str, num_pages: int = 3) -> str:
    """페이지마다 큰 이미지 + 전 페이지를 /Pg 로 지목하는 구조트리를 가진 태그드 PDF.

    InDesign 이 '태그드 PDF'(`/MarkInfo <</Marked true>>`)로 내보낸 실제 보고서
    (예: BNK금융그룹 2025 지속가능경영보고서)의 구조를 재현한다. select() 는 카탈로그의
    /StructTreeRoot 를 페이지별로 가지치기하지 않으므로, 한 페이지만 남겨도
        Root → /StructTreeRoot → StructElem → /Pg → 다른 page → 그 page 의 이미지
    사슬을 타고 문서 전체가 딸려온다. 주석 사슬(_create_nav_widget_pdf)과 달리
    진입점이 페이지가 아니라 문서 카탈로그라, 주석을 떼는 것으로는 끊기지 않는다.
    docs/34 §2-2 참고.
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
        root = int(doc.xref_get_key(-1, "Root")[1].split()[0])
        struct_root = doc.get_new_xref()
        kids = []
        for i in range(num_pages):
            elem = doc.get_new_xref()
            doc.update_object(
                elem,
                f"<</Type/StructElem/S/P/P {struct_root} 0 R/Pg {doc[i].xref} 0 R>>",
            )
            kids.append(elem)
        refs = " ".join(f"{k} 0 R" for k in kids)
        doc.update_object(struct_root, f"<</Type/StructTreeRoot/K[{refs}]>>")
        doc.xref_set_key(root, "StructTreeRoot", f"{struct_root} 0 R")
        doc.xref_set_key(root, "MarkInfo", "<</Marked true>>")
        doc.saveIncr()
    finally:
        doc.close()
    return path


def _create_big_appearance_pdf(path: str, num_pages: int = 3, ap_bytes: int = 1_000_000) -> str:
    """페이지마다 무거운 외관 스트림(/AP)을 가진 위젯 주석이 달린 PDF.

    페이지를 넘나드는 사슬은 없지만 주석 외관 자체가 무거워, 동작 키만 끊는 1단계로는
    단일 페이지가 한도 아래로 내려가지 않는다. 주석을 통째로 떼는 2단계에서야 해결된다.
    → 2단계 경로(손실 있는 안전망)를 태우기 위한 픽스처.
    """
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
        pix = fitz.Pixmap(fitz.csRGB, 300, 300, bytearray(os.urandom(300 * 300 * 3)), False)
        page.insert_image(fitz.Rect(0, 100, 500, 500), pixmap=pix)
    doc.save(path, deflate=True)
    doc.close()

    doc = fitz.open(path)
    try:
        for i in range(num_pages):
            ap = doc.get_new_xref()
            doc.update_object(ap, "<</Type/XObject/Subtype/Form/BBox[0 0 10 10]>>")
            doc.update_stream(ap, os.urandom(ap_bytes), compress=False)
            widget = doc.get_new_xref()
            doc.update_object(
                widget,
                "<</Type/Annot/Subtype/Widget/FT/Btn/F 4/Rect[0 0 10 10]"
                f"/P {doc[i].xref} 0 R/AP<</N {ap} 0 R>>>>",
            )
            doc.xref_set_key(doc[i].xref, "Annots", f"[{widget} 0 R]")
        doc.saveIncr()
    finally:
        doc.close()
    return path


def _create_shared_pool_appearance_pdf(
    path: str, num_pages: int = 3, pool_bytes: int = 1_000_000
) -> str:
    """모든 페이지의 위젯 외관(/AP)이 **같은** 무거운 Form XObject 를 참조하는 PDF.

    /AP 는 Form XObject 라 자기 /Resources 로 임의 객체를 참조할 수 있고, 그것이 문서
    공용 풀이면 페이지를 참조하지 않고도 바이트가 딸려온다 — docs/25 §2-3 의 공유 객체 풀
    병과 같은 형태다. 동작 키(/A·/AA·/Dest)만 끊는 1단계로는 이 사슬이 끊기지 않는다.
    docs/34 §2-6 이 "미검증"으로 남긴 가정을 겨냥한 픽스처.
    """
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    try:
        pool = doc.get_new_xref()  # 문서 전체가 공유하는 무거운 객체
        doc.update_object(pool, "<</Type/XObject/Subtype/Form/BBox[0 0 10 10]>>")
        doc.update_stream(pool, os.urandom(pool_bytes), compress=False)
        for i in range(num_pages):
            ap = doc.get_new_xref()
            doc.update_object(
                ap,
                "<</Type/XObject/Subtype/Form/BBox[0 0 10 10]"
                f"/Resources<</XObject<</Fm0 {pool} 0 R>>>>>>",
            )
            doc.update_stream(ap, b"/Fm0 Do")
            widget = doc.get_new_xref()
            doc.update_object(
                widget,
                "<</Type/Annot/Subtype/Widget/FT/Btn/F 4/Rect[0 0 10 10]"
                f"/P {doc[i].xref} 0 R/AP<</N {ap} 0 R>>>>",
            )
            doc.xref_set_key(doc[i].xref, "Annots", f"[{widget} 0 R]")
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

    def test_nav_widget_pdf_chain_broken_and_splits(self, tmp_path, monkeypatch):
        """페이지마다 다른 페이지를 가리키는 GoTo 위젯이 있으면 단일 페이지 파트도 원본
        크기가 된다. 이때 거부하지 말고 사슬을 끊어 한도 이하 파트를 만든다.

        이슈 #21 후속 — 인터랙티브 목차/네비게이션 버튼을 가진 보고서(예: 현대글로비스
        2025 지속가능경영보고서)가 통째로 거부되던 회귀. 이제 주석을 통째로 떼지 않고
        동작 키(/A·/AA·/Dest)만 끊어 1단계에서 해결한다 (docs/34 §2-6).
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

    def test_tagged_pdf_struct_tree_does_not_block_split(self, tmp_path, monkeypatch):
        """구조트리가 전 페이지를 /Pg 로 잡는 태그드 PDF도 분할된다.

        주석이 아예 없으므로 주석 제거 재시도로는 구제되지 않는다 — 사슬의 진입점이
        페이지가 아니라 문서 카탈로그라서, select() 이전에 카탈로그를 정리해야 끊긴다.
        docs/34 §2-2 (BNK금융그룹 2025 지속가능경영보고서가 통째로 거부되던 회귀).
        """
        pdf_path = str(tmp_path / "tagged.pdf")
        _create_tagged_pdf(pdf_path, num_pages=3)

        # 전제 ① 구조트리 사슬 탓에 단일 페이지 파트가 원본 전체 크기다
        file_size = os.path.getsize(pdf_path)
        single_bytes = _saved_page_bytes(pdf_path, 0)
        assert single_bytes > file_size * 0.9, "테스트 전제: 단일 페이지가 원본 크기로 부풀어야 함"
        # 전제 ② 뗄 주석이 없다 → 기존 처방(주석 제거)은 이 파일에 무력하다
        assert not splitter_mod._has_annots(pdf_path), "테스트 전제: 주석 없는 태그드 PDF"

        target_mb = (file_size / (1024 * 1024)) / 2
        limit_bytes = target_mb * 1024 * 1024

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

    def test_shared_pool_via_appearance_stream(self, tmp_path):
        """위젯 외관(/AP)이 공용 풀을 물면 1단계로는 안 끊기고 2단계에서 해결된다.

        §2-6 은 "코퍼스 7개 파일에서는 사슬이 /A·/AA·/Dest 에만 있었다"고만 말할 수 있고,
        /AP → /Resources → 공유 풀 경로는 미검증으로 남겨 두었다(docs/34 §10 쟁점 3).
        이 테스트가 그 경로를 합성해 **안전망(2단계)이 실제로 필요하다**는 것을 고정한다.
        """
        pdf_path = str(tmp_path / "shared_ap.pdf")
        _create_shared_pool_appearance_pdf(pdf_path, num_pages=3)

        # 전제: 공유 풀 탓에 단일 페이지가 문서 전체 크기다
        file_size = os.path.getsize(pdf_path)
        assert _saved_page_bytes(pdf_path, 0) > file_size * 0.9, (
            "테스트 전제: /AP 공유 풀로 단일 페이지가 원본 크기여야 함"
        )

        # 1단계(동작 키만 끊기)로는 여전히 안 줄어든다
        temps: list[str] = []
        cleaned = splitter_mod._strip_chains_copy(pdf_path, temps)
        assert _saved_page_bytes(cleaned, 0) > file_size * 0.9, (
            "1단계가 공유 풀 사슬을 끊었다면 이 테스트의 전제가 바뀐 것"
        )
        splitter_mod._cleanup(temps, keep=pdf_path)

        # 2단계까지 가면 분할된다 (주석이 사라지며 공용 풀 참조도 끊긴다)
        target_mb = (file_size / (1024 * 1024)) / 2
        result = split_pdf(pdf_path, target_mb=target_mb)

        total_pages = 0
        for part_path in result:
            assert os.path.isfile(part_path)
            doc = fitz.open(part_path)
            total_pages += doc.page_count
            doc.close()
        assert total_pages == 3, "모든 페이지가 보존되어야 함"

        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    def test_catalog_reduced_to_whitelist(self, tmp_path):
        """정리 사본에서 Type·Pages 외 카탈로그 키는 **값이 null** 이 된다.

        키 목록이 두 개로 줄어드는 것이 아니다 — xref_set_key(x, k, "null") 은 키를
        삭제하지 않고 `/k null` 을 남긴다(값이 null 이면 명세상 부재와 등가). docs/34 §3-1.
        """
        pdf_path = str(tmp_path / "tagged_cat.pdf")
        _create_tagged_pdf(pdf_path, num_pages=2)

        doc = fitz.open(pdf_path)
        root = int(doc.xref_get_key(-1, "Root")[1].split()[0])
        before = set(doc.xref_get_keys(root))
        doc.close()
        assert {"StructTreeRoot", "MarkInfo"} <= before, "테스트 전제: 뗄 키가 있어야 함"

        temps: list[str] = []
        cleaned = splitter_mod._strip_chains_copy(pdf_path, temps)

        doc = fitz.open(cleaned)
        try:
            root = int(doc.xref_get_key(-1, "Root")[1].split()[0])
            for key in doc.xref_get_keys(root):
                kind, _ = doc.xref_get_key(root, key)
                if key in splitter_mod._CATALOG_KEEP:
                    assert kind != "null", f"/{key} 는 남아야 한다"
                else:
                    assert kind == "null", f"/{key} 가 비워지지 않았다 (kind={kind})"
        finally:
            doc.close()
        splitter_mod._cleanup(temps, keep=pdf_path)

    def test_no_copy_when_split_not_needed(self, tmp_path, monkeypatch):
        """임계 이하면 원본 경로를 그대로 돌려주고 임시 파일을 하나도 만들지 않는다.

        분할이 걸리지 않는 파일(코퍼스 19개 중 14개)에 정리 비용을 물리지 않는다는 계약.
        docs/34 §4.
        """
        pdf_path = str(tmp_path / "small.pdf")
        _create_pdf(pdf_path, num_pages=5)

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

        result = split_pdf(pdf_path, target_mb=50)

        assert result == [pdf_path]
        assert created_paths == [], f"사본을 만들지 말아야 한다: {created_paths}"

    def test_widget_appearance_survives_split(self, tmp_path, monkeypatch):
        """분할을 끝까지 통과한 파트에도 위젯 주석과 외관(/AP)이 남는다.

        위젯 라벨 글자는 페이지 콘텐츠가 아니라 주석 외관에 그려져 있어, 주석을 통째로
        지우면 그 글자가 사라진다(docs/34 §2-5 — 실물에서 현대글로비스 −491자·GS건설 −388자).
        _strip_chains_copy 단계의 보존은 test_annot_actions_cleared_but_annots_kept 가 보고,
        여기서는 select()·저장을 거친 **최종 파트**까지 살아남는지를 본다.

        글자 자체가 보존되는지는 합성 PDF로 재현하지 못했다(PyMuPDF 가 합성 위젯의 /AP
        텍스트를 추출하지 않는다). 그 확인은 §8의 실물 파일 검증이 담당한다.
        """
        pdf_path = str(tmp_path / "nav_ap.pdf")
        _create_big_appearance_pdf(pdf_path, num_pages=3, ap_bytes=50_000)

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.splitter._MERGE_LIMIT_MB", 0
        )
        target_mb = (os.path.getsize(pdf_path) / (1024 * 1024)) / 2
        result = split_pdf(pdf_path, target_mb=target_mb)

        assert len(result) >= 2, "테스트 전제: 실제로 분할되어야 한다"
        kept = 0
        for part_path in result:
            doc = fitz.open(part_path)
            try:
                for page in doc:
                    for annot in splitter_mod._annot_xrefs(doc, page.xref):
                        obj = doc.xref_object(annot, compressed=True)
                        assert "/AP" in obj, "파트에서 외관(/AP)이 사라졌다"
                        kept += 1
            finally:
                doc.close()
        assert kept >= 2, f"파트에 위젯이 보존되지 않았다 (남은 주석 {kept}개)"

        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    @pytest.mark.parametrize("indirect", [False, True], ids=["direct", "indirect"])
    def test_annot_actions_cleared_but_annots_kept(self, tmp_path, indirect):
        """사슬 차단은 주석의 동작 키만 비우고 주석 객체와 외관(/AP)은 남긴다.

        주석을 통째로 지우면 위젯 외관에 그려진 글자까지 사라진다(docs/34 §2-5).
        사슬은 /A·/AA·/Dest 에만 있으므로 그것만 null 로 만든다.
        /Annots 는 직접 배열로도 간접 참조로도 오므로 둘 다 검증한다.
        """
        pdf_path = str(tmp_path / "actions.pdf")
        _create_pdf(pdf_path, num_pages=2)

        doc = fitz.open(pdf_path)
        try:
            action = doc.get_new_xref()
            doc.update_object(action, f"<</S/GoTo/D[{doc[1].xref} 0 R/Fit]>>")
            appearance = doc.get_new_xref()
            doc.update_object(appearance, "<</Type/XObject/Subtype/Form/BBox[0 0 10 10]>>")
            doc.update_stream(appearance, b"BT /F1 8 Tf (LABEL) Tj ET")
            annot = doc.get_new_xref()
            doc.update_object(
                annot,
                "<</Type/Annot/Subtype/Widget/FT/Btn/F 4/Rect[0 0 10 10]"
                f"/A {action} 0 R/AA<</D {action} 0 R>>/Dest[{doc[1].xref} 0 R/Fit]"
                f"/AP<</N {appearance} 0 R>>>>",
            )
            if indirect:
                arr = doc.get_new_xref()
                doc.update_object(arr, f"[{annot} 0 R]")
                doc.xref_set_key(doc[0].xref, "Annots", f"{arr} 0 R")
            else:
                doc.xref_set_key(doc[0].xref, "Annots", f"[{annot} 0 R]")
            doc.saveIncr()
        finally:
            doc.close()

        temps: list[str] = []
        cleaned = splitter_mod._strip_chains_copy(pdf_path, temps)

        doc = fitz.open(cleaned)
        try:
            annots = splitter_mod._annot_xrefs(doc, doc[0].xref)
            assert annots, "주석 객체가 남아 있어야 한다 (통째로 지우면 안 됨)"
            obj = doc.xref_object(annots[0], compressed=True)
            assert "/Widget" in obj, "주석 자체가 보존되어야 한다"
            assert "/AP" in obj, "외관(/AP)이 보존되어야 글자가 살아남는다"
            for key in ("A", "AA", "Dest"):
                kind, _ = doc.xref_get_key(annots[0], key)
                assert kind == "null", f"/{key} 가 끊기지 않았다 (kind={kind})"
        finally:
            doc.close()
        splitter_mod._cleanup(temps, keep=pdf_path)

    def test_annot_xrefs_handles_nonzero_generation(self, tmp_path):
        """generation 이 0 이 아닌 참조도 잡는다.

        PDF 간접 참조는 `12 3 R` 처럼 generation != 0 일 수 있고, 증분 갱신을 거친
        파일에서 실제로 나타난다. `(\\d+) 0 R` 로만 잡으면 그 주석이 목록에서 조용히 빠져
        1단계가 사슬을 끊지 못하고 손실 있는 2단계로 강등된다.
        """
        pdf_path = str(tmp_path / "gen.pdf")
        _create_pdf(pdf_path, num_pages=1)

        doc = fitz.open(pdf_path)
        try:
            annot = doc.get_new_xref()
            doc.update_object(annot, "<</Type/Annot/Subtype/Widget/Rect[0 0 10 10]>>")
            # generation 을 3 으로 만든 참조를 /Annots 에 넣는다
            doc.xref_set_key(doc[0].xref, "Annots", f"[{annot} 3 R]")
            doc.saveIncr()
        finally:
            doc.close()

        doc = fitz.open(pdf_path)
        try:
            found = splitter_mod._annot_xrefs(doc, doc[0].xref)
            assert annot in found, f"generation!=0 참조를 놓쳤다 (found={found})"
        finally:
            doc.close()

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

    def test_two_stage_cleanup_removes_first_copy(self, tmp_path, monkeypatch):
        """2단계까지 가고 2단계 사본이 그대로 파트가 되면 1단계 사본이 회수되어야 한다.

        이 설계는 사본을 최대 두 개 만든다(1단계 사슬 차단 + 2단계 주석 제거). _cleanup 은
        전부 아니면 전무라, `cleaned not in parts` 단일 가드로는 반환된 2단계 사본 때문에
        정리가 통째로 건너뛰어져 **1단계 사본(원본 크기급)이 디스크에 영구히 남는다.**
        상위 upload_pdf_parts 의 finally 도 parts 안의 경로만 지우므로 회수하지 못한다.
        docs/34 §5-4.
        """
        pdf_path = str(tmp_path / "big_ap.pdf")
        _create_big_appearance_pdf(pdf_path, num_pages=3)

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

        result = split_pdf(pdf_path, target_mb=1.0)

        # 전제: 2단계 사본이 그대로 단일 파트로 반환된 상황이어야 한다
        assert len(result) == 1 and result[0] != pdf_path, "테스트 전제: 2단계 사본이 곧 파트"
        assert len(created_paths) >= 2, "테스트 전제: 사본이 두 개 이상 만들어져야 함"

        leaked = [p for p in created_paths if p not in result and os.path.exists(p)]
        assert not leaked, f"파트가 아닌 임시 파일 leak: {leaked}"

        for part_path in result:
            if part_path != pdf_path:
                os.unlink(part_path)

    def test_chain_strip_save_failure_cleans_up_temp_files(self, tmp_path, monkeypatch):
        """사슬 차단 사본의 save 가 실패해도 임시 파일이 남지 않는다.

        _strip_chains_copy 는 save 이전에 임시 파일을 등록하므로, 사본 생성이 정리 루프
        바깥에 있으면 save 실패 시 빈 파일이 디스크에 남는다 (PR #73 리뷰가 잡았던 것과
        같은 함정 — 사본 생성 지점이 1단계로 옮겨졌을 뿐이다).
        """
        pdf_path = str(tmp_path / "nav_fail.pdf")
        _create_nav_widget_pdf(pdf_path, num_pages=3)
        target_mb = (os.path.getsize(pdf_path) / (1024 * 1024)) / 2

        # _strip_chains_copy 안에 있는 동안 = 사본 save 시점
        stripping = {"now": False}
        original_strip = splitter_mod._strip_chains_copy

        def flagging_strip(path: str, created_temps: list[str]) -> str:
            stripping["now"] = True
            try:
                return original_strip(path, created_temps)
            finally:
                stripping["now"] = False

        monkeypatch.setattr(splitter_mod, "_strip_chains_copy", flagging_strip)

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
