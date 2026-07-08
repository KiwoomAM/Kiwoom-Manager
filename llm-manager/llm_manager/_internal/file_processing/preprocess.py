"""file_preprocessing — 파일 전처리(업로드/캐시 생성)만 떼어낸 진입점. docs/26 §4.2.

llm_request 의 LLM 캐시 조회·LLM 호출 조립 없이, stored_files 를 받아:
- 전 파일을 files_api 로 강제 업로드(classify 미사용) → 파일별 URI
- (opt-in) 전체 파일에 대해 Context Cache 1개 생성

files_api / context_cache 의 실제 처리는 resolver 의 공유 헬퍼를 재사용한다(SSOT):
  _resolve_one_files_api        — 파일 1개 files_api 처리 (HIT 재사용 / 업로드 + dedup)
  _resolve_or_create_context_cache — context cache 조회/생성 (dedup, too-small fail-soft)
"""

from __future__ import annotations

import logging

from llm_manager._internal.file_processing import cache_store
from llm_manager._internal.file_processing.resolver import (
    _build_labeled_cache_contents,
    _resolve_one_files_api,
    _resolve_or_create_context_cache,
)
from llm_manager._internal.handler_client.models import FileRef
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.stored_file_resolver import resolve_stored_files
from llm_manager.models import FileInput, StoredFile

logger = logging.getLogger(__name__)


async def file_preprocessing_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    context_cache_ttl: int | None = None,
    standby_ttl_seconds: int = 600,
) -> dict[str, str] | list[list[str]]:
    """파일 전처리 (core). docs/26 §3.1.

    반환:
      - context cache 모드(`context_cache_ttl` 양수 + 생성 성공)
        → ``{"context_cache_id": <hash_key>, "cached_content_name": <gemini cache id>}``
      - files_api-only 모드 / too-small fail-soft
        → ``(n, ?)`` 파일별 URI 리스트 (``result[i]`` = i 번째 stored_file 의 URI 들)
    """
    files = await resolve_stored_files(stored_files or [])
    if not files:
        return []

    checksums = [make_file_checksum(fi) for fi in files]

    # 전 파일 강제 files_api (classify 미사용) — 파일별 URI 그룹 수집
    per_file_uris: list[list[str]] = []
    file_refs_all: list[FileRef] = []
    groups: list[tuple[FileInput, list[FileRef]]] = []  # 파일별 그룹(라벨링, doc 29)
    for fi, cs in zip(files, checksums):
        _uri_parts, refs = await _resolve_one_files_api(
            fi, cs, standby_ttl_seconds=standby_ttl_seconds,
        )
        per_file_uris.append([r.uri for r in refs])
        file_refs_all.extend(refs)
        groups.append((fi, refs))

    # context cache 모드 (opt-in): 전체 파일에 대해 cache 1개
    if context_cache_ttl is not None and context_cache_ttl > 0 and file_refs_all:
        hash_key = cache_store.make_context_cache_key(
            checksums,
            model=llm_input.get("model"),
            system_instruction=llm_input.get("system"),
            tools=llm_input.get("tools"),
            tool_config=llm_input.get("tool_config"),
        )
        name = await _resolve_or_create_context_cache(
            hash_key, checksums, file_refs_all,
            cache_contents=_build_labeled_cache_contents(groups),
            model=llm_input.get("model"),
            system_instruction=llm_input.get("system"),
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
            tools=llm_input.get("tools"),
            tool_config=llm_input.get("tool_config"),
        )
        if name:
            return {"context_cache_id": hash_key, "cached_content_name": name}
        # too-small fail-soft (name is None) → uri 폴백으로 떨어진다

    return per_file_uris
