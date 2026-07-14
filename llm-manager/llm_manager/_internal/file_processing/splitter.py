"""PDF 분할 유틸리티.

50MB를 초과하는 PDF를 Gemini Files API 제한(PDF 50MB) 이하의 파트로 분할한다.

페이지별 바이트 기여도를 추정해 그룹핑하고, 저장 후 실제 크기가 한도를 넘는 그룹은
페이지를 이등분해 재귀적으로 더 쪼갠다. 단일 페이지가 한도를 넘으면(모든 페이지가
~50MB 공유 객체 풀을 참조하는 병리적 PDF) 더 쪼갤 수 없으므로
PdfPageTooLargeError로 거부한다.
docs/25.pdf-split-shared-pool-and-unsplittable-reject.md 참고.
(Kiwoom-ESG file_manager.split_by_size 로직 기반)
"""

from __future__ import annotations

import logging
import math
import os
import tempfile

import fitz  # PyMuPDF

from llm_manager.exceptions import PdfPageTooLargeError

logger = logging.getLogger(__name__)

_SAFETY_MARGIN = 0.95  # 5% 안전 마진
_MERGE_LIMIT_MB = 45  # 병합 시 상한 (insert_pdf 후 크기 증가 가능성 대비)
# garbage=2: unused 객체 제거 + xref 압축 (빠름, ~0.4s). garbage>=3 의 객체/스트림
# 중복 대조는 객체 수가 많은 PDF에서 분 단위로 폭발하므로 쓰지 않는다 (docs/25 §2-1).
_GARBAGE = 2


def _estimate_page_sizes(doc: fitz.Document, file_size: int) -> list[float]:
    """페이지별 바이트 기여도를 추정한다.

    각 페이지의 이미지 xref 스트림 크기 + 텍스트/벡터 콘텐츠 크기를
    합산하고, 실제 파일 크기에 비례하도록 스케일링한다.
    """
    page_sizes: list[float] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        size = 0
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                size += len(doc.xref_stream_raw(xref))
            except Exception:
                try:
                    size += len(doc.xref_stream(xref))
                except Exception:
                    pass
        # 텍스트/벡터 콘텐츠 추정
        size += len(page.read_contents())
        page_sizes.append(max(size, 1))

    # 추정값 → 실제 파일 크기에 비례하도록 스케일링
    estimated_total = sum(page_sizes)
    scale = file_size / estimated_total if estimated_total > 0 else 1
    return [s * scale for s in page_sizes]


def _group_pages(
    page_sizes: list[float], target_bytes: float, target_mb: int
) -> list[list[int]]:
    """누적 추정 크기가 target_bytes를 넘지 않도록 페이지를 그룹으로 묶는다."""
    groups: list[list[int]] = []
    current_group: list[int] = []
    current_size = 0.0
    for i, size in enumerate(page_sizes):
        if current_size + size > target_bytes and current_group:
            groups.append(current_group)
            current_group = [i]
            current_size = size
        else:
            current_group.append(i)
            current_size += size

        if size > target_bytes:
            logger.warning(
                "페이지 %d의 추정 크기(%.1fMB)가 목표(%dMB)를 초과합니다",
                i + 1,
                size / (1024 * 1024),
                target_mb,
            )

    if current_group:
        groups.append(current_group)
    return groups


def _save_pages(src_path: str, group: list[int], created_temps: list[str]) -> str:
    """src_path에서 group 페이지만 골라 임시 PDF로 저장하고 경로를 반환.

    tempfile은 save 이전에 created_temps에 등록해, save가 도중 실패해도 호출자의
    정리 루프가 회수하게 한다(leak 방지, docs/16 §3.3).
    """
    part_doc = fitz.open(src_path)
    try:
        part_doc.select(group)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)  # 분할 pdf 임시 생성
        tmp.close()
        created_temps.append(tmp.name)
        part_doc.save(tmp.name, garbage=_GARBAGE, deflate=True)
    finally:
        part_doc.close()
    return tmp.name


def _split_group(
    src_path: str,
    group: list[int],
    limit_bytes: int,
    created_temps: list[str],
    result: list[str],
) -> None:
    """group 페이지를 저장한다. 실제 크기가 한도를 넘으면 페이지를 이등분해 재귀.

    단일 페이지인데도 한도를 넘으면 더 쪼갤 수 없으므로 PdfPageTooLargeError.
    원본 페이지 인덱스를 이등분하므로 재귀는 단일 페이지에서 반드시 종료된다.
    """
    part = _save_pages(src_path, group, created_temps)
    part_size = os.path.getsize(part)
    logger.info(
        "분할: p.%d-%d (%.1fMB)",
        group[0] + 1,
        group[-1] + 1,
        part_size / (1024 * 1024),
    )

    if part_size <= limit_bytes:
        result.append(part)
        return

    # 한도 초과 — 방금 만든 파트는 버리고 더 쪼갠다
    os.unlink(part)
    created_temps.remove(part)

    if len(group) == 1:
        size_mb = part_size / (1024 * 1024)
        logger.warning(
            "분할 불가 PDF: 단일 페이지 p.%d 가 %.1fMB로 한도 초과",
            group[0] + 1,
            size_mb,
        )
        raise PdfPageTooLargeError(page=group[0] + 1, size_mb=size_mb)

    logger.warning(
        "파트(p.%d-%d) %.1fMB 초과 → 페이지 이등분 재분할",
        group[0] + 1,
        group[-1] + 1,
        part_size / (1024 * 1024),
    )
    mid = len(group) // 2
    _split_group(src_path, group[:mid], limit_bytes, created_temps, result)
    _split_group(src_path, group[mid:], limit_bytes, created_temps, result)


def _merge_adjacent(result: list[str], created_temps: list[str]) -> list[str]:
    """인접 파트를 실제 크기 기준 _MERGE_LIMIT_MB 상한으로 병합한다."""
    if len(result) <= 1:
        return result

    merge_limit_bytes = _MERGE_LIMIT_MB * 1024 * 1024
    merged: list[str] = [result[0]]
    for i in range(1, len(result)):
        prev_size = os.path.getsize(merged[-1])
        curr_size = os.path.getsize(result[i])
        if prev_size + curr_size <= merge_limit_bytes:
            doc_m = fitz.open(merged[-1])
            try:
                doc_c = fitz.open(result[i])
                try:
                    doc_m.insert_pdf(doc_c)
                finally:
                    doc_c.close()
                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                tmp.close()
                created_temps.append(tmp.name)
                doc_m.save(tmp.name, garbage=_GARBAGE, deflate=True)
            finally:
                doc_m.close()
            os.unlink(merged[-1])
            os.unlink(result[i])
            merged[-1] = tmp.name
            logger.info("병합 → %.1fMB", os.path.getsize(tmp.name) / (1024 * 1024))
        else:
            merged.append(result[i])
    return merged


def _cap_pages(
    parts: list[str],
    src_path: str,
    max_pages: int,
    limit_bytes: int,
    created_temps: list[str],
) -> list[str]:
    """각 파트가 max_pages를 넘으면 ceil(pages/max_pages) 등분해 페이지 캡.

    각 등분은 _split_group으로 저장해 크기(limit_bytes)도 함께 보장한다(공유 풀 복제로
    페이지 파트가 50MB를 넘으면 크기로 더 쪼갬). src_path(원본)는 호출자 소유라
    unlink 하지 않는다.
    """
    out: list[str] = []
    for p in parts:
        d = fitz.open(p)
        try:
            n = d.page_count
        finally:
            d.close()
        if n <= max_pages:
            out.append(p)
            continue
        k = math.ceil(n / max_pages)
        for j in range(k):
            group = list(range(j * n // k, (j + 1) * n // k))
            _split_group(p, group, limit_bytes, created_temps, out)
        if p != src_path:  # 페이지 분할로 대체된 임시 파트는 정리
            try:
                os.unlink(p)
            except OSError:
                pass
            if p in created_temps:
                created_temps.remove(p)
    return out


def _cleanup(temps: list[str], keep: str) -> None:
    """임시 파일을 회수한다. keep(원본)은 호출자 소유라 건드리지 않는다."""
    for t in set(temps):
        if t == keep:
            continue
        try:
            os.unlink(t)
        except FileNotFoundError:
            pass  # 이미 unlink 된 항목 (재분할/병합 중 제거) — 정상
        except OSError:
            logger.warning("임시 파일 정리 실패: %s", t, exc_info=True)


def _has_annots(path: str) -> bool:
    """주석을 실제로 가진 페이지가 하나라도 있는지 — 주석 제거 재시도가 의미 있는지 판단.

    /Annots 는 키 부재("null")·직접 배열("array")·간접 참조("xref") 셋 다로 온다.
    키 존재만 보면 빈 배열도 "주석 있음"이 되어, 뗄 게 없는데도 사본을 뜨고 재분할하는
    한 바퀴를 헛돈다. 간접 참조는 드문 경우가 아니라 실물 보고서가 쓰는 형태다
    (예: `/Annots 1844 0 R`).
    """
    doc = fitz.open(path)
    try:
        for page in doc:
            kind, value = doc.xref_get_key(page.xref, "Annots")
            if kind == "null":
                continue
            if kind == "xref":  # 간접 참조 → 배열 실물을 열어본다
                value = doc.xref_object(int(value.split()[0]), compressed=True)
            if value.strip() not in ("[]", ""):
                return True
        return False
    finally:
        doc.close()


def _strip_annots_copy(path: str, created_temps: list[str]) -> str:
    """모든 페이지의 /Annots 를 떼어낸 소스 사본을 만들어 경로를 반환한다.

    select() 는 남긴 페이지에서 참조로 닿는 객체를 모두 가져온다. 페이지마다 다른
    페이지로 점프하는 GoTo 위젯(인터랙티브 목차·네비게이션 버튼)이 있으면
        page → /Annots → Widget → /AA → GoTo → 다른 page → 그 page 의 이미지
    사슬을 타고 문서 전체가 딸려와, 어느 한 페이지만 남겨도 원본 크기가 된다
    (mupdf 는 Link 주석과 목적지는 재작성해 정리하지만 Widget 의 /AA 는 손대지 않는다).
    그래서 사슬을 select() 이전에 끊는다. docs/25 §3 참고.
    """
    doc = fitz.open(path)
    try:
        for page in doc:
            doc.xref_set_key(page.xref, "Annots", "null")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        created_temps.append(tmp.name)
        doc.save(tmp.name, garbage=_GARBAGE, deflate=True)
    finally:
        doc.close()
    return tmp.name


def split_pdf(path: str, target_mb: int = 50, max_pages: int = 1000) -> list[str]:
    """PDF를 target_mb 이하 AND max_pages 이하 파트로 분할한다.

    먼저 원본 그대로 분할을 시도한다(정상 PDF는 여기서 끝나며 동작이 바뀌지 않는다).
    단일 페이지가 한도를 넘어 분할 불가로 판정되면 — 주석 참조 사슬로 페이지가 부풀었을
    가능성이 크므로 — 주석을 모두 떼어낸 소스 사본으로 한 번 더 분할한다. 이 병에 걸린
    PDF는 한 페이지가 아니라 거의 모든 페이지가 같은 사슬을 갖고 있어, 페이지별로 고치면
    전 페이지를 한 장까지 이등분하게 되므로 문서 단위로 처방한다.

    그래도 한도를 넘으면(주석과 무관하게 진짜로 큰 페이지) PdfPageTooLargeError.

    결정적(deterministic): 무작위·시간 의존이 없어 같은 PDF 바이트(+같은 PyMuPDF 버전)는
    항상 같은 페이지 경계에서 분할된다 — 분할점은 페이지별 추정/실제 바이트 크기의 순수 함수다.
    주석 제거도 페이지 순서·내용을 바꾸지 않아 경계에 영향을 주지 않는다.
    (분기 판단이 save 결과 크기에 의존하므로 PyMuPDF 버전이 바뀌면 경계가 밀릴 수는 있다.)
    그래서 pdf_part_ranges 를 파일당 한 번만 계산해 Redis 에 저장·재사용해도 안전하다 —
    호출마다 재분할할 필요가 없다(YAGNI).

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB). 기본 50.
        max_pages: 파트당 최대 페이지 수. 기본 1000 (Gemini 한도).

    Returns:
        파트 파일 경로 리스트. 분할 불필요 시 [원본 경로] 반환.
        분할된 임시 파일은 호출자(uploader)가 삭제 책임.

    Raises:
        PdfPageTooLargeError: 단일 페이지가 target_mb를 초과해 분할로도 처리 불가
            (주석 제거 후에도 한도 초과).
    """
    try:
        return _split_once(path, target_mb, max_pages)
    except PdfPageTooLargeError:
        if not _has_annots(path):
            raise  # 주석이 없으니 떼어내도 달라질 게 없다 — 진짜 분할 불가
        logger.warning("단일 페이지 한도 초과 — 주석 제거 후 재분할 시도: %s", path)

    # 사본 생성도 정리 루프 안에 둔다 — _strip_annots_copy 는 save 이전에 임시 파일을
    # 등록하므로, 밖에 두면 save 실패 시 그 파일을 아무도 회수하지 않는다.
    temps: list[str] = []
    try:
        cleaned = _strip_annots_copy(path, temps)
        parts = _split_once(cleaned, target_mb, max_pages)
    except Exception:
        _cleanup(temps, keep=path)
        raise
    if cleaned not in parts:  # 사본이 파트로 그대로 반환된 경우가 아니면 회수
        _cleanup(temps, keep=path)
    return parts


def _split_once(path: str, target_mb: int, max_pages: int) -> list[str]:
    """주석을 손대지 않고 path 를 한 번 분할한다 (split_pdf 의 본체).

    크기 분할(페이지별 바이트 기여도 추정 → 그룹핑 → 초과 시 이등분 재귀)을 먼저 하고,
    결과 파트가 max_pages를 넘으면 ceil(pages/max_pages) 등분해 페이지 캡을 적용한다.
    """
    file_size = os.path.getsize(path)
    target_bytes = target_mb * 1024 * 1024 * _SAFETY_MARGIN
    limit_bytes = target_mb * 1024 * 1024

    size_over = file_size > target_bytes
    doc = fitz.open(path)
    try:  # _estimate_page_sizes raise 시에도 doc.close 보장 (docs/16 §3.3)
        n_pages = doc.page_count
        page_sizes = _estimate_page_sizes(doc, file_size) if size_over else None
    finally:
        doc.close()

    if not size_over and n_pages <= max_pages:
        return [path]

    created_temps: list[str] = []  # 만든 임시 파일 추적 — except 시 정리용 (docs/16 §3.3)
    result: list[str] = []
    try:
        if size_over:
            groups = _group_pages(page_sizes, target_bytes, target_mb)
            for group in groups:
                _split_group(path, group, limit_bytes, created_temps, result)
            result = _merge_adjacent(result, created_temps)
        else:
            result = [path]  # 크기 OK, 페이지만 초과 → 페이지 캡만
        return _cap_pages(result, path, max_pages, limit_bytes, created_temps)
    except Exception as e:
        # raise(예: PdfPageTooLargeError, save 실패) → 만든 임시 파일 모두 정리 (원본 제외).
        # PdfPageTooLargeError 는 split_pdf 가 주석 제거로 복구를 시도하는 신호라 조용히
        # 정리만 하고, 그 외 진짜 실패만 스택트레이스와 함께 남긴다.
        msg = "분할 실패 — 임시 파일 정리 (created=%d, result=%d)"
        args = (len(created_temps), len(result))
        if isinstance(e, PdfPageTooLargeError):
            logger.debug(msg, *args)
        else:
            logger.exception(msg, *args)
        _cleanup(created_temps + result, keep=path)
        raise
