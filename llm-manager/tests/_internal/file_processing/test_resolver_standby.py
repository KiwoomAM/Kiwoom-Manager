"""resolver.py dedup 통합 테스트.

files_api + context_cache 2계층의 standby 경로(선점/대기→HIT/timeout)를
실제 asyncio 동시성으로 검증.

Mock:
- resolver.upload_file: 카운터 + FileRef 반환
- resolver.create_context_cache: 카운터 + CacheRef 반환
- dedup.RedisManager: FakeRedisHandler (conftest fake_redis fixture)
- cache_store: redis-handler의 memory fallback 사용

공유 fixture: tests/logic/conftest.py (fake_redis, fast_backoff)
"""

from __future__ import annotations

import asyncio

from llm_manager._internal.file_processing import cache_store
from llm_manager._internal.file_processing.resolver import _process_files_api
from llm_manager._internal.handler_client.models import CacheRef, FileRef
from llm_manager._internal.keying import make_file_checksum
from llm_manager.models import FileInput

# ── 헬퍼 ──────────────────────────────────────────────────────


def _pdf_input(name: str, size: int = 30_000_000) -> FileInput:
    """Files API 경로로 가도록 >20MB PDF FileInput 생성."""
    return FileInput(
        file_name=name,
        file_mtime=0,
        file_size=size,
        mime_type="application/pdf",
        extension="pdf",
        data=b"dummy pdf bytes",
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
        fi = _pdf_input("concurrent_same.pdf")
        checksum = make_file_checksum(fi)

        upload_count = 0

        async def fake_upload_file(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(0.05)
            return _fake_fileref(f"https://example.com/concurrent-{upload_count}")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload_file,
        )

        async def _run():
            return await _process_files_api(
                [fi], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=None, standby_ttl_seconds=10,
            )

        results = await asyncio.gather(_run(), _run())

        assert upload_count == 1, f"upload_file 중복 호출 (count={upload_count})"

        # 양쪽 task 모두 같은 URI 사용
        uri_a = results[0][0][0].file_uri
        uri_b = results[1][0][0].file_uri
        assert uri_a == uri_b == "https://example.com/concurrent-1"

    async def test_timeout_waits_for_slow_leader_instead_of_duplicating(
        self, fake_redis, fast_backoff, patient_waiter, monkeypatch,
    ):
        """A가 standby를 오래 잡고 있으면 B는 timeout 후에도 **중복 업로드하지 않는다**.

        (계약 변경 — docs/31 C2) 예전엔 timeout 즉시 자기가 업로드했다(Y' Hybrid).
        그러면 대기자 27명이 동시에 timeout 에 도달할 때 27개가 동시에 101MB 를 받는다.
        이제는 timeout 에서도 SETNX 재획득을 거쳐, 성공한 하나만 leader 가 된다.
        여기선 A 가 standby 를 계속 들고 있어 B 의 재획득이 실패하므로, B 는 자기가
        일하지 않고 A 를 기다렸다가 결과를 재사용한다.
        """
        fi = _pdf_input("slow_upload.pdf")
        checksum = make_file_checksum(fi)

        upload_count = 0

        async def very_slow_upload(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(1.5)  # standby_ttl(1)보다 김 → B는 timeout
            return _fake_fileref(f"https://example.com/slow-{upload_count}")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", very_slow_upload,
        )

        async def _run():
            return await _process_files_api(
                [fi], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=None, standby_ttl_seconds=1,
            )

        await asyncio.gather(_run(), _run())

        assert upload_count == 1, f"timeout 후 중복 업로드 발생(herd), 실제={upload_count}"

    async def test_different_checksums_upload_in_parallel(
        self, fake_redis, fast_backoff, monkeypatch,
    ):
        """다른 checksum 2개 → 둘 다 선점 → 병렬 upload (standby 충돌 없음)."""
        fi_a = _pdf_input("parallel_a.pdf", size=30_000_000)
        fi_b = _pdf_input("parallel_b.pdf", size=40_000_000)
        cs_a = make_file_checksum(fi_a)
        cs_b = make_file_checksum(fi_b)
        assert cs_a != cs_b

        upload_count = 0

        async def fake_upload_file(path, mime_type):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(0.01)
            return _fake_fileref(f"https://example.com/parallel-{upload_count}")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.upload_file", fake_upload_file,
        )

        async def _run(fi, cs):
            return await _process_files_api(
                [fi], checksums=[cs],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=None, standby_ttl_seconds=10,
            )

        await asyncio.gather(_run(fi_a, cs_a), _run(fi_b, cs_b))
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
        fi = _pdf_input("ctx_concurrent.pdf")
        checksum = make_file_checksum(fi)

        # files_api 사전 저장 — 미리 업로드된 상태로 만들어 context_cache만 테스트
        cache_store.save_files_api(checksum, {
            "file_checksum": checksum,
            "file_name": fi.file_name,
            "file_size": fi.file_size,
            "mime_type": fi.mime_type,
            "uris": ["https://example.com/ctx-preloaded"],
            "split": False,
        })

        create_count = 0

        async def fake_create_context_cache(file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None):
            nonlocal create_count
            create_count += 1
            await asyncio.sleep(0.05)
            return _fake_cacheref(f"cachedContents/ctx-{create_count}")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache",
            fake_create_context_cache,
        )

        async def _run():
            return await _process_files_api(
                [fi], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction="ctx test",
                context_cache_ttl=3600, standby_ttl_seconds=10,
            )

        results = await asyncio.gather(_run(), _run())

        assert create_count == 1, f"create_context_cache 중복 호출 (count={create_count})"
        # 양쪽 task의 cached_content_name 동일
        assert results[0][1] == results[1][1] == "cachedContents/ctx-1"

    async def test_timeout_waits_for_slow_leader_instead_of_duplicating_create(
        self, fake_redis, fast_backoff, patient_waiter, monkeypatch,
    ):
        """A가 create를 오래 잡으면 B는 timeout 후 자기가 create (Y' Hybrid)."""
        fi = _pdf_input("ctx_slow.pdf")
        checksum = make_file_checksum(fi)

        cache_store.save_files_api(checksum, {
            "file_checksum": checksum,
            "file_name": fi.file_name,
            "file_size": fi.file_size,
            "mime_type": fi.mime_type,
            "uris": ["https://example.com/ctx-slow-preloaded"],
            "split": False,
        })

        create_count = 0

        async def very_slow_create(file_refs, *, model, system_instruction, ttl, tools=None, tool_config=None):
            nonlocal create_count
            create_count += 1
            await asyncio.sleep(1.5)  # standby_ttl(1)보다 김
            return _fake_cacheref(f"cachedContents/slow-{create_count}")

        monkeypatch.setattr(
            "llm_manager._internal.file_processing.resolver.create_context_cache",
            very_slow_create,
        )

        async def _run():
            return await _process_files_api(
                [fi], checksums=[checksum],
                model="gemini-2.5-flash", system_instruction=None,
                context_cache_ttl=3600, standby_ttl_seconds=1,
            )

        await asyncio.gather(_run(), _run())

        # (계약 변경 — docs/31 C2) timeout 즉시 자기가 생성하던 Y' Hybrid 대신,
        # SETNX 재획득에 실패하면 느린 leader 를 기다려 결과를 재사용한다.
        assert create_count == 1, f"timeout 후 중복 생성 발생(herd), 실제={create_count}"
