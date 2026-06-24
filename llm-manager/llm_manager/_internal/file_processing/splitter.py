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


def split_pdf(path: str, target_mb: int = 50, max_pages: int = 1000) -> list[str]:
    """PDF를 target_mb 이하 AND max_pages 이하 파트로 분할한다.

    크기 분할(페이지별 바이트 기여도 추정 → 그룹핑 → 초과 시 이등분 재귀)을 먼저 하고,
    결과 파트가 max_pages를 넘으면 ceil(pages/max_pages) 등분해 페이지 캡을 적용한다.

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB). 기본 50.
        max_pages: 파트당 최대 페이지 수. 기본 1000 (Gemini 한도).

    Returns:
        파트 파일 경로 리스트. 분할 불필요 시 [원본 경로] 반환.
        분할된 임시 파일은 호출자(uploader)가 삭제 책임.

    Raises:
        PdfPageTooLargeError: 단일 페이지가 target_mb를 초과해 분할로도 처리 불가.
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
    except Exception:
        # raise(예: PdfPageTooLargeError, save 실패) → 만든 임시 파일 모두 정리 (원본 제외)
        logger.exception(
            "split_pdf 실패 — 임시 파일 정리 (created=%d, result=%d)",
            len(created_temps),
            len(result),
        )
        for t in set(created_temps) | set(result):
            if t == path:
                continue  # 원본은 호출자 소유
            try:
                os.unlink(t)
            except FileNotFoundError:
                pass  # 이미 unlink 된 항목 (재분할/병합 중 제거) — 정상
            except OSError:
                logger.warning("임시 파일 정리 실패: %s", t, exc_info=True)
        raise
