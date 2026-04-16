"""resolver.py dedup 통합 테스트.

files_api + context_cache 2계층의 standby 경로(선점/대기→HIT/timeout)를
실제 asyncio 동시성으로 검증.

Mock:
- resolver.aget_file: dummy bytes 반환
- resolver.upload_file: 카운터 + FileRef 반환
- resolver.create_context_cache: 카운터 + CacheRef 반환
- dedup.get_client: FakeRedisClient (conftest fake_redis fixture)
- cache_store: redis-handler의 memory fallback 사용

공유 fixture: tests/logic/conftest.py (fake_redis, fast_backoff)
"""

from __future__ import annotations

import asyncio

import pytest
from file_manager import FileMeta
from llm_handler.models import CacheRef, FileRef

from app.logic.file_handler import cache_store
from app.logic.file_handler.resolver import _process_files_api
from app.logic.keying import make_file_checksum


# ── 헬퍼 ──────────────────────────────────────────────────────


def _pdf_meta(name: str, size: int = 30_000_000) -> FileMeta:
    """Files API 경로로 가도록 >20MB PDF 메타 생성."""
    return FileMeta(
        file_name=name,
        file_mtime=0,
        file_size=size,
        file_path=f"/fake/{name}",
        mime_type="application/pdf",
        extension="pdf",
    )


def _fake_fileref(uri: str) -> FileRef:
    return FileRef(
        name=f"files/{uri}",
        uri=uri,
        mime_type="application/pdf",
        state="ACTIVE",
    )


def _fake_cacheref(name: str) -> CacheRef:
    return CacheRef(name=name, model="gemini-2.5-flash")


# ── files_api standby 통합 ────────────────────────────────────


class TestFilesApiStandby:
    """files_api 업로드 dedup 통합 테스트 (context_cache_ttl=None)."""

    async def test_concurrent_same_checksum_uploads_once(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """같은 checksum 동시 요청 시 upload_file은 1회만 호출."""
        meta = _pdf_meta("concurrent_same.pdf")
        checksum = make_file_checksum(meta)

        async def slow_aget_file(path):
            await asyncio.sleep(0.05)  # standby 잡고 있을 시간 확보
            return b"dummy pdf bytes"

        upload_count = 0

        async def fake_upload_file(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(0.05)
            return _fake_fileref(f"https://example.com/concurrent-{upload_count}")

        monkeypatch.setattr(
            "app.logic.file_handler.resolver.aget_file", slow_aget_file,
        )
        monkeypatch.setattr(
            "app.logic.file_handler.resolver.upload_file", fake_upload_file,
        )

        async def _run():
            return await _process_files_api(
                [meta], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=None, standby_ttl_seconds=10,
            )

        results = await asyncio.gather(_run(), _run())

        assert upload_count == 1, f"upload_file 중복 호출 (count={upload_count})"

        # 양쪽 task 모두 같은 URI 사용
        uri_a = results[0][0][0].file_uri
        uri_b = results[1][0][0].file_uri
        assert uri_a == uri_b == "https://example.com/concurrent-1"

    async def test_timeout_falls_back_to_own_upload(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """A가 standby를 오래 잡으면 B는 timeout 후 자기가 upload (Y' Hybrid)."""
        meta = _pdf_meta("slow_upload.pdf")
        checksum = make_file_checksum(meta)

        async def fast_aget_file(path):
            return b"dummy"

        upload_count = 0

        async def very_slow_upload(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(1.5)  # standby_ttl(1)보다 김 → B는 timeout
            return _fake_fileref(f"https://example.com/slow-{upload_count}")

        monkeypatch.setattr(
            "app.logic.file_handler.resolver.aget_file", fast_aget_file,
        )
        monkeypatch.setattr(
            "app.logic.file_handler.resolver.upload_file", very_slow_upload,
        )

        async def _run():
            return await _process_files_api(
                [meta], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=None, standby_ttl_seconds=1,
            )

        await asyncio.gather(_run(), _run())

        assert upload_count == 2, f"timeout 후 양쪽 upload 예상, 실제={upload_count}"

    async def test_different_checksums_upload_in_parallel(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """다른 checksum 2개 → 둘 다 선점 → 병렬 upload (standby 충돌 없음)."""
        meta_a = _pdf_meta("parallel_a.pdf", size=30_000_000)
        meta_b = _pdf_meta("parallel_b.pdf", size=40_000_000)
        cs_a = make_file_checksum(meta_a)
        cs_b = make_file_checksum(meta_b)
        assert cs_a != cs_b

        async def fast_aget_file(path):
            return b"dummy"

        upload_count = 0

        async def fake_upload_file(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(0.01)
            return _fake_fileref(f"https://example.com/parallel-{upload_count}")

        monkeypatch.setattr(
            "app.logic.file_handler.resolver.aget_file", fast_aget_file,
        )
        monkeypatch.setattr(
            "app.logic.file_handler.resolver.upload_file", fake_upload_file,
        )

        async def _run(meta, cs):
            return await _process_files_api(
                [meta], checksums=[cs],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=None, standby_ttl_seconds=10,
            )

        await asyncio.gather(_run(meta_a, cs_a), _run(meta_b, cs_b))
        assert upload_count == 2


# ── context_cache standby 통합 ────────────────────────────────


class TestContextCacheStandby:
    """context_cache 생성 dedup 통합 테스트.

    files_api는 사전 seed하여 context_cache 경로만 검증.
    """

    async def test_concurrent_same_hash_creates_once(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """같은 files + model/system → 같은 hash_key → create_context_cache 1회만."""
        meta = _pdf_meta("ctx_concurrent.pdf")
        checksum = make_file_checksum(meta)

        # files_api 사전 저장 — 미리 업로드된 상태로 만들어 context_cache만 테스트
        cache_store.save_files_api(checksum, {
            "file_checksum": checksum,
            "file_name": meta.file_name,
            "file_size": meta.file_size,
            "mime_type": meta.mime_type,
            "uris": ["https://example.com/ctx-preloaded"],
            "split": False,
        })

        async def fast_aget_file(path):
            return b"dummy"

        create_count = 0

        async def fake_create_context_cache(file_refs, *, model, system_instruction, ttl):
            nonlocal create_count
            create_count += 1
            await asyncio.sleep(0.05)
            return _fake_cacheref(f"cachedContents/ctx-{create_count}")

        monkeypatch.setattr(
            "app.logic.file_handler.resolver.aget_file", fast_aget_file,
        )
        monkeypatch.setattr(
            "app.logic.file_handler.resolver.create_context_cache",
            fake_create_context_cache,
        )

        async def _run():
            return await _process_files_api(
                [meta], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction="ctx test",
                context_cache_ttl=3600, standby_ttl_seconds=10,
            )

        results = await asyncio.gather(_run(), _run())

        assert create_count == 1, f"create_context_cache 중복 호출 (count={create_count})"
        # 양쪽 task의 cached_content_name 동일
        assert results[0][1] == results[1][1] == "cachedContents/ctx-1"

    async def test_timeout_falls_back_to_own_create(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """A가 create를 오래 잡으면 B는 timeout 후 자기가 create (Y' Hybrid)."""
        meta = _pdf_meta("ctx_slow.pdf")
        checksum = make_file_checksum(meta)

        cache_store.save_files_api(checksum, {
            "file_checksum": checksum,
            "file_name": meta.file_name,
            "file_size": meta.file_size,
            "mime_type": meta.mime_type,
            "uris": ["https://example.com/ctx-slow-preloaded"],
            "split": False,
        })

        async def fast_aget_file(path):
            return b"dummy"

        create_count = 0

        async def very_slow_create(file_refs, *, model, system_instruction, ttl):
            nonlocal create_count
            create_count += 1
            await asyncio.sleep(1.5)  # standby_ttl(1)보다 김
            return _fake_cacheref(f"cachedContents/slow-{create_count}")

        monkeypatch.setattr(
            "app.logic.file_handler.resolver.aget_file", fast_aget_file,
        )
        monkeypatch.setattr(
            "app.logic.file_handler.resolver.create_context_cache",
            very_slow_create,
        )

        async def _run():
            return await _process_files_api(
                [meta], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=3600, standby_ttl_seconds=1,
            )

        await asyncio.gather(_run(), _run())

        assert create_count == 2
