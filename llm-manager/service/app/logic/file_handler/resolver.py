"""파일 처리 진입점.

file_manager 분리 후 새 설계 — file_metas + llm_input 받아서
(inline_parts, uri_parts, cached_content)를 반환하는 부분 섞기 흐름.

세부 함수는 Phase A T2~T6에서 차례로 추가된다 (T1에선 상수 + 헬퍼만).

상세 설계: docs/inline-vs-files-api-strategy.md §4, §5, §6, §7
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass

from file_manager import FileMeta  # TODO: SDK 배포 전까지 없음. ② Phase A 검증 시 stub/실물 준비
from llm_handler.aio import upload_file
from llm_handler.models import FileRef, Part

from app.core.errors import CacheError, FileUploadError
from app.core.file_manager_client import fetch_file_bytes
from app.logic.file_handler import cache_store
from app.logic.file_handler.uploader import create_context_cache, upload_pdf_parts
from app.logic.keying import make_file_checksum

logger = logging.getLogger(__name__)


# ── 합계 한도 상수 (§4.1 Step 2 — 모달리티 인지) ──────────────

_INLINE_LIMIT_WITH_IMAGE_AUDIO = 20 * 1024 * 1024  # 20 MB
_INLINE_LIMIT_PDF_OR_TEXT_ONLY = 50 * 1024 * 1024  # 50 MB

# Files API 경로 — Gemini PDF 공식 한도, 초과 시 splitter.split_pdf로 분할
_PDF_SPLIT_LIMIT = 50 * 1024 * 1024  # 50 MB


def _inline_total_limit(pool: list[FileMeta]) -> int:
    """inline 후보 합계 한도 — image/audio 포함 여부에 따라 결정.

    image 또는 audio가 하나라도 inline 후보에 있으면 엄격한 20MB,
    PDF/텍스트만 있으면 완화된 50MB.
    """
    has_image_or_audio = any(
        m.mime_type.startswith(("image/", "audio/")) for m in pool
    )
    return _INLINE_LIMIT_WITH_IMAGE_AUDIO if has_image_or_audio else _INLINE_LIMIT_PDF_OR_TEXT_ONLY


# ── 비파일 부분 byte 추정 (§4.1 Step 2) ──────────────────────


def _estimate_non_file_bytes(llm_input: dict) -> int:
    """파일 Part를 제외한 llm_input의 JSON 직렬화 byte 수 근사.

    classify_files 호출 시점에는 llm_input에 파일 Part가 들어있지 않으므로
    단순 직렬화 크기로 충분.
    """
    try:
        return len(json.dumps(llm_input, default=str).encode("utf-8"))
    except Exception:
        return 0  # 추정 실패 시 0 → inline 판정에 관대한 fallback


# ── 부분 섞기 분류 (§4.1) ────────────────────────────────────


def classify_files(
    file_metas: list[FileMeta],
    llm_input: dict,
) -> tuple[list[FileMeta], list[FileMeta]]:
    """파일 메타를 inline_pool / files_api_pool로 분류 (부분 섞기).

    Step 1: 비디오만 선제 분리 (Gemini inline 자체 불가).
            나머지 개별 한도(PDF 50MB, image/audio/text 20MB)는 Step 2의
            합계 체크에 녹아들어 별도 체크 안 함. PDF 50MB 초과는
            이후 Files API 업로드 단계에서 splitter가 처리.

    Step 2: 합계 체크 — 파일 size + _estimate_non_file_bytes(llm_input).
            모달리티 인지 한도 — image/audio 포함 시 20MB / PDF·텍스트만
            50MB.

    Step 3: 작은 것부터 정렬 + 한 번 순회 (재평가 없음 — Q2 보수).
            큰 파일을 Files API로 보내 Redis HIT 이점 극대화.

    Returns:
        (inline_pool, files_api_pool) 튜플.
    """
    inline_pool: list[FileMeta] = []
    files_api_pool: list[FileMeta] = []

    # Step 1: 비디오만 강제 분리
    for m in file_metas:
        if m.mime_type.startswith("video/"):
            files_api_pool.append(m)
        else:
            inline_pool.append(m)

    # Step 2: 합계 한도 결정 (모달리티 인지)
    limit = _inline_total_limit(inline_pool)

    # Step 3: 작은 것부터 정렬 + 한 번 순회 (재평가 없음)
    inline_pool.sort(key=lambda m: m.file_size)
    total = _estimate_non_file_bytes(llm_input)
    kept: list[FileMeta] = []
    for m in inline_pool:
        if total + m.file_size <= limit:
            kept.append(m)
            total += m.file_size
        else:
            files_api_pool.append(m)

    return kept, files_api_pool


# ── inline 분류 처리 (§6.1) ──────────────────────────────────


async def _process_inline(inline_pool: list[FileMeta]) -> list[Part]:
    """inline 분류 파일을 file_manager에서 bytes로 받아 Part.from_bytes 구성.

    Redis 전혀 접근 안 함 (inline은 Files API URI/context_cache 와 무관).
    매 호출마다 file_manager에서 bytes 수급 — 제한 내 작은 파일이라 부담 낮음.
    tempfile 불필요 — Part.from_bytes는 메모리 bytes 직접 수용.

    상세: docs/inline-vs-files-api-strategy.md §6.1
    """
    inline_parts: list[Part] = []
    for m in inline_pool:
        bytes_data = await fetch_file_bytes(m.file_path)
        inline_parts.append(
            Part.from_bytes(data=bytes_data, mime_type=m.mime_type)
        )
    return inline_parts


# ── Files API 분류 처리 — A/B/C miss 3단계 분기 (§6.2) ──────


async def _process_files_api(
    files_api_pool: list[FileMeta],
    *,
    checksums: list[str],
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
) -> tuple[list[Part], str | None]:
    """Files API 분류 파일 처리 — A/B/C miss 3단계 분기.

    A: Redis context_cache HIT → cached_content만 반환 (uri_parts=[])
    B: Redis files_api 모두 HIT → 기존 URI로 Part.from_uri 구성
    C: 일부/전부 MISS → file_manager에서 bytes 받아 Files API 업로드 + Redis 저장
       (PDF + 50MB 초과면 splitter.split_pdf → upload_pdf_parts)

    context_cache_ttl 지정 시 분기 B/C 마지막에 create_context_cache + Redis 저장.

    Args:
        files_api_pool: Files API로 분류된 FileMeta 리스트.
        checksums: 각 FileMeta의 checksum (호출자가 make_file_checksum으로 계산하여 1:1 전달).
        model: Gemini 모델명 (context cache hash_key용).
        system_instruction: 시스템 프롬프트 (context cache hash_key용).
        context_cache_ttl: context cache TTL(초). None이면 context caching 안 함.

    Returns:
        (uri_parts, cached_content) 튜플. files_api_pool이 비면 ([], None).

    상세: docs/inline-vs-files-api-strategy.md §6.2
    """
    if not files_api_pool:
        return [], None

    # ── ① 분기 A: Redis context_cache 조회 ─────────────────
    hash_key = cache_store.make_context_cache_key(
        checksums,
        model=model,
        system_instruction=system_instruction,
    )
    try:
        cached = cache_store.find_context_cache(hash_key)
    except Exception as e:
        logger.exception("Redis context_cache 조회 실패: hash_key=%s", hash_key[:16])
        raise CacheError(f"Redis context_cache 조회 실패: {e}") from e

    if cached:
        logger.info("context cache hit: %s", cached["cached_content_name"])
        return [], cached["cached_content_name"]

    # ── ②③ 분기 B/C: 각 파일별 Redis files_api 조회 → MISS 시 업로드 ──
    uri_parts: list[Part] = []
    file_refs: list[FileRef] = []  # context_cache 생성용

    for meta, checksum in zip(files_api_pool, checksums):
        try:
            existing = cache_store.find_files_api(checksum)
        except Exception as e:
            logger.exception("Redis files_api 조회 실패: checksum=%s", checksum[:16])
            raise CacheError(f"Redis files_api 조회 실패: {e}") from e

        if existing:
            # 분기 B 부분: 기존 URI 재사용
            for uri in existing["uris"]:
                uri_parts.append(Part.from_uri(uri, mime_type=meta.mime_type))
                file_refs.append(
                    FileRef(name="", uri=uri, mime_type=meta.mime_type, state="ACTIVE")
                )
            logger.info("files_api cache hit: checksum=%s, uris=%d", checksum[:16], len(existing["uris"]))
        else:
            # 분기 C: file_manager에서 bytes 받아 업로드
            bytes_data = await fetch_file_bytes(meta.file_path)
            suffix = f".{meta.extension}" if meta.extension else ""
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                tmp.write(bytes_data)
                tmp.close()
                try:
                    if meta.mime_type == "application/pdf" and meta.file_size > _PDF_SPLIT_LIMIT:
                        refs = await upload_pdf_parts(tmp.name)
                    else:
                        refs = [await upload_file(tmp.name, meta.mime_type)]
                except Exception as e:
                    logger.exception(
                        "Files API 업로드 실패: file_name=%s, mime=%s",
                        meta.file_name, meta.mime_type,
                    )
                    raise FileUploadError(
                        f"Files API 업로드 실패: file_name={meta.file_name}, {e}"
                    ) from e
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

            uris = [r.uri for r in refs]
            try:
                cache_store.save_files_api(checksum, {
                    "file_checksum": checksum,
                    "file_name": meta.file_name,
                    "file_size": meta.file_size,
                    "mime_type": meta.mime_type,
                    "uris": uris,
                    "split": len(refs) > 1,
                })
            except Exception as e:
                logger.exception("Redis files_api 저장 실패: checksum=%s", checksum[:16])
                raise CacheError(f"Redis files_api 저장 실패: {e}") from e

            for r in refs:
                uri_parts.append(Part.from_uri(r.uri, mime_type=r.mime_type))
                file_refs.append(r)
            logger.info(
                "files_api 신규 업로드: checksum=%s, uris=%d, split=%s",
                checksum[:16], len(refs), len(refs) > 1,
            )

    # ── ④ context_cache_ttl 지정 시 create_cache + Redis 저장 (실패 시 raise) ──
    cached_content: str | None = None
    if context_cache_ttl and file_refs:
        try:
            cache_ref = await create_context_cache(
                file_refs,
                model=model,
                system_instruction=system_instruction,
                ttl=context_cache_ttl,
            )
        except Exception as e:
            logger.exception("Context cache 생성 실패")
            raise FileUploadError(f"Context cache 생성 실패: {e}") from e

        try:
            cache_store.save_context_cache(
                hash_key,
                {
                    "file_checksums": checksums,
                    "model": model,
                    "system_instruction": system_instruction,
                    "cached_content_name": cache_ref.name,
                    "files_api_uris": [r.uri for r in file_refs],
                },
                ttl=context_cache_ttl,
            )
        except Exception as e:
            logger.exception("Redis context_cache 저장 실패: hash_key=%s", hash_key[:16])
            raise CacheError(f"Redis context_cache 저장 실패: {e}") from e

        cached_content = cache_ref.name
        logger.info("context cache 생성: %s", cache_ref.name)

    return uri_parts, cached_content


# ── Gemini 404 자동 복구 (§9) ─────────────────────────────────


async def resolve_files_after_404(
    file_metas: list[FileMeta],
    llm_input: dict,
    *,
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
) -> ResolvedFiles:
    """Gemini 404 발생 후 Redis 무효화 + resolve_files 재호출.

    영향받는 Files API 경로 파일들의 Redis 키(files_api:{checksum},
    context_cache:{hash_key})를 삭제한 뒤 resolve_files를 다시 호출하여
    분류 + 재업로드. 자동 복구는 1회만 — 호출자가 재호출 후 또 404면 raise.

    정상 운영에선 Redis TTL(47h) < Gemini TTL(48h)로 거의 발생 안 함. 발생 시
    Phase B 연쇄 삭제 누락·외부 삭제 등의 신호로 봄.

    상세: docs/inline-vs-files-api-strategy.md §9
    """
    # 영향받는 checksums 계산 — resolve_files의 분류·승급 순서 재현
    inline_pool, files_api_pool = classify_files(file_metas, llm_input)
    if context_cache_ttl is not None and not files_api_pool and inline_pool:
        files_api_pool = inline_pool
    checksums = [make_file_checksum(m) for m in files_api_pool]

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
    )
    try:
        cache_store.delete_context_cache(hash_key)
    except Exception:
        logger.exception(
            "Redis context_cache 삭제 실패 (계속 진행): hash_key=%s", hash_key[:16]
        )

    # resolve_files 재호출 → 분류 + 재업로드 (이번 실패는 상위로 raise)
    return await resolve_files(
        file_metas, llm_input,
        model=model,
        system_instruction=system_instruction,
        context_cache_ttl=context_cache_ttl,
    )


# ── 진입점 (§5 파이프라인) ────────────────────────────────────


@dataclass
class ResolvedFiles:
    """resolve_files 반환 — 호출자(runner)가 contents 합쳐 LLM 호출.

    - inline_parts + uri_parts → call_input['contents'] 앞쪽에 삽입
    - cached_content → 있으면 call_input['cached_content']에 별도 세팅
    """
    inline_parts: list[Part]
    uri_parts: list[Part]
    cached_content: str | None


async def resolve_files(
    file_metas: list[FileMeta],
    llm_input: dict,
    *,
    model: str | None,
    system_instruction: str | None,
    context_cache_ttl: int | None,
) -> ResolvedFiles:
    """파일 처리 전체 흐름.

    Step 1: classify_files (부분 섞기 분류) → inline_pool / files_api_pool
    Step 2: context_cache_ttl 지정 + 전원 inline이면 전 파일 Files API로 승급 (§4.4)
    Step 3: 두 풀 처리 (서로 독립이라 병렬 실행)
    Step 4: ResolvedFiles 반환

    상세: docs/inline-vs-files-api-strategy.md §4, §5, §6
    """
    # Step 1: 분류
    inline_pool, files_api_pool = classify_files(file_metas, llm_input)

    # Step 2: context_cache_ttl 승급 정책
    if context_cache_ttl is not None and not files_api_pool and inline_pool:
        logger.warning(
            "context_cache_ttl=%d 지정 — 모든 파일 Files API 경로로 승급",
            context_cache_ttl,
        )
        files_api_pool = inline_pool
        inline_pool = []

    # Step 3: 두 풀 병렬 처리
    checksums = [make_file_checksum(m) for m in files_api_pool]

    inline_parts, (uri_parts, cached_content) = await asyncio.gather(
        _process_inline(inline_pool),
        _process_files_api(
            files_api_pool,
            checksums=checksums,
            model=model,
            system_instruction=system_instruction,
            context_cache_ttl=context_cache_ttl,
        ),
    )

    return ResolvedFiles(
        inline_parts=inline_parts,
        uri_parts=uri_parts,
        cached_content=cached_content,
    )
