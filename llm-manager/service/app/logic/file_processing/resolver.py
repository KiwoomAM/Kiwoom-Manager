"""파일 처리 진입점.

files + llm_input 받아서 (inline_parts, uri_parts, cached_content)를 반환하는
부분 섞기 흐름. 각 원소는 FileInput (meta + bytes)로, 에이전트가 준 bytes를
그대로 LLM 호출 payload에 사용한다.

cache stampede dedup (docs/cache-stampede-dedup.md):
- files_api 업로드, context_cache 생성 시점에 Redis standby 선점
- 다른 요청이 이미 작업 중이면 exponential backoff로 대기

상세 설계: docs/inline-vs-files-api-strategy.md §4, §5, §6, §7
             docs/cache-stampede-dedup.md §6
             docs/file-manager-bytes-passthrough.md
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
from llm_handler.aio import upload_file
from llm_handler.models import FileRef, Part

from app.core.errors import CacheError, FileUploadError
from app.logic.file_processing import cache_store, dedup
from app.logic.file_processing.uploader import create_context_cache, upload_pdf_parts
from app.logic.file_resolver import fetch_with_fallback
from app.logic.keying import make_file_checksum
from app.models.requests import FileInput

logger = logging.getLogger(__name__)


# ── 합계 한도 상수 (§4.1 Step 2 — 모달리티 인지) ──────────────

_INLINE_LIMIT_WITH_IMAGE_AUDIO = 20 * 1024 * 1024  # 20 MB
_INLINE_LIMIT_PDF_OR_TEXT_ONLY = 50 * 1024 * 1024  # 50 MB

# Files API 경로 — Gemini PDF 공식 한도, 초과 시 splitter.split_pdf로 분할
_PDF_SPLIT_LIMIT = 50 * 1024 * 1024  # 50 MB


def _inline_total_limit(pool: list[FileInput]) -> int:
    """inline 후보 합계 한도 — image/audio 포함 여부에 따라 결정.

    image 또는 audio가 하나라도 inline 후보에 있으면 엄격한 20MB,
    PDF/텍스트만 있으면 완화된 50MB.
    """
    has_image_or_audio = any(
        fi.mime_type.startswith(("image/", "audio/")) for fi in pool
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

    Step 1: 비디오만 선제 분리 (Gemini inline 자체 불가).
            나머지 개별 한도(PDF 50MB, image/audio/text 20MB)는 Step 2의
            합계 체크에 녹아들어 별도 체크 안 함. PDF 50MB 초과는
            이후 Files API 업로드 단계에서 splitter가 처리.

    Step 2: 합계 체크 — 파일 size + _estimate_non_file_bytes(llm_input).
            모달리티 인지 한도 — image/audio 포함 시 20MB / PDF·텍스트만
            50MB.

    Step 3: 작은 것부터 정렬 + 한 번 순회 (재평가 없음 — Q2 보수).
            큰 파일을 Files API로 보내 Redis HIT 이점 극대화.
    """
    inline_pool: list[FileInput] = []
    files_api_pool: list[FileInput] = []

    # Step 1: 비디오만 강제 분리
    for fi in files:
        if fi.mime_type.startswith("video/"):
            files_api_pool.append(fi)
        else:
            inline_pool.append(fi)

    # Step 2: 합계 한도 결정 (모달리티 인지)
    limit = _inline_total_limit(inline_pool)

    # Step 3: 작은 것부터 정렬 + 한 번 순회 (재평가 없음)
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
    상세: docs/inline-vs-files-api-strategy.md §6.1
    """
    fm = FileManagerHandler()
    inline_parts: list[Part] = []
    for fi in inline_pool:
        if not fi.data:
            fi.data = await fetch_with_fallback(fm, fi)
        inline_parts.append(
            Part.from_bytes(data=fi.data, mime_type=fi.mime_type)
        )
        logger.info("[route] file=%s mode=inline", fi.file_name)
    return inline_parts


# ── files_api 헬퍼 ───────────────────────────────────────────


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
            if fi.mime_type == "application/pdf" and fi.file_size > _PDF_SPLIT_LIMIT:
                refs = await upload_pdf_parts(tmp.name)
            else:
                refs = [await upload_file(tmp.name, mime_type=fi.mime_type)]
        except Exception as e:
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


# ── context_cache 헬퍼 ───────────────────────────────────────


async def _create_and_save_context_cache(
    hash_key: str,
    checksums: list[str],
    file_refs: list[FileRef],
    *,
    model: str,
    system_instruction: str | None,
    ttl: int,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> str:
    """context_cache 생성 + Redis 저장 → cached_content_name 반환 (분기 ④).

    standby 선점 후(정상 경로), 또는 standby 대기 timeout 후(Y' Hybrid)
    양쪽에서 호출된다.

    `tools` / `tool_config` 는 cache 에 함께 저장되어야 `call(cached_content=...)` 시
    Bad Request 를 피할 수 있다 (Gemini 제약 — cached_content + request-level
    tools/tool_config 동시 전달 불가).
    """
    try:
        cache_ref = await create_context_cache(
            file_refs,
            model=model,
            system_instruction=system_instruction,
            ttl=ttl,
            tools=tools,
            tool_config=tool_config,
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
) -> tuple[list[Part], str | None]:
    """Files API 분류 파일 처리 — A/B/C miss 3단계 분기 + standby dedup.

    A: Redis context_cache HIT → cached_content만 반환 (uri_parts=[])
    B: Redis files_api 모두 HIT → 기존 URI로 Part.from_uri 구성
    C: 일부/전부 MISS → standby 선점 → 업로드 → Redis 저장 (또는 대기 → HIT)
       (PDF + 50MB 초과면 splitter.split_pdf → upload_pdf_parts)

    context_cache_ttl 지정 시 분기 B/C 마지막에 standby로 보호된 create_context_cache.

    상세: docs/inline-vs-files-api-strategy.md §6.2
         docs/cache-stampede-dedup.md §6
    """
    if not files_api_pool:
        return [], None

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
        return [], cached["cached_content_name"]

    # ── ②③ 분기 B/C: 각 파일별 Redis files_api 조회 → MISS 시 standby → 업로드 ──
    uri_parts: list[Part] = []
    file_refs: list[FileRef] = []  # context_cache 생성용

    for fi, checksum in zip(files_api_pool, checksums):
        try:
            existing = cache_store.find_files_api(checksum)
        except Exception as e:
            logger.exception("Redis files_api 조회 실패: checksum=%s", checksum[:16])
            raise CacheError(f"Redis files_api 조회 실패: {e}") from e

        if existing:
            _reuse_files_api_uris(fi, existing, uri_parts, file_refs)
            continue

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

    # ── ④ context_cache_ttl 지정 시 standby로 보호된 create_cache + Redis 저장 ──
    cached_content: str | None = None
    # context_cache 활성 조건: None 도 0 도 아닌 양수만 (0 은 명시적 비활성)
    ctx_cache_active = context_cache_ttl is not None and context_cache_ttl > 0
    if ctx_cache_active and not file_refs:
        logger.info("[route] mode=context_cache source=none reason=no_files_api_files")
    if ctx_cache_active and file_refs:
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
                cached_content = await _create_and_save_context_cache(
                    hash_key, checksums, file_refs,
                    model=model,
                    system_instruction=system_instruction,
                    ttl=context_cache_ttl,
                    tools=tools,
                    tool_config=tool_config,
                )
            finally:
                await dedup.release_standby("context_cache", hash_key)
        else:
            # 다른 요청이 생성 중 → 대기
            cached_cc = await dedup.wait_for_standby_release(
                "context_cache", hash_key, standby_ttl_seconds,
                find_cache_fn=lambda: cache_store.find_context_cache(hash_key),
            )
            if cached_cc:
                cached_content = cached_cc["cached_content_name"]
                logger.info("[route] mode=context_cache source=cached name=%s", cached_content)
            else:
                # timeout → 자기가 생성 (Y' Hybrid)
                logger.warning(
                    "context_cache standby timeout → 자기가 생성 (중복 가능): hash_key=%s",
                    hash_key[:16],
                )
                cached_content = await _create_and_save_context_cache(
                    hash_key, checksums, file_refs,
                    model=model,
                    system_instruction=system_instruction,
                    ttl=context_cache_ttl,
                    tools=tools,
                    tool_config=tool_config,
                )

    return uri_parts, cached_content


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

    상세: docs/inline-vs-files-api-strategy.md §9
    """
    # 영향받는 checksums 계산 — resolve_files의 분류·승급 순서 재현
    inline_pool, files_api_pool = classify_files(files, llm_input)
    if context_cache_ttl is not None and not files_api_pool and inline_pool:
        files_api_pool = inline_pool
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
    """
    inline_parts: list[Part]
    uri_parts: list[Part]
    cached_content: str | None


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

    상세: docs/inline-vs-files-api-strategy.md §4, §5, §6
    """
    # Step 1: 분류
    inline_pool, files_api_pool = classify_files(files, llm_input)

    # Step 2: context_cache_ttl 승급 정책
    if context_cache_ttl is not None and not files_api_pool and inline_pool:
        logger.warning(
            "context_cache_ttl=%d 지정 — 모든 파일 Files API 경로로 승급",
            context_cache_ttl,
        )
        files_api_pool = inline_pool
        inline_pool = []

    # Step 3: 두 풀 병렬 처리
    checksums = [make_file_checksum(fi) for fi in files_api_pool]

    inline_parts, (uri_parts, cached_content) = await asyncio.gather(
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

    return ResolvedFiles(
        inline_parts=inline_parts,
        uri_parts=uri_parts,
        cached_content=cached_content,
    )
