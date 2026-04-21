"""PDF 분할 유틸리티.

50MB를 초과하는 PDF를 Gemini Files API 제한(PDF 50MB) 이하의
파트로 분할한다.

페이지별 바이트 기여도를 추정하여 누적 크기 기반으로 그룹핑.
(Kiwoom-ESG file_manager.split_by_size 로직 기반)
"""

from __future__ import annotations

import logging
import os
import tempfile

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

_SAFETY_MARGIN = 0.95  # 5% 안전 마진
_MERGE_LIMIT_MB = 45  # 병합 시 상한 (insert_pdf 후 크기 증가 가능성 대비)
_MAX_DEPTH = 3  # 재분할 최대 깊이


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


def split_pdf(path: str, target_mb: int = 50, *, _depth: int = 0) -> list[str]:
    """PDF를 target_mb 이하 파트로 분할한다.

    페이지별 바이트 기여도를 추정하여 누적 크기가 target_mb를
    초과하지 않도록 그룹핑한 후 분할한다.
    초과하는 파트는 재귀적으로 재분할 (최대 _MAX_DEPTH회).

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB). 기본 50.

    Returns:
        파트 파일 경로 리스트. 분할 불필요 시 [원본 경로] 반환.
        분할된 임시 파일은 호출자(uploader)가 삭제 책임.
    """
    file_size = os.path.getsize(path)
    target_bytes = target_mb * 1024 * 1024 * _SAFETY_MARGIN

    if file_size <= target_bytes:
        return [path]

    if _depth >= _MAX_DEPTH:
        logger.warning(
            "재분할 한도(%d) 초과: %s (%.1fMB) — 현재 크기로 진행",
            _MAX_DEPTH,
            os.path.basename(path),
            file_size / (1024 * 1024),
        )
        return [path]

    doc = fitz.open(path)

    # 페이지별 바이트 추정
    page_sizes = _estimate_page_sizes(doc, file_size)
    doc.close()

    # 누적 크기 기반 그룹핑
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

    # 그룹별 PDF 저장 + 초과 시 재분할
    limit_bytes = target_mb * 1024 * 1024
    result: list[str] = []
    for idx, group in enumerate(groups):
        part_doc = fitz.open(path)
        try:
            part_doc.select(group)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) # 분할 pdf 임시 생성
            tmp.close()
            part_doc.save(tmp.name, garbage=4, deflate=True)
        finally:
            part_doc.close()

        part_size = os.path.getsize(tmp.name)
        part_mb = part_size / (1024 * 1024)
        logger.info(
            "분할 %d/%d: p.%d-%d (%.1fMB)",
            idx + 1,
            len(groups),
            group[0] + 1,
            group[-1] + 1,
            part_mb,
        )

        if part_size > limit_bytes:
            logger.warning("파트 %d이 %.1fMB로 초과 → 재분할", idx + 1, part_mb)
            sub_paths = split_pdf(tmp.name, target_mb, _depth=_depth + 1)
            os.unlink(tmp.name)
            result.extend(sub_paths)
        else:
            result.append(tmp.name)

    # 후처리: 인접 파트 병합 (실제 크기 기준, 45MB 상한)
    merge_limit_bytes = _MERGE_LIMIT_MB * 1024 * 1024
    if len(result) > 1:
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
                    doc_m.save(tmp.name, garbage=4, deflate=True)
                finally:
                    doc_m.close()
                os.unlink(merged[-1])
                os.unlink(result[i])
                merged[-1] = tmp.name
                logger.info(
                    "병합: 파트 %d+%d → %.1fMB",
                    len(merged),
                    len(merged) + 1,
                    os.path.getsize(tmp.name) / (1024 * 1024),
                )
            else:
                merged.append(result[i])
        result = merged

    return result
