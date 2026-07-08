"""파일 처리 진입점.

files + llm_input 받아서 (inline_parts, uri_parts, cached_content)를 반환하는
부분 섞기 흐름. 각 원소는 FileInput (meta + bytes)로, 에이전트가 준 bytes를
그대로 LLM 호출 payload에 사용한다.

cache stampede dedup (docs/08.dedup-design-and-impl.md):
- files_api 업로드, context_cache 생성 시점에 Redis standby 선점
- 다른 요청이 이미 작업 중이면 exponential backoff로 대기

상세 설계: docs/07.inline-vs-files-api-strategy.md §4, §5, §6, §7
             docs/08.dedup-design-and-impl.md §6 (모듈 구조)
             docs/09.file-manager-integration.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass

from file_manager import FileManagerHandler

from llm_manager._internal.file_processing import cache_store, dedup
from llm_manager._internal.file_processing.splitter import split_pdf
from llm_manager._internal.file_processing.uploader import create_context_cache, upload_pdf_parts
from llm_manager._internal.gemini_errors import is_cache_too_small
from llm_manager._internal.handler_client.aio import upload_file
from llm_manager._internal.handler_client.exceptions import HandlerConnectionError, HandlerError
from llm_manager._internal.handler_client.models import FileRef, Part
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.stored_file_resolver import fetch_with_fallback
from llm_manager.exceptions import CacheError, FileUploadError, PdfPageTooLargeError
from llm_manager.models import FileInput

logger = logging.getLogger(__name__)


# ── 합계 한도 상수 (§4.1 Step 2 — 모달리티 인지) ──────────────

_INLINE_LIMIT_WITH_IMAGE_AUDIO = 20 * 1024 * 1024  # 20 MB
_INLINE_LIMIT_PDF_OR_TEXT_ONLY = 50 * 1024 * 1024  # 50 MB

# Files API 경로 — Gemini PDF 공식 한도, 초과 시 splitter.split_pdf로 분할
_PDF_SPLIT_LIMIT = 50 * 1024 * 1024  # 50 MB

# context cache too-small 선제 게이트 (§4.4.1) — 캐시 최소 토큰 + 모달리티 환산
_CACHE_MIN_TOKENS = 1024  # Gemini Context Cache 최소 합계 토큰
_PDF_TOKENS_PER_PAGE = 258  # Gemini 문서: PDF 페이지당 258 토큰 (flat)
_IMAGE_TOKENS_PER_TILE = 258  # Gemini 문서: 이미지 타일당 258 토큰
_IMAGE_TILE_PX = 768  # 이미지는 768×768 타일로 분할 (작은 이미지=1타일)


def _inline_total_limit(pool: list[FileInput]) -> int:
    """inline 후보 합계 한도 — image/audio/video 포함 여부에 따라 결정.

    image·audio·video 중 하나라도 inline 후보에 있으면 엄격한 20MB
    (Gemini 인라인 미디어는 요청 총 20MB 권장 — 비디오 포함),
    PDF/텍스트만이면 완화된 50MB.
    """
    has_media = any(
        fi.mime_type.startswith(("image/", "audio/", "video/")) for fi in pool
    )
    return _INLINE_LIMIT_WITH_IMAGE_AUDIO if has_media else _INLINE_LIMIT_PDF_OR_TEXT_ONLY


# ── 비파일 부분 byte 추정 (§4.1 Step 2) ──────────────────────


def _estimate_non_file_bytes(llm_input: dict) -> int:
    """파일 Part를 제외한 llm_input의 JSON 직렬화 byte 수 근사.

    classify_files 호출 시점에는 llm_input에 파일 Part가 들어있지 않으므로
    단순 직렬화 크기로 충분.
    """
    try:
        return len(json.dumps(llm_input, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        logger.exception(
            "_estimate_non_file_bytes 실패 — llm_input JSON 직렬화 불가. "
            "0 반환 (inline 한도 계산이 낙관적이 될 수 있음). "
            "이 상태면 llm_input 자체가 정상이 아닐 가능성이 높음"
        )
        return 0


# ── 부분 섞기 분류 (§4.1) ────────────────────────────────────


def classify_files(
    files: list[FileInput],
    llm_input: dict,
) -> tuple[list[FileInput], list[FileInput]]:
    """FileInput 리스트를 inline_pool / files_api_pool로 분류 (부분 섞기).

    모든 파일이 inline 후보다. 합계 한도(모달리티 인지 — 미디어 있으면 20MB,
    PDF/텍스트만 50MB)를 작은 것부터 채우고, 넘치는 파일은 files_api_pool로 민다.
    20MB 초과 비디오·대형 PDF는 자연히 합계를 초과해 files_api로 밀리므로 별도
    선분리가 필요 없다. PDF 50MB 초과 분할은 이후 Files API 업로드 단계에서
    splitter가 처리.
    """
    inline_pool: list[FileInput] = list(files)
    files_api_pool: list[FileInput] = []

    # 합계 한도 결정 (모달리티 인지 — video 포함)
    limit = _inline_total_limit(inline_pool)

    # 작은 것부터 정렬 + 한 번 순회 (재평가 없음)
    inline_pool.sort(key=lambda fi: fi.file_size)
    total = _estimate_non_file_bytes(llm_input)
    kept: list[FileInput] = []
    for fi in inline_pool:
        if total + fi.file_size <= limit:
            kept.append(fi)
            total += fi.file_size
        else:
            files_api_pool.append(fi)

    return kept, files_api_pool


# ── inline 분류 처리 (§6.1) ──────────────────────────────────


async def _process_inline(inline_pool: list[FileInput]) -> list[Part]:
    """inline 분류 파일을 bytes로 Part.from_bytes 구성. fi.data 비었으면 file-manager 에서 fetch.

    Redis 전혀 접근 안 함 (inline은 Files API URI/context_cache 와 무관).
    상세: docs/07.inline-vs-files-api-strategy.md §6.1
    """
    fm = FileManagerHandler()
    inline_parts: list[Part] = []
    for fi in inline_pool:
        if not fi.data:
            fi.data = await fetch_with_fallback(fm, fi)
            if fi.resolved_via is None:
                fi.resolved_via = "primary"  # fetch 검증 완료
        inline_parts.append(
            Part.from_bytes(data=fi.data, mime_type=fi.mime_type)
        )
        logger.info("[route] file=%s mode=inline", fi.file_name)
    return inline_parts


# ── files_api 헬퍼 ───────────────────────────────────────────


def _count_pdf_pages(data: bytes) -> int | None:
    """PDF 바이트의 페이지 수 (fitz/PyMuPDF). 깨진/비-PDF 면 None.

    too-small 선제 게이트(§4.4.1)의 토큰 어림에 쓰인다. None 은 '추정 불가'로,
    게이트는 skip하지 않고 create 를 시도한다 (fail-soft backstop).
    """
    try:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        return None


def _estimate_image_tokens(data: bytes) -> int | None:
    """이미지 바이트 → Gemini 타일 토큰 어림 (fitz/PyMuPDF). 깨진/비-이미지면 None.

    Gemini 는 이미지를 768×768 픽셀 타일로 나눠 타일당 258 토큰(작은 이미지=1타일=258).
    too-small 게이트(§4.4.1)용 — 산식 ceil(w/768)×ceil(h/768)×258 이 Gemini 타일 과금과
    동일해 실측과 같다(과대 > 가 아니라 하한 보장 ≥ — Gemini 가 대형 이미지를 축소하면 그 이상).
    하한이 보장(잘못 skip 은 estimate<actual 일 때만 발생)되므로 캐시 가능한 이미지를 잘못 skip 안 함.
    None 은 '추정 불가' → 게이트 통과(create 시도, fail-soft backstop).
    """
    try:
        import math

        import fitz

        pix = fitz.Pixmap(data)
        tiles = math.ceil(pix.width / _IMAGE_TILE_PX) * math.ceil(pix.height / _IMAGE_TILE_PX)
        return max(1, tiles) * _IMAGE_TOKENS_PER_TILE
    except Exception:
        return None


def _split_pdf_bytes(data: bytes, *, max_pages: int = 1000) -> list[bytes]:
    """PDF bytes를 ≤50MB AND ≤max_pages 파트 bytes로 분할 (tempfile 경유).

    인라인 경로에서 >1000p PDF를 메모리상으로 쪼갤 때 사용. path 기반 split_pdf를
    재사용하므로 임시 파일에 쓰고 결과 파트를 다시 bytes로 읽는다.
    """
    src = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        src.write(data)
        src.close()
        part_paths = split_pdf(src.name, max_pages=max_pages)
        blobs: list[bytes] = []
        try:
            for p in part_paths:
                with open(p, "rb") as f:
                    blobs.append(f.read())
        finally:
            for p in part_paths:
                if p != src.name:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        return blobs
    finally:
        try:
            os.unlink(src.name)
        except OSError:
            pass


async def _expand_pdfs_over_page_limit(pool: list[FileInput]) -> list[FileInput]:
    """inline 후보 중 >1000p PDF를 ≤1000p part FileInput들로 치환 (메모리 분할, docs/28 §5.4).

    bytes를 미리 확보하므로 이후 처리에서 재fetch하지 않는다. 비-PDF·≤1000p·측정불가
    PDF는 그대로 둔다. 반환 리스트는 classify_files로 재분류돼 inline/files_api 폴백된다.
    """
    fm = FileManagerHandler()
    out: list[FileInput] = []
    for fi in pool:
        if fi.mime_type != "application/pdf":
            out.append(fi)
            continue
        if not fi.data:
            fi.data = await fetch_with_fallback(fm, fi)
            if fi.resolved_via is None:
                fi.resolved_via = "primary"  # fetch 검증 완료
        pages = fi.page_count if fi.page_count is not None else _count_pdf_pages(fi.data)
        fi.page_count = pages  # stash → 재호출(404 복구 등) 시 재파싱 방지 (리뷰 ②)
        if pages is None or pages <= 1000:
            out.append(fi)
            continue
        n_parts = 0
        for idx, blob in enumerate(_split_pdf_bytes(fi.data), start=1):
            n_parts = idx
            out.append(
                FileInput(
                    file_name=f"{fi.file_name}#part{idx}",
                    file_size=len(blob),
                    file_mtime=fi.file_mtime,
                    mime_type="application/pdf",
                    extension="pdf",
                    data=blob,
                    file_path=fi.file_path,
                    resolved_via=fi.resolved_via,
                    page_count=_count_pdf_pages(blob),
                )
            )
        logger.info(
            "[route] file=%s inline PDF %dp → %d parts (page-split)",
            fi.file_name, pages, n_parts,
        )
    return out


def _reuse_files_api_uris(
    fi: FileInput,
    existing: dict,
    uri_parts: list[Part],
    file_refs: list[FileRef],
) -> None:
    """기존 Redis URI 재사용 (분기 B) — uri_parts/file_refs에 append."""
    for uri in existing["uris"]:
        uri_parts.append(Part.from_uri(uri, mime_type=fi.mime_type))
        file_refs.append(
            FileRef(name="", uri=uri, mime_type=fi.mime_type, state="ACTIVE")
        )
    logger.info(
        "[route] file=%s mode=files_api source=cached uris=%d",
        fi.file_name, len(existing["uris"]),
    )
    # Redis 에 stash 된 PDF 페이지 수 / 이미지 토큰 복원 → too-small 게이트(§4.4.1)가 HIT/병렬 재호출에서도 동작
    fi.page_count = existing.get("page_count")
    fi.image_tokens = existing.get("image_tokens")
    # 분할 PDF 파트별 원본 페이지 범위 복원 (page_count 와 대칭) — 없으면 HIT 응답에 사라져 소비자의
    # 절대페이지 환산이 조용히 깨진다(PR #62 리뷰). Redis JSON 은 튜플→리스트라 다시 list[tuple] 로 재구성(계약 유지).
    _ranges = existing.get("pdf_part_ranges")
    fi.pdf_part_ranges = [tuple(r) for r in _ranges] if _ranges else None
    fi.data = b""  # Files API 경로(cache HIT) — bytes 더 이상 불필요 (docs/16 §3.2)


async def _upload_and_cache_file(
    fi: FileInput,
    checksum: str,
    uri_parts: list[Part],
    file_refs: list[FileRef],
) -> None:
    """Files API Redis 조회 후 miss 면 호출 — file-manager 에서 fetch → 업로드 → Redis 저장."""
    if not fi.data:
        fm = FileManagerHandler()
        fi.data = await fetch_with_fallback(fm, fi)
        if fi.resolved_via is None:
            fi.resolved_via = "primary"  # fetch 검증 완료
    # PDF 페이지 수 / 이미지 토큰 stash (bytes 보유 시점 — 곧 비워짐) → too-small 게이트(§4.4.1)
    if fi.mime_type == "application/pdf" and fi.page_count is None:
        fi.page_count = _count_pdf_pages(fi.data)
    elif fi.mime_type.startswith("image/") and fi.image_tokens is None:
        fi.image_tokens = _estimate_image_tokens(fi.data)
    if not fi.extension:
        logger.warning(
            "파일 확장자 없음: file_name=%s — 임시 파일 suffix 없이 업로드 진행",
            fi.file_name,
        )
    suffix = f".{fi.extension}" if fi.extension else ""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    t0 = time.monotonic()
    try:
        tmp.write(fi.data)
        tmp.close()
        try:
            if fi.mime_type == "application/pdf" and (
                fi.file_size > _PDF_SPLIT_LIMIT or (fi.page_count or 0) > 1000
            ):
                refs, pdf_part_ranges = await upload_pdf_parts(tmp.name)
                # 분할된 경우 파트별 원본 페이지 범위를 fi 에 실어 file_meta 로 노출 (소비자의
                # (파트, 로컬쪽)→절대쪽 환산용). 비분할이면 None 이라 굳이 덮지 않는다.
                if pdf_part_ranges is not None:
                    fi.pdf_part_ranges = pdf_part_ranges
            else:
                refs = [await upload_file(tmp.name, mime_type=fi.mime_type)]
        except (HandlerError, HandlerConnectionError, PdfPageTooLargeError):
            # llm-handler 에러 + 분할 불가 PDF(422)는 묶지 말고 그대로 전파 — caller 가
            # status_code/error_type 으로 분기할 수 있게 (전역 핸들러가 relay).
            raise
        except Exception as e:
            # 진짜 로컬 file 처리 실패 (tempfile I/O 등) 만 file 502 로 wrap.
            logger.exception(
                "Files API 업로드 실패: file_name=%s, mime=%s",
                fi.file_name, fi.mime_type,
            )
            raise FileUploadError(
                f"Files API 업로드 실패: file_name={fi.file_name}, {e}"
            ) from e
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    duration_ms = int((time.monotonic() - t0) * 1000)
    uris = [r.uri for r in refs]
    dynamic_ttl = cache_store.compute_files_api_ttl(refs)
    try:
        cache_store.save_files_api(
            checksum,
            {
                "file_checksum": checksum,
                "file_name": fi.file_name,
                "file_size": fi.file_size,
                "mime_type": fi.mime_type,
                "uris": uris,
                "split": len(refs) > 1,
                "page_count": fi.page_count,
                "image_tokens": fi.image_tokens,
                "pdf_part_ranges": fi.pdf_part_ranges,
            },
            ttl=dynamic_ttl,
        )
    except Exception as e:
        logger.exception("Redis files_api 저장 실패: checksum=%s", checksum[:16])
        raise CacheError(f"Redis files_api 저장 실패: {e}") from e

    for r in refs:
        uri_parts.append(Part.from_uri(r.uri, mime_type=r.mime_type))
        file_refs.append(r)
    logger.info(
        "[route] file=%s mode=files_api source=live parts=%d duration_ms=%d",
        fi.file_name, len(refs), duration_ms,
    )
    fi.data = b""  # Files API 경로 — bytes 가 디스크/URI 로 옮겨졌으니 메모리 회수 (docs/16 §3.2)


async def _resolve_one_files_api(
    fi: FileInput,
    checksum: str,
    *,
    standby_ttl_seconds: int,
) -> tuple[list[Part], list[FileRef]]:
    """파일 1개를 files_api 경로로 처리 → (uri_parts, file_refs).

    `_process_files_api` 의 파일별 루프 몸통을 추출한 것으로 동작은 동일하다:
    Redis HIT 재사용 / MISS standby 선점 업로드 / waiter 대기 / timeout Y' Hybrid.
    호출자마다 새 리스트를 반환하므로, 파일 단위 그룹핑(file_preprocessing)이 가능하다.
    """
    uri_parts: list[Part] = []
    file_refs: list[FileRef] = []
    try:
        existing = cache_store.find_files_api(checksum)
    except Exception as e:
        logger.exception("Redis files_api 조회 실패: checksum=%s", checksum[:16])
        raise CacheError(f"Redis files_api 조회 실패: {e}") from e

    if existing:
        _reuse_files_api_uris(fi, existing, uri_parts, file_refs)
        return uri_parts, file_refs

    # MISS — standby 선점 시도
    acquired = await dedup.try_acquire_standby(
        "files_api", checksum, standby_ttl_seconds,
    )
    if acquired:
        try:
            await _upload_and_cache_file(fi, checksum, uri_parts, file_refs)
        finally:
            await dedup.release_standby("files_api", checksum)
    else:
        # 다른 요청이 업로드 중 → 대기
        cached_file = await dedup.wait_for_standby_release(
            "files_api", checksum, standby_ttl_seconds,
            find_cache_fn=lambda c=checksum: cache_store.find_files_api(c),
        )
        if cached_file:
            _reuse_files_api_uris(fi, cached_file, uri_parts, file_refs)
        else:
            # timeout → 자기가 업로드 (Y' Hybrid)
            logger.warning(
                "files_api standby timeout → 자기가 업로드 (중복 가능): checksum=%s",
                checksum[:16],
            )
            await _upload_and_cache_file(fi, checksum, uri_parts, file_refs)
    return uri_parts, file_refs


# ── context_cache 헬퍼 ───────────────────────────────────────


def _estimate_cache_tokens(
    files: list[FileInput], system_instruction: str | None,
) -> int | None:
    """캐시될 콘텐츠(system + 파일들)의 합계 토큰을 로컬로 과대평가 (§4.4.1).

    too-small 선제 게이트용 — 반환값이 `_CACHE_MIN_TOKENS` 미만이면 create 를 skip한다.
    과대평가 방향이라 "캐시 가능한 콘텐츠를 잘못 skip"하지 않는다. 추정 불가(스코프 외
    모달리티 / PDF page_count 미상)면 `None` → 게이트는 skip하지 않고 create 를 시도한다
    (fail-soft 가 backstop).

    환산 (Gemini 공식): PDF=`258×page_count`, 텍스트=`file_size//3`(바이트 기반 보수),
    이미지=`image_tokens`(stash, 타일수×258), system=`len//3`. 오디오/비디오는 스코프 외 → None.
    이미지도 `image_tokens` 미상(None)이면 None(추정 불가 — 게이트 통과, fail-soft).
    """
    total = len(system_instruction) // 3 if system_instruction else 0
    for fi in files:
        if fi.mime_type == "application/pdf":
            if fi.page_count is None:
                return None
            total += _PDF_TOKENS_PER_PAGE * fi.page_count
        elif fi.mime_type.startswith("text/"):
            total += fi.file_size // 3
        elif fi.mime_type.startswith("image/"):
            if fi.image_tokens is None:
                return None
            total += fi.image_tokens
        else:
            return None
    return total


async def _create_and_save_context_cache(
    hash_key: str,
    checksums: list[str],
    file_refs: list[FileRef],
    *,
    cache_contents: list[str | FileRef],
    model: str,
    system_instruction: str | None,
    ttl: int,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> str | None:
    """context_cache 생성 + Redis 저장 → cached_content_name 반환 (분기 ④).

    `cache_contents` = 파일별 헤더 라벨이 섞인 캐시 콘텐츠(create 대상, doc 29).
    `file_refs` = Redis `files_api_uris` 저장·checksum 용(라벨 없는 순수 refs, 유지).

    standby 선점 후(정상 경로), 또는 standby 대기 timeout 후(Y' Hybrid)
    양쪽에서 호출된다.

    `tools` / `tool_config` 는 cache 에 함께 저장되어야 `call(cached_content=...)` 시
    Bad Request 를 피할 수 있다 (Gemini 제약 — cached_content + request-level
    tools/tool_config 동시 전달 불가).

    Context Cache 최소 토큰(1024) 미달이면 캐시를 skip 하고 None 을 반환한다
    (fail-soft, 이슈 #646) — 캐시는 비용 최적화일 뿐 정확도와 무관하므로. 호출부는
    None → cached_content=None 으로 받아 Files API URI 로 호출을 이어간다.
    too-small 외의 에러는 기존대로 raise.
    """
    try:
        cache_ref = await create_context_cache(
            cache_contents,
            model=model,
            system_instruction=system_instruction,
            ttl=ttl,
            tools=tools,
            tool_config=tool_config,
        )
    except (HandlerError, HandlerConnectionError) as e:
        # too-small(min_total_token_count) 은 fail-soft — 캐시 skip 하고 진행.
        if is_cache_too_small(e):
            logger.info(
                "[route] mode=context_cache source=skipped reason=below_min_token "
                "hash_key=%s", hash_key[:16],
            )
            return None
        # 그 외 분류 에러는 그대로 전파 (전역 핸들러가 relay).
        raise
    except Exception as e:
        if is_cache_too_small(e):  # wrap 안 된 raw 예외 방어
            logger.info(
                "[route] mode=context_cache source=skipped reason=below_min_token "
                "hash_key=%s", hash_key[:16],
            )
            return None
        logger.exception("Context cache 생성 실패")
        raise FileUploadError(f"Context cache 생성 실패: {e}") from e

    try:
        cache_store.save_context_cache(
            hash_key,
            {
                "file_checksums": checksums,
                "model": model,
                "system_instruction": system_instruction,
                "tools": tools,
                "tool_config": tool_config,
                "cached_content_name": cache_ref.name,
                "files_api_uris": [r.uri for r in file_refs],
            },
            ttl=ttl,
        )
    except Exception as e:
        logger.exception("Redis context_cache 저장 실패: hash_key=%s", hash_key[:16])
        raise CacheError(f"Redis context_cache 저장 실패: {e}") from e

    logger.info("[route] mode=context_cache source=live name=%s", cache_ref.name)
    return cache_ref.name


# ── Files API 분류 처리 — A/B/C miss 3단계 분기 (§6.2) ──────


async def _resolve_or_create_context_cache(
    hash_key: str,
    checksums: list[str],
    file_refs: list[FileRef],
    *,
    cache_contents: list[str | FileRef],
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int,
    standby_ttl_seconds: int,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> str | None:
    """context_cache 를 standby dedup 으로 보호하며 조회/생성 → cached_content_name (or None).

    `_process_files_api` 분기 ④의 dedup 블록을 추출한 것으로 동작 동일:
    선점→생성 / 대기→HIT / timeout→자기생성(Y' Hybrid). too-small fail-soft 면 None.
    호출 전제: ctx cache 활성 + file_refs 존재. file_preprocessing(context cache 모드)과 공유(SSOT).
    """
    if model is None:
        raise ValueError(
            "context cache 생성에는 model이 필요합니다 — "
            "llm_input에 'model' 키를 포함하세요"
        )
    acquired_cc = await dedup.try_acquire_standby(
        "context_cache", hash_key, standby_ttl_seconds,
    )
    if acquired_cc:
        try:
            return await _create_and_save_context_cache(
                hash_key, checksums, file_refs,
                cache_contents=cache_contents,
                model=model,
                system_instruction=system_instruction,
                ttl=context_cache_ttl,
                tools=tools,
                tool_config=tool_config,
            )
        finally:
            await dedup.release_standby("context_cache", hash_key)
    # 다른 요청이 생성 중 → 대기
    cached_cc = await dedup.wait_for_standby_release(
        "context_cache", hash_key, standby_ttl_seconds,
        find_cache_fn=lambda: cache_store.find_context_cache(hash_key),
    )
    if cached_cc:
        logger.info("[route] mode=context_cache source=cached name=%s", cached_cc["cached_content_name"])
        return cached_cc["cached_content_name"]
    # timeout → 자기가 생성 (Y' Hybrid)
    logger.warning(
        "context_cache standby timeout → 자기가 생성 (중복 가능): hash_key=%s",
        hash_key[:16],
    )
    return await _create_and_save_context_cache(
        hash_key, checksums, file_refs,
        cache_contents=cache_contents,
        model=model,
        system_instruction=system_instruction,
        ttl=context_cache_ttl,
        tools=tools,
        tool_config=tool_config,
    )


def _build_labeled_cache_contents(
    groups: list[tuple[FileInput, list[FileRef]]],
) -> list[str | FileRef]:
    """파일별 '===== [문서 N] 파일명 =====' 헤더 + FileRef 를 섞은 cache contents (doc 29).

    분할 파일(len(refs)>1)은 한 헤더 아래 파트들을 라벨 없이 연속 배치해 '한 문서가 용량
    때문에 쪼개진 것'임을 LLM 이 인지하되 연속성은 유지한다(docs/29 §3.1).

    주의(라벨을 출처 귀속에 쓰는 소비자용 — docs/29 §3.3/§3.6):
    라벨은 context cache 콘텐츠에만 붙는다(inline/캐시 skip 경로엔 없음).
    분할 파일 각 파트는 새 PDF 라 페이지가 1 부터 재시작한다(원문 오프셋 미적용).
    """
    contents: list[str | FileRef] = []
    for idx, (fi, refs) in enumerate(groups, start=1):
        n = len(refs)
        header = f"===== [문서 {idx}] {fi.file_name}"
        if n > 1:
            header += f" (총 {n}개 파트로 분할 업로드됨)"
        header += " ====="
        contents.append(header)
        contents.extend(refs)
    return contents


def _build_split_page_notice(
    groups: list[tuple[FileInput, list[FileRef]]],
) -> str | None:
    """분할 업로드된 파일이 있으면 '각 파트의 원본 페이지 범위 + 출처는 원본 절대 페이지로'
    안내 문구를 만든다(없으면 None).

    50MB/1000p 초과로 분할되면 각 파트가 새 PDF 라 페이지가 1 부터 재시작한다. 그대로 두면
    LLM 이 뒤 파트의 근거를 '파트 안 로컬 쪽번호'로 답해 원본 기준으로 밀린다(앞 파트 장수만큼).
    이 문구를 system_instruction 뒤에 붙여 context cache 에 구우면, 모델이 출처를 원본 절대
    페이지로 답하도록 유도한다. 분할된 파일이 하나도 없으면 None(문구 없음).
    """
    lines: list[str] = []
    for idx, (fi, refs) in enumerate(groups, start=1):
        rngs = fi.pdf_part_ranges
        if len(refs) > 1 and rngs and len(rngs) == len(refs):
            spans = ", ".join(
                f"part{i}=원본 {s}~{e}쪽" for i, (s, e) in enumerate(rngs, start=1)
            )
            lines.append(f"- [문서 {idx}] {fi.file_name}: {spans}")
    if not lines:
        return None
    body = "\n".join(lines)
    return (
        "\n\n[분할 업로드 안내]\n"
        "아래 문서는 용량 때문에 원본 PDF가 여러 파트로 분할되어 업로드되었다. 각 파트는 그 파트 "
        "안에서 다시 1쪽부터 시작하지만, 원본 PDF 기준 각 파트의 페이지 범위는 다음과 같다:\n"
        f"{body}\n"
        "출처의 페이지(쪽) 번호는 반드시 원본 PDF 파일 기준의 절대 페이지로 답하라 — 페이지에 "
        "인쇄된 쪽번호가 아니라, 원본 PDF 파일에서 물리적으로 몇 번째 장인지다."
    )


def _with_split_page_notice(
    system_instruction: str | None,
    groups: list[tuple[FileInput, list[FileRef]]],
) -> str | None:
    """system_instruction 뒤에 분할 안내 문구를 덧붙인다(분할 파일 없으면 원본 그대로).

    주의: 이 문구는 context cache 에 굽는 system 에만 붙이고 캐시 KEY(make_context_cache_key)
    에는 넣지 않는다 — KEY 는 원본 system 으로 계산돼 조회/생성이 일관된다. 문구는 파일의
    pdf_part_ranges 에서 결정론적으로 파생되고 파일 신원(checksum)은 이미 KEY 에 있으므로
    충돌하지 않는다.
    """
    notice = _build_split_page_notice(groups)
    if notice is None:
        return system_instruction
    return (system_instruction or "") + notice


async def _process_files_api(
    files_api_pool: list[FileInput],
    *,
    checksums: list[str],
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
    standby_ttl_seconds: int,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> tuple[list[Part], str | None, str | None]:
    """Files API 분류 파일 처리 — A/B/C miss 3단계 분기 + standby dedup.

    반환: (uri_parts, cached_content, hash_key).
    hash_key 는 context_cache 식별자 — files_api_pool 이 비면 None.

    A: Redis context_cache HIT → cached_content만 반환 (uri_parts=[])
    B: Redis files_api 모두 HIT → 기존 URI로 Part.from_uri 구성
    C: 일부/전부 MISS → standby 선점 → 업로드 → Redis 저장 (또는 대기 → HIT)
       (PDF + 50MB 초과면 splitter.split_pdf → upload_pdf_parts)

    context_cache_ttl 지정 시 분기 B/C 마지막에 standby로 보호된 create_context_cache.

    상세: docs/07.inline-vs-files-api-strategy.md §6.2
         docs/08.dedup-design-and-impl.md §6 (모듈 구조)
    """
    if not files_api_pool:
        return [], None, None

    # ── ① 분기 A: Redis context_cache 조회 ─────────────────
    hash_key = cache_store.make_context_cache_key(
        checksums,
        model=model,
        system_instruction=system_instruction,
        tools=tools,
        tool_config=tool_config,
    )
    try:
        cached = cache_store.find_context_cache(hash_key)
    except Exception as e:
        logger.exception("Redis context_cache 조회 실패: hash_key=%s", hash_key[:16])
        raise CacheError(f"Redis context_cache 조회 실패: {e}") from e

    if cached:
        logger.info("[route] mode=context_cache source=cached name=%s", cached["cached_content_name"])
        return [], cached["cached_content_name"], hash_key

    # ── ②③ 분기 B/C: 각 파일별 Redis files_api 조회 → MISS 시 standby → 업로드 ──
    uri_parts: list[Part] = []
    file_refs: list[FileRef] = []  # context_cache 생성 + Redis files_api_uris용
    groups: list[tuple[FileInput, list[FileRef]]] = []  # 파일별 그룹(라벨링, doc 29)

    for fi, checksum in zip(files_api_pool, checksums):
        parts, refs = await _resolve_one_files_api(
            fi, checksum, standby_ttl_seconds=standby_ttl_seconds,
        )
        uri_parts.extend(parts)
        file_refs.extend(refs)
        groups.append((fi, refs))

    # ── ④ context_cache_ttl 지정 시 standby로 보호된 create_cache + Redis 저장 ──
    cached_content: str | None = None
    # context_cache 활성 조건: None 도 0 도 아닌 양수만 (0 은 명시적 비활성)
    ctx_cache_active = context_cache_ttl is not None and context_cache_ttl > 0
    if ctx_cache_active and not file_refs:
        logger.info("[route] mode=context_cache source=none reason=no_files_api_files")
    if ctx_cache_active and file_refs:
        # ctx_cache_active(중간 변수)가 not-None 을 보장하나 pyright 는 추적 못 함 → 명시.
        # 불변식이 깨지면 None 이 조용히 흘러가는 대신 즉시 터진다 (FAIL-LOUD).
        assert context_cache_ttl is not None
        # too-small 선제 게이트 (§4.4.1) — 추정 합계가 최소치 미만이면 create 시도 자체를
        # skip (doomed 400 + 왕복 회피). 추정 불가(None)면 통과 → create 시도(fail-soft backstop).
        est_tokens = _estimate_cache_tokens(files_api_pool, system_instruction)
        if est_tokens is not None and est_tokens < _CACHE_MIN_TOKENS:
            logger.info(
                "[route] mode=context_cache source=skipped reason=below_min_token_estimate "
                "est=%d hash_key=%s", est_tokens, hash_key[:16],
            )
        else:
            cached_content = await _resolve_or_create_context_cache(
                hash_key, checksums, file_refs,
                cache_contents=_build_labeled_cache_contents(groups),
                model=model,
                # 분할된 파일이 있으면 안내 문구를 system 뒤에 붙여 캐시에 굽는다(키는 원본 system).
                system_instruction=_with_split_page_notice(system_instruction, groups),
                context_cache_ttl=context_cache_ttl,
                standby_ttl_seconds=standby_ttl_seconds,
                tools=tools,
                tool_config=tool_config,
            )

    return uri_parts, cached_content, hash_key


# ── 풀 분류 (classify + 승급 + 전개) — resolve_files / 404 복구 공유 ──


async def _resolve_pools(
    files: list[FileInput],
    llm_input: dict,
    *,
    context_cache_ttl: int | None,
) -> tuple[list[FileInput], list[FileInput]]:
    """→ (inline_pool, files_api_pool). ttl 켜지면 크기 분기 없이 전 파일 files_api(docs/30);
    ttl 없을 때만 classify(부분 섞기) + 인라인 >1000p PDF 전개·재분류.

    resolve_files와 resolve_files_after_404가 공유한다. 404 복구가 동일한
    files_api_pool(Step 2.5 전개로 생긴 part 포함)을 봐야 무효화 대상 checksum이
    일치한다 — 안 그러면 spilled part의 stale URI가 무효화되지 않아 "1회 복구"가
    실패한다 (docs/27 §404 복구, docs/28 §5.4).
    """
    # ttl 켜지면 캐시는 all-or-nothing — 크기 분기(classify) 없이 전 파일을 Files API 로 (docs/30).
    # inline 분기의 목적(작은 파일을 굳이 Files API 로 안 올림)은 '캐시 안 할 때' 얘기라,
    # 캐시를 켠 경우엔 나누지 않고 전부 캐시 대상으로 보낸다.
    if context_cache_ttl is not None:
        logger.warning(
            "context_cache_ttl=%d 지정 — 크기 분기 없이 전 파일 Files API 경로로 승급",
            context_cache_ttl,
        )
        return [], list(files)

    # ttl 없을 때만: 크기로 분류(부분 섞기) + 인라인 >1000p PDF 페이지 전개·재분류 (docs/28 §5.4)
    inline_pool, files_api_pool = classify_files(files, llm_input)
    if inline_pool:
        expanded = await _expand_pdfs_over_page_limit(inline_pool)
        inline_pool, spilled = classify_files(expanded, llm_input)
        files_api_pool = spilled + files_api_pool

    return inline_pool, files_api_pool


# ── Gemini 404 자동 복구 (§9) ─────────────────────────────────


async def resolve_files_after_404(
    files: list[FileInput],
    llm_input: dict,
    *,
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
    standby_ttl_seconds: int = 600,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> ResolvedFiles:
    """Gemini 404 발생 후 Redis 무효화 + resolve_files 재호출.

    영향받는 Files API 경로 파일들의 Redis 키(files_api:{checksum},
    context_cache:{hash_key})를 삭제한 뒤 resolve_files를 다시 호출하여
    분류 + 재업로드. 자동 복구는 1회만 — 호출자가 재호출 후 또 404면 raise.

    정상 운영에선 Redis TTL(47h) < Gemini TTL(48h)로 거의 발생 안 함. 발생 시
    Phase B 연쇄 삭제 누락·외부 삭제 등의 신호로 봄.

    상세: docs/07.inline-vs-files-api-strategy.md §9
    """
    # 영향받는 checksums 계산 — resolve_files와 동일 헬퍼로 전개(Step 2.5)까지 재현.
    # 인라인 >1000p PDF가 part로 spill돼 캐시된 경우, 그 part checksum도 무효화 대상이다.
    _, files_api_pool = await _resolve_pools(
        files, llm_input, context_cache_ttl=context_cache_ttl,
    )
    checksums = [make_file_checksum(fi) for fi in files_api_pool]

    logger.warning(
        "Gemini 404 자동 복구: Redis invalidate 후 재업로드 (checksums=%d)",
        len(checksums),
    )

    # Redis 무효화 — 삭제 실패는 재업로드 흐름에 무관하니 로그만
    for cs in checksums:
        try:
            cache_store.delete_files_api(cs)
        except Exception:
            logger.exception(
                "Redis files_api 삭제 실패 (계속 진행): checksum=%s", cs[:16]
            )

    hash_key = cache_store.make_context_cache_key(
        checksums, model=model, system_instruction=system_instruction,
        tools=tools, tool_config=tool_config,
    )
    try:
        cache_store.delete_context_cache(hash_key)
    except Exception:
        logger.exception(
            "Redis context_cache 삭제 실패 (계속 진행): hash_key=%s", hash_key[:16]
        )

    # resolve_files 재호출 → 분류 + 재업로드 (이번 실패는 상위로 raise)
    return await resolve_files(
        files, llm_input,
        model=model,
        system_instruction=system_instruction,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
        tools=tools,
        tool_config=tool_config,
    )


# ── 진입점 (§5 파이프라인) ────────────────────────────────────


@dataclass
class ResolvedFiles:
    """resolve_files 반환 — 호출자(runner)가 contents 합쳐 LLM 호출.

    - inline_parts + uri_parts → call_input['contents'] 앞쪽에 삽입
    - cached_content → 있으면 call_input['cached_content']에 별도 세팅
    - context_cache_id → Context Cache 생성/HIT 시 hash_key (응답 노출용)
    """
    inline_parts: list[Part]
    uri_parts: list[Part]
    cached_content: str | None
    context_cache_id: str | None = None


async def resolve_files(
    files: list[FileInput],
    llm_input: dict,
    *,
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
    standby_ttl_seconds: int = 600,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> ResolvedFiles:
    """파일 처리 전체 흐름.

    Step 1: classify_files (부분 섞기 분류) → inline_pool / files_api_pool
    Step 2: context_cache_ttl 지정 + 전원 inline이면 전 파일 Files API로 승급 (§4.4)
    Step 3: 두 풀 처리 (서로 독립이라 병렬 실행)
    Step 4: ResolvedFiles 반환

    Args:
        standby_ttl_seconds: cache stampede dedup standby TTL + 대기 timeout (기본 600).

    상세: docs/07.inline-vs-files-api-strategy.md §4, §5, §6
    """
    # Step 1~2.5: 분류 + 승급 + 인라인 PDF 페이지 전개 (공유 헬퍼 — 404 복구와 동일 로직)
    inline_pool, files_api_pool = await _resolve_pools(
        files, llm_input, context_cache_ttl=context_cache_ttl,
    )

    # Step 3: 두 풀 병렬 처리
    checksums = [make_file_checksum(fi) for fi in files_api_pool]

    inline_parts, fa_result = await asyncio.gather(
        _process_inline(inline_pool),
        _process_files_api(
            files_api_pool,
            checksums=checksums,
            model=model,
            system_instruction=system_instruction,
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
            tools=tools,
            tool_config=tool_config,
        ),
    )
    uri_parts, cached_content, context_cache_id = fa_result

    return ResolvedFiles(
        inline_parts=inline_parts,
        uri_parts=uri_parts,
        cached_content=cached_content,
        # context_cache_id 는 실제 캐시가 있을 때(cached_content not None)만 노출.
        # too-small fail-soft / ttl 미지정이면 캐시가 없으니 None — 삭제할 대상도 없다.
        context_cache_id=context_cache_id if cached_content else None,
    )
