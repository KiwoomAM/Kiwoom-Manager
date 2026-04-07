"""PDF 분할 유틸리티.

50MB를 초과하는 PDF를 Gemini Files API 제한(PDF 50MB) 이하의
파트로 균등 분할한다.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def split_pdf(path: str, target_mb: int = 50) -> list[str]:
    """PDF를 target_mb 이하 파트로 균등 분할한다.

    페이지가 균일한 크기라고 가정하고 ceil(파일크기/target_mb)등분.
    분할 후 실제 크기가 초과하는 파트는 재균등 분할한다.

    Args:
        path: PDF 파일 경로.
        target_mb: 파트당 최대 크기 (MB). 기본 50.

    Returns:
        파트 파일 경로 리스트. 분할 불필요 시 [원본 경로] 반환.
        분할된 임시 파일은 호출자(uploader)가 삭제 책임.
    """
    file_size = os.path.getsize(path)
    target_bytes = target_mb * 1024 * 1024

    if file_size <= target_bytes:
        return [path]

    doc = fitz.open(path)
    total_pages = len(doc)
    doc.close()

    # 균등 분할: ceil(파일크기 / target) 등분
    num_parts = math.ceil(file_size / target_bytes)
    pages_per_part = math.ceil(total_pages / num_parts)

    # 페이지 그룹 생성
    groups: list[list[int]] = []
    for i in range(0, total_pages, pages_per_part):
        groups.append(list(range(i, min(i + pages_per_part, total_pages))))

    # 그룹별 PDF 저장 + 초과 시 재분할
    result: list[str] = []
    for idx, group in enumerate(groups):
        part_doc = fitz.open(path)
        part_doc.select(group)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        part_doc.save(tmp.name, garbage=4, deflate=True)
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

        if part_size > target_bytes:
            logger.warning("파트 %d이 %.1fMB로 초과 → 재분할", idx + 1, part_mb)
            sub_paths = split_pdf(tmp.name, target_mb)
            os.unlink(tmp.name)
            result.extend(sub_paths)
        else:
            result.append(tmp.name)

    return result
