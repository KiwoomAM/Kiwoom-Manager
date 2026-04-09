"""파일 처리 진입점.

파일 크기에 따라 inline/Files API/split 분기하고,
Redis 캐시를 조회/저장하며, context cache opt-in을 처리한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from llm_handler.models import FileRef, Part

from app.logic.types import FileChecksum
from app.logic.file_handler import cache_store
from app.logic.file_handler.uploader import create_context_cache, upload_pdf_parts
from llm_handler.aio import upload_file

logger = logging.getLogger(__name__)

# 크기 임계값
_INLINE_LIMIT = 20 * 1024 * 1024  # 20MB
_PDF_SPLIT_LIMIT = 50 * 1024 * 1024  # 50MB


@dataclass
class ResolvedFiles:
    """resolve_files 결과."""

    contents_parts: list[Part]  # inline 또는 file_uri Part들
    cached_content: str | None  # context cache name (있는 경우)


async def resolve_files(
    file_metas: list[FileChecksum],
    file_paths: list[str],
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    context_cache_ttl: int | None = None,
) -> ResolvedFiles:
    """파일 크기에 따라 최적 방식을 결정하고 처리한다.

    1. Redis에서 기존 context cache 조회 (항상)
    2. hit → cached_content 재사용
    3. miss → 각 파일별 처리 → context_cache_ttl 있으면 create_cache + Redis 저장

    Args:
        file_metas: checksum.file_checksum()으로 생성된 메타 리스트.
        file_paths: 로컬 파일 경로 리스트 (file_metas와 1:1 대응).
        model: Gemini 모델명 (context cache hash_key용).
        system_instruction: 시스템 프롬프트 (context cache hash_key용).
        context_cache_ttl: context cache TTL. None이면 context caching 안 함.

    Returns:
        ResolvedFiles(contents_parts, cached_content).
    """
    checksums = [m["checksum"] for m in file_metas]

    # ── ① Redis에서 기존 context cache 조회 (항상) ─────────────
    hash_key = cache_store.make_context_cache_key(
        checksums,
        model=model,
        system_instruction=system_instruction,
    )
    cached = cache_store.find_context_cache(hash_key)
    if cached:
        logger.info("context cache hit: %s", cached["cached_content_name"])
        return ResolvedFiles(
            contents_parts=[],
            cached_content=cached["cached_content_name"],
        )

    # ── ② 각 파일별 독립 처리 ─────────────────────────────────
    contents_parts: list[Part] = []
    file_refs: list[FileRef] = []  # create_cache용 (context_cache_ttl 있을 때)

    for meta, path in zip(file_metas, file_paths):
        size = meta["size"]
        mime = meta["mime_type"]
        checksum = meta["checksum"]

        if size <= _INLINE_LIMIT:
            # ≤20MB → inline (base64)
            contents_parts.append(Part.from_file(path))

        elif mime == "application/pdf" and size > _PDF_SPLIT_LIMIT:
            # PDF >50MB → Redis 조회 → miss면 split + Files API 업로드
            parts, refs = await _resolve_split_pdf(meta, path)
            contents_parts.extend(parts)
            file_refs.extend(refs)

        else:
            # >20MB → Redis 조회 → miss면 Files API 업로드
            part, ref = await _resolve_files_api(meta, path)
            contents_parts.append(part)
            file_refs.append(ref)

    # ── ③ context_cache_ttl이 있으면 context cache 생성 ──────────────
    if context_cache_ttl and file_refs:
        try:
            cache_ref = await create_context_cache(
                file_refs,
                model=model,
                system_instruction=system_instruction,
                ttl=context_cache_ttl,
            )
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
            logger.info("context cache 생성: %s", cache_ref.name)
            return ResolvedFiles(contents_parts=[], cached_content=cache_ref.name)
        except Exception:
            logger.warning(
                "context cache 생성 실패 → files/inline으로 진행", exc_info=True
            )

    return ResolvedFiles(contents_parts=contents_parts, cached_content=None)


# ── 내부 헬퍼 ─────────────────────────────────────────────────


async def _resolve_split_pdf(
    meta: FileChecksum, path: str
) -> tuple[list[Part], list[FileRef]]:
    """PDF >50MB: Redis 조회 → miss면 split + 업로드."""
    checksum = meta["checksum"]

    # Redis에서 기존 split URI 리스트 조회
    existing = cache_store.find_files_api(checksum)
    if existing and existing.get("split"):
        uris = existing["uris"]
        mime = existing["mime_type"]
        logger.info("split pdf cache hit: %d파트", len(uris))
        parts = [Part.from_uri(uri, mime_type=mime) for uri in uris]
        refs = [
            FileRef(name="", uri=uri, mime_type=mime, state="ACTIVE") for uri in uris
        ]
        return parts, refs

    # miss → split + 업로드
    refs = await upload_pdf_parts(path)
    uris = [ref.uri for ref in refs]

    cache_store.save_files_api(
        checksum,
        {
            "file_checksum": checksum,
            "file_name": meta["filename"],
            "file_size": meta["size"],
            "mime_type": meta["mime_type"],
            "uris": uris,
            "split": True,
        },
    )

    parts = [Part.from_uri(ref.uri, mime_type=ref.mime_type) for ref in refs]
    return parts, refs


async def _resolve_files_api(
    meta: FileChecksum, path: str
) -> tuple[Part, FileRef]:
    """>20MB 파일: Redis 조회 → miss면 Files API 업로드."""
    checksum = meta["checksum"]
    mime = meta["mime_type"]

    existing = cache_store.find_files_api(checksum)
    if existing:
        uri = existing["uris"][0]
        logger.info("files_api cache hit: %s", uri)
        ref = FileRef(name="", uri=uri, mime_type=mime, state="ACTIVE")
    else:
        ref = await upload_file(path, mime)
        cache_store.save_files_api(
            checksum,
            {
                "file_checksum": checksum,
                "file_name": meta["filename"],
                "file_size": meta["size"],
                "mime_type": mime,
                "uris": [ref.uri],
                "split": False,
            },
        )

    part = Part.from_uri(ref.uri, mime_type=ref.mime_type)
    return part, ref
