"""Context Cache expire e2e — 실제 Gemini + Redis + file-manager 통합.

수동 실행 (Gemini 비용 발생). 전제:
- llm-request Service 가 LLM_REQUEST_URL 에 떠있음
- 192.7.9.45 file-manager / redis 접근 가능
- kw_ai_dev database 사용

실행:
    RUN_INTEGRATION=1 LLM_REQUEST_URL=http://localhost:8101 \\
    pytest service/tests/integration/test_expire_e2e.py -v -s
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from file_manager import FileManagerHandler
from llm_handler import aio as llm_aio

from app.logic.file_processing import cache_store
from llm_request import (
    StoredFile,
    expire_context_cache_async,
    llm_request_async,
)

SAMPLE_PDF = (
    Path(__file__).resolve().parents[3] / "quickstarts" / "medium_file.pdf"
)


@pytest.mark.asyncio
async def test_expire_e2e_shortens_gemini_cache_ttl():
    """cache 생성 → expire → Gemini expire_time 단축 확인."""
    assert SAMPLE_PDF.exists(), f"sample PDF 없음: {SAMPLE_PDF}"

    # 1. file-manager 등록
    fm = FileManagerHandler()
    metadata = fm.build_metadata(
        file_name=SAMPLE_PDF.name,
        file_mtime=int(SAMPLE_PDF.stat().st_mtime),
        file_size=SAMPLE_PDF.stat().st_size,
    )
    sf_dict = await fm.register_or_get(SAMPLE_PDF.read_bytes(), metadata)

    # 2. Context Cache 생성 요청 (TTL 1h, live 호출 강제)
    # contents 에 uuid 를 박아 MDB cache miss → live LLM 호출 보장
    unique = uuid.uuid4().hex[:8]
    _, meta = await llm_request_async(
        {
            "contents": f"이 파일의 한 줄 요약 ({unique})",
            "model": "gemini-2.5-flash",
        },
        stored_files=[StoredFile(**sf_dict)],
        timings={"context_cache_ttl": 3600},
        database="kw_ai_dev",
    )
    assert meta.context_cache_id is not None, (
        "Context Cache 가 생성되지 않음 — "
        "토큰이 모델 최소치를 못 채웠을 가능성 (더 큰 파일 사용)"
    )
    cache_id = meta.context_cache_id

    # 3. Gemini 측 expire_time 확인 (단축 전)
    doc = cache_store.find_context_cache(cache_id)
    assert doc is not None, "Redis context_cache doc 없음"
    name = doc["cached_content_name"]

    cache_before = await llm_aio.get_cache(name)
    expire_before = cache_before.expire_time
    print(f"\n[before] cache={name} expire_time={expire_before}")

    # 4. expire 호출 (grace 60s)
    result = await expire_context_cache_async([cache_id], grace_seconds=60)
    assert result.expired == [cache_id]
    assert result.failed == []

    # 5. Gemini expire_time 단축 검증
    await asyncio.sleep(1)
    cache_after = await llm_aio.get_cache(name)
    print(f"[after]  cache={name} expire_time={cache_after.expire_time}")

    assert cache_after.expire_time < expire_before, (
        f"expire_time 단축 안 됨: "
        f"before={expire_before} after={cache_after.expire_time}"
    )


@pytest.mark.asyncio
async def test_expire_e2e_idempotent_for_unknown_id():
    """존재하지 않는 cache_id → expired 분류 (idempotent)."""
    fake_id = "a" * 64
    result = await expire_context_cache_async([fake_id], grace_seconds=60)
    assert result.expired == [fake_id]
    assert result.failed == []
