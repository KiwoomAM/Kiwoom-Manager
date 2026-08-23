"""PDF 분할 유틸리티.

50MB를 초과하는 PDF를 Gemini Files API 제한(PDF 50MB) 이하의 파트로 분할한다.

페이지별 바이트 기여도를 추정해 그룹핑하고, 저장 후 실제 크기가 한도를 넘는 그룹은
페이지를 이등분해 재귀적으로 더 쪼갠다. 단일 페이지가 한도를 넘으면(모든 페이지가
~50MB 공유 객체 풀을 참조하는 병리적 PDF) 더 쪼갤 수 없으므로
PdfPageTooLargeError로 거부한다.
docs/25.pdf-split-shared-pool-and-unsplittable-reject.md 참고.

**문서 전역 참조 사슬** — select() 는 남긴 페이지에서 참조로 닿는 객체를 모두 가져온다.
그래서 어느 한 페이지만 남겨도 문서 전체가 딸려오는 PDF 가 있고, 이때는 페이지를 이등분해도
크기가 줄지 않는다. 사슬은 한 종류가 아니고 진입점이 서로 반대다:

  · 주석 사슬 (페이지발)   page → /Annots → Widget → /AA → GoTo → 다른 page
  · 구조트리 사슬 (루트발) Root → /StructTreeRoot → StructElem → /Pg → 모든 page

주석 사슬은 페이지 쪽을 청소하면 끊기지만, 구조트리 사슬은 반대편 끝인 카탈로그가 붙잡고
있어 페이지를 아무리 청소해도 끊기지 않는다. 그래서 split_pdf 는 select() 이전에 카탈로그와
주석 동작을 함께 정리한다(_strip_chains_copy).
docs/34.pdf-split-global-reference-chains.md 참고.
"""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile

import fitz  # PyMuPDF

from llm_manager.exceptions import PdfPageTooLargeError

logger = logging.getLogger(__name__)

_SAFETY_MARGIN = 0.95  # 5% 안전 마진
_MERGE_LIMIT_MB = 45  # 병합 시 상한 (insert_pdf 후 크기 증가 가능성 대비)
# garbage=2: unused 객체 제거 + xref 압축 (빠름, ~0.4s). garbage>=3 의 객체/스트림
# 중복 대조는 객체 수가 많은 PDF에서 분 단위로 폭발하므로 쓰지 않는다 (docs/25 §2-1).
_GARBAGE = 2
# 사본에서 남길 카탈로그 키. select() 후 살아남는 키는 Type·Pages·Names·StructTreeRoot 뿐이고
# 분할 파트에 필요한 것은 앞의 둘이다. 열거식 블랙리스트("위험한 키만 뗀다")는 병을 만날 때마다
# 자라므로 화이트리스트로 닫는다. docs/34 §3-2 참고.
_CATALOG_KEEP = ("Type", "Pages")
# 주석에서 끊을 키 — 동작(/A·/AA)과 목적지(/Dest). 외관(/AP)은 건드리지 않는다: 위젯 라벨
# 글자가 거기 그려져 있어, 주석을 통째로 지우면 그 글자까지 사라진다. docs/34 §2-5·§2-6.
_ACTION_KEYS = ("A", "AA", "Dest")


def _target_bytes(target_mb: float) -> float:
    """분할 개시 임계(바이트). 안전 마진 포함 — 판정이 갈리지 않게 한 곳에서만 계산한다."""
    return target_mb * 1024 * 1024 * _SAFETY_MARGIN


def _needs_split(path: str, target_mb: float, max_pages: int) -> bool:
    """사본을 만들기 전에 분할이 필요한지 판정한다 (docs/34 §4).

    크기 임계는 _split_once 와 같은 _target_bytes 에서 유도한다 (SSOT).
    """
    if os.path.getsize(path) > _target_bytes(target_mb):
        return True
    doc = fitz.open(path)
    try:
        return doc.page_count > max_pages
    finally:
        doc.close()


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
    page_sizes: list[float], target_bytes: float, target_mb: float
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


def _annot_xrefs(doc: fitz.Document, page_xref: int) -> list[int]:
    """페이지 /Annots 가 가리키는 주석 객체 xref 목록.

    /Annots 는 직접 배열("array")로도 간접 참조("xref")로도 온다 — 간접이면 배열 실물을
    열어야 한다(_has_annots 와 같은 이유). 실물 보고서가 쓰는 형태다(예: /Annots 1844 0 R).

    참조의 generation 은 0 이 아닐 수 있다(증분 갱신을 거친 파일). `(\\d+) \\d+ R` 로 넓게
    잡는다 — 이 문맥은 참조만 담기므로 오탐 위험이 사실상 없다.
    """
    kind, value = doc.xref_get_key(page_xref, "Annots")
    if kind == "null":
        return []
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    return [int(m) for m in re.findall(r"(\d+) \d+ R", value)]


def _strip_chains_copy(path: str, created_temps: list[str]) -> str:
    """문서 전역 참조 사슬만 끊은 사본을 만들어 경로를 반환한다 (무손실).

    카탈로그에서 _CATALOG_KEEP 외의 키를 모두 null 로 만든다. 사슬의 실물은
    /StructTreeRoot 다 — 태그드 PDF 의 구조트리 노드가 /Pg 로 전 페이지를 지목하므로,
    카탈로그가 살아 있는 한 어느 한 페이지만 남겨도 문서 전체가 딸려온다. 주석 사슬과 달리
    진입점이 페이지가 아니라 문서 루트라, 페이지 쪽을 아무리 청소해도 끊기지 않는다.
    docs/34 §2-2·§3-1 참고.

    xref_set_key(x, k, "null") 은 키를 삭제하지 않고 `/k null` 을 남긴다(값이 null 이면
    명세상 부재와 등가). 없던 키에 쓰면 그 키를 새로 만들므로 있는 키만 지정한다.

    tempfile 은 save 이전에 created_temps 에 등록해, save 가 도중 실패해도 호출자의
    정리 루프가 회수하게 한다 (leak 방지).
    """
    doc = fitz.open(path)
    try:
        root = int(doc.xref_get_key(-1, "Root")[1].split()[0])
        stripped = [k for k in doc.xref_get_keys(root) if k not in _CATALOG_KEEP]
        for key in stripped:
            doc.xref_set_key(root, key, "null")
        # 어떤 파일의 렌더링이 이상할 때 무엇을 뗐는지 남긴다. 화이트리스트가 과하게 떼면
        # 거부가 아니라 조용한 손실로 나타나므로(예: /AcroForm + NeedAppearances) 빵부스러기가 된다.
        logger.debug("카탈로그 정리: %s", stripped)
        for page in doc:
            # 페이지 열기 동작도 다른 페이지로 갈 수 있다 (코퍼스 0건 — 예방, docs/34 §10)
            if doc.xref_get_key(page.xref, "AA")[0] != "null":
                doc.xref_set_key(page.xref, "AA", "null")
            for annot in _annot_xrefs(doc, page.xref):
                for key in _ACTION_KEYS:
                    if doc.xref_get_key(annot, key)[0] != "null":
                        doc.xref_set_key(annot, key, "null")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        created_temps.append(tmp.name)
        doc.save(tmp.name, garbage=_GARBAGE, deflate=True)
    finally:
        doc.close()
    return tmp.name


def _strip_annots_copy(path: str, created_temps: list[str]) -> str:
    """모든 페이지의 /Annots 를 떼어낸 소스 사본을 만들어 경로를 반환한다.

    **2단계 안전망 — 손실이 있으므로 마지막에만 쓴다.** 1차 처방은 _strip_chains_copy 다
    (주석 객체와 외관은 남기고 동작 키만 끊는다). 이 함수는 주석을 통째로 지우므로 위젯
    외관(/AP)에 그려진 라벨 글자까지 함께 잃는다 — 실물에서 현대글로비스 −491자,
    GS건설 −388자였다(docs/34 §2-5).

    그럼에도 필요한 이유는 동작 키만으로 안 끊기는 사슬이 있기 때문이다. /AP 는 Form
    XObject 라 자기 /Resources 로 문서 공용 풀을 참조할 수 있고, 그러면 페이지를 참조하지
    않고도 바이트가 딸려온다(docs/34 §2-6, docs/25 §2-3 의 공유 풀 병과 같은 형태).

    원래 이 함수가 겨냥한 것은 GoTo 위젯 사슬이다 — mupdf 는 Link 주석과 목적지는 재작성해
    정리하지만 Widget 의 /AA 는 손대지 않는다. docs/25 §6 참고.
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


def split_pdf(path: str, target_mb: float = 50, max_pages: int = 1000) -> list[str]:
    """PDF를 target_mb 이하 AND max_pages 이하 파트로 분할한다.

    분할이 걸리지 않으면(_needs_split) 원본 경로를 그대로 돌려주고 사본을 만들지 않는다.

    걸리면 select() 이전에 문서 전역 참조 사슬을 먼저 끊는다. 사슬은 두 종류이고 진입점이
    서로 반대다 — 주석 사슬은 페이지가 옆 페이지를 붙잡고(page → /Annots → Widget → /AA →
    GoTo → 다른 page), 구조트리 사슬은 문서 루트가 전 페이지를 붙잡는다(Root →
    /StructTreeRoot → StructElem → /Pg → 모든 page). 어느 쪽이든 한 페이지만 남겨도 문서
    전체가 딸려와, 페이지를 이등분해도 크기가 줄지 않는다.

    1단계는 무손실이다 — 카탈로그를 정리하고(_strip_chains_copy) 분할한다. 그래도 단일
    페이지가 한도를 넘고 뗄 주석이 있으면, 2단계로 주석을 통째로 떼어(_strip_annots_copy)
    한 번 더 분할한다. 2단계는 위젯 외관(/AP)에 그려진 글자를 함께 잃으므로 마지막에만 쓴다.
    그래도 넘으면 PdfPageTooLargeError. docs/34 참고.

    결정적(deterministic): 무작위·시간 의존이 없어 같은 PDF 바이트(+같은 PyMuPDF 버전)는
    항상 같은 페이지 경계에서 분할된다 — 분할점은 페이지별 추정/실제 바이트 크기의 순수 함수다.
    사슬 차단도 페이지 순서·내용을 바꾸지 않아 경계에 영향을 주지 않는다.
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
            (사슬 차단·주석 제거 후에도 한도 초과).
    """
    if not _needs_split(path, target_mb, max_pages):
        return [path]  # 사본을 만들지 않는다 (docs/34 §4)

    # 사본 생성을 정리 루프 안에 둔다 — _strip_*_copy 는 save 이전에 임시 파일을 등록하므로,
    # 밖에 두면 save 실패 시 그 파일을 아무도 회수하지 않는다.
    temps: list[str] = []
    try:
        cleaned = _strip_chains_copy(path, temps)  # 1단계 — 무손실
        try:
            parts = _split_once(cleaned, target_mb, max_pages)
        except PdfPageTooLargeError:
            if not _has_annots(cleaned):
                raise  # 뗄 주석이 없으니 2단계가 무의미하다 — 진짜 분할 불가
            logger.warning("무손실 정리로도 한도 초과 — 주석 제거 후 재분할: %s", path)
            cleaned = _strip_annots_copy(cleaned, temps)  # 2단계 — 손실 있음
            parts = _split_once(cleaned, target_mb, max_pages)
    except Exception:
        _cleanup(temps, keep=path)
        raise

    # 파트로 반환된 사본만 남기고 나머지를 회수한다. 사본이 최대 두 개(1단계·2단계)이므로
    # `cleaned not in parts` 단일 가드로는, 2단계 사본이 그대로 파트가 될 때 정리가 통째로
    # 건너뛰어져 1단계 사본이 남는다. 파트 삭제 책임은 호출자 소유라 파트는 건드리지 않는다.
    _cleanup([t for t in temps if t not in parts], keep=path)
    return parts


def _split_once(path: str, target_mb: float, max_pages: int) -> list[str]:
    """주석을 손대지 않고 path 를 한 번 분할한다 (split_pdf 의 본체).

    크기 분할(페이지별 바이트 기여도 추정 → 그룹핑 → 초과 시 이등분 재귀)을 먼저 하고,
    결과 파트가 max_pages를 넘으면 ceil(pages/max_pages) 등분해 페이지 캡을 적용한다.
    """
    file_size = os.path.getsize(path)
    target_bytes = _target_bytes(target_mb)  # _needs_split 과 같은 식 (SSOT)
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
