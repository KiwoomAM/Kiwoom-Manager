"""file_processing/dedup.py 테스트.

RedisManager를 in-memory fake로 mock하여 실제 Redis 없이
SETNX/GET/DEL 동작을 검증한다.

공유 fixture(fake_redis/no_redis/fast_backoff)는
tests/logic/conftest.py 에 정의되어 있다.
"""

from __future__ import annotations

import asyncio

import pytest

from llm_manager._internal.file_processing import dedup
from llm_manager._internal.file_processing.dedup import (
    WaitResult,
    _make_standby_key,
    record_failure,
    release_standby,
    try_acquire_standby,
    wait_for_standby_release,
)
from llm_manager.exceptions import LLMManagerError, PdfPageTooLargeError

# ── _make_standby_key ────────────────────────────────────────


class TestMakeStandbyKey:
    def test_files_api_prefix(self):
        assert _make_standby_key("files_api", "abc123") == "file_cache:files_api_standby:abc123"

    def test_context_cache_prefix(self):
        assert _make_standby_key("context_cache", "h1") == "file_cache:context_cache_standby:h1"

    def test_agent_llm_prefix(self):
        assert _make_standby_key("agent_llm", "ck1") == "agent_llm_standby:ck1"

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="Unknown standby prefix"):
            _make_standby_key("unknown_prefix", "x")


# ── try_acquire_standby ──────────────────────────────────────


class TestTryAcquireStandby:
    async def test_first_acquire_returns_true(self, fake_redis):
        ok = await try_acquire_standby("files_api", "checksum1", ttl_seconds=60)
        assert ok is True
        # 실제 키 저장 확인
        assert fake_redis.get("file_cache:files_api_standby:checksum1") is not None

    async def test_second_acquire_returns_false(self, fake_redis):
        ok1 = await try_acquire_standby("files_api", "checksum1", ttl_seconds=60)
        ok2 = await try_acquire_standby("files_api", "checksum1", ttl_seconds=60)
        assert ok1 is True
        assert ok2 is False

    async def test_different_keys_both_acquire(self, fake_redis):
        ok1 = await try_acquire_standby("files_api", "k1", ttl_seconds=60)
        ok2 = await try_acquire_standby("files_api", "k2", ttl_seconds=60)
        assert ok1 is True
        assert ok2 is True

    async def test_different_prefix_no_conflict(self, fake_redis):
        """같은 key라도 prefix 다르면 네임스페이스 분리."""
        ok1 = await try_acquire_standby("files_api", "shared", ttl_seconds=60)
        ok2 = await try_acquire_standby("context_cache", "shared", ttl_seconds=60)
        ok3 = await try_acquire_standby("agent_llm", "shared", ttl_seconds=60)
        assert (ok1, ok2, ok3) == (True, True, True)

    async def test_redis_unavailable_raises(self, no_redis):
        with pytest.raises(RuntimeError, match="Redis 미연결"):
            await try_acquire_standby("files_api", "x", ttl_seconds=60)


# ── wait_for_standby_release ──────────────────────────────────


class TestWaitForStandbyRelease:
    """wait_for_standby_release 는 이제 WaitResult(verdict) 를 돌려준다.

    kind: "cached"(HIT) / "failed"(leader 실패 기록) / "lead"(자기가 작업).
    None → 곧장 자기가 작업하던 옛 herd 경로는 사라지고, 깨어나면 항상 SETNX 를
    거쳐 하나만 leader 가 된다(docs/31 §3-2).
    """

    async def test_sync_find_cache_fn_hit_returns_cached(self, fake_redis, fast_backoff):
        """sync find_cache_fn 이 HIT 반환 시 CACHED verdict."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        cached_doc = {"uris": ["u1"]}
        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=lambda: cached_doc,
        )
        assert result.kind == "cached"
        assert result.data == cached_doc

    async def test_async_find_cache_fn_hit_returns_cached(self, fake_redis, fast_backoff):
        """async find_cache_fn 도 동일하게 지원."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        cached_doc = {"cached_content_name": "cc1"}

        async def _find():
            return cached_doc

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=_find,
        )
        assert result.kind == "cached"
        assert result.data == cached_doc

    async def test_failure_record_returns_failed_with_error(self, fake_redis, fast_backoff):
        """standby 사라짐 + 실패 기록 존재 → FAILED verdict + 원본 에러 재구성."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        # leader 실패 시나리오: 실패 기록 후 standby 해제
        await record_failure("files_api", "k1", PdfPageTooLargeError(page=3, size_mb=97.1))
        await release_standby("files_api", "k1")

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=lambda: None,  # 캐시는 계속 MISS
        )
        assert result.kind == "failed"
        assert isinstance(result.error, LLMManagerError)
        assert "p.3" in str(result.error)  # 원본 message 보존
        assert result.error.error_type == "pdf_page_too_large"
        assert result.error.status_code == 422

    async def test_standby_vanished_no_record_reacquires_lead(self, fake_redis, fast_backoff):
        """standby 사라짐 + 실패 기록 없음(eviction) → SETNX 재획득 성공 → LEAD."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        async def _releaser():
            await asyncio.sleep(0.03)
            await release_standby("files_api", "k1")  # 기록 없이 사라짐

        _ = asyncio.create_task(_releaser())

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=lambda: None,
        )
        assert result.kind == "lead"
        # 재획득했으므로 standby 를 다시 들고 있어야 함
        assert fake_redis.get("file_cache:files_api_standby:k1") is not None

    async def test_timeout_returns_lead(self, fake_redis, fast_backoff):
        """timeout 도달 시 LEAD (Y' Hybrid — 자기가 작업)."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=1,
            find_cache_fn=lambda: None,
        )
        assert result.kind == "lead"

    async def test_redis_unavailable_returns_lead(self, no_redis, fast_backoff):
        """대기 중 Redis 미연결 → get None 폴백 → LEAD(자기 작업).

        v3 graceful degradation: 단절을 예외로 올리지 않고 자기가 작업한다.
        """
        result = await wait_for_standby_release("files_api", "k1", timeout=1)
        assert result.kind == "lead"


# ── 실패 기록 (negative cache) ────────────────────────────────


class TestRecordAndReconstructFailure:
    async def test_llm_error_preserves_contract(self, fake_redis):
        """LLMManagerError 하위는 status_code·error_type·message 를 보존."""
        await record_failure("files_api", "k1", PdfPageTooLargeError(page=3, size_mb=97.1))

        info = await dedup._find_failure("files_api", "k1")
        assert info is not None
        assert info["status_code"] == 422
        assert info["error_type"] == "pdf_page_too_large"
        assert "p.3" in info["message"]

        err = dedup._reconstruct_error(info)
        assert isinstance(err, LLMManagerError)
        assert err.status_code == 422
        assert err.error_type == "pdf_page_too_large"
        assert str(err) == info["message"]  # verbatim, 마스킹 없음

    async def test_non_llm_error_defaults(self, fake_redis):
        """라이브러리 예외(httpx 등)는 502 / leader_failed 로, message 는 verbatim."""
        await record_failure(
            "files_api", "k1",
            RuntimeError("peer closed connection without sending complete message body"),
        )
        info = await dedup._find_failure("files_api", "k1")
        assert info["status_code"] == 502
        assert info["error_type"] == "leader_failed"
        assert "peer closed connection" in info["message"]
        assert info["class"] == "RuntimeError"  # 원본 클래스명 진단용 보존

    async def test_per_layer_ttl(self, fake_redis):
        """실패 기록 TTL 은 계층별 — files_api 60초, agent_llm 15초."""
        await record_failure("files_api", "k1", RuntimeError("x"))
        await record_failure("agent_llm", "k2", RuntimeError("y"))
        assert fake_redis.ttls["file_cache:files_api_failed:k1"] == 60
        assert fake_redis.ttls["agent_llm_failed:k2"] == 15

    async def test_find_failure_absent_returns_none(self, fake_redis):
        assert await dedup._find_failure("files_api", "nope") is None


# ── anti-herd 불변식 (docs/31 §2) ─────────────────────────────


class TestAntiHerdInvariant:
    """leader 실패가 herd 로 번지지 않는지 — 동시 작업은 항상 ≤ 1."""

    async def test_failure_record_makes_all_waiters_fail(self, fake_redis, fast_backoff):
        """leader 가 실패 기록을 남기면, 대기자 전원이 재시도 없이 즉시 실패한다.

        101MB 재다운로드 0번 — 28건 herd 의 정상 경로 해소.
        """
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)
        await record_failure("files_api", "k1", PdfPageTooLargeError(page=3, size_mb=97.1))
        await release_standby("files_api", "k1")

        async def _waiter():
            r = await wait_for_standby_release(
                "files_api", "k1", timeout=5, find_cache_fn=lambda: None,
            )
            return r.kind

        kinds = await asyncio.gather(*[_waiter() for _ in range(10)])
        assert kinds == ["failed"] * 10  # 아무도 자기가 작업하지 않음

    async def test_eviction_only_one_becomes_leader(self, fake_redis, fast_backoff):
        """실패 기록이 없어도(eviction) 동시에 깨어난 대기자 중 하나만 LEAD.

        SETNX 폴백 안전망 — TTL 과 무관하게 불변식 유지(docs/31 §3-2).
        """
        # leader 가 기록 없이 사라진 상태
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)
        await release_standby("files_api", "k1")

        cache: dict = {}

        async def _waiter():
            r = await wait_for_standby_release(
                "files_api", "k1", timeout=5,
                find_cache_fn=lambda: cache.get("doc"),
            )
            if r.kind == "lead":
                # 승자가 작업 완료 시뮬레이션: 캐시 저장 후 standby 해제
                cache["doc"] = {"uris": ["u"]}
                await release_standby("files_api", "k1")
            return r.kind

        kinds = await asyncio.gather(*[_waiter() for _ in range(10)])
        assert kinds.count("lead") == 1  # 정확히 하나만 작업
        assert kinds.count("cached") == 9  # 나머지는 승자 결과 재사용

    async def test_timeout_does_not_hand_lead_to_everyone(
        self, fake_redis, fast_backoff, patient_waiter,
    ):
        """C2 회귀 — leader 가 실패하지 않고 '매달려' timeout 에 걸려도 herd 가 되면 안 된다.

        leader 가 standby 를 든 채 느리게 진행하면 실패 기록이 없다. 대기자들은 같은 시각에
        대기를 시작했으므로 timeout 에 **동시에** 도달한다. 이때 재획득 결과를 무시하고
        전원에게 LEAD 를 주면 27개가 동시에 101MB 를 받는다(고치려던 herd 그대로).
        """
        # 다른 leader 가 standby 를 계속 들고 있다 (느린 작업 중 — 실패도, 완료도 안 함)
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        cache: dict = {}

        async def _slow_leader():
            # 대기자들의 timeout(1초) 을 넘겨 완료 — timeout 경로를 실제로 밟게 한다
            await asyncio.sleep(1.5)
            cache["doc"] = {"uris": ["u"]}
            await release_standby("files_api", "k1")

        async def _waiter():
            r = await wait_for_standby_release(
                "files_api", "k1", timeout=1, find_cache_fn=lambda: cache.get("doc"),
            )
            return r.kind

        results = await asyncio.gather(_slow_leader(), *[_waiter() for _ in range(10)])
        kinds = results[1:]
        # 아무도 자기가 일하지 않고, 느린 leader 의 결과를 재사용해야 한다
        assert kinds.count("lead") == 0, f"timeout 경로에서 herd 발생: {kinds}"
        assert kinds.count("cached") == 10


# ── C1 회귀 — 낡은 실패 기록이 재실행을 오염시키면 안 된다 ────


class TestFailureRecordDoesNotPoisonRerun:
    """leader 가 새로 standby 를 잡으면 그 키의 낡은 실패 기록은 무효다.

    실패 기록은 '그 시도가 실패했다'는 뜻이지 '이 파일은 영영 안 된다'가 아니다.
    지우지 않으면, 실패 후 TTL(60초) 안에 재실행할 때 새 leader 는 정상 작업하는데
    대기자들만 낡은 기록을 보고 죽는다 — 실패한 배치를 곧바로 다시 돌리는 ESG 의
    정상 패턴에서 27건이 실패한다.
    """

    async def test_acquire_clears_stale_failure(self, fake_redis):
        """standby 획득에 성공하면 = 새 시도 시작 → 낡은 실패 기록을 지운다."""
        await record_failure("files_api", "ck", RuntimeError("이전 시도 실패"))
        assert await dedup._find_failure("files_api", "ck") is not None

        acquired = await try_acquire_standby("files_api", "ck", ttl_seconds=60)

        assert acquired is True
        assert await dedup._find_failure("files_api", "ck") is None, "낡은 기록이 남아있음"

    async def test_rerun_waiters_see_new_leader_not_old_failure(self, fake_redis, fast_backoff):
        """1차 실패 후 재실행 — 새 leader 가 성공하면 대기자도 그 결과를 받아야 한다."""
        # 1차: leader 실패 → 기록 남기고 해제
        await try_acquire_standby("files_api", "ck", ttl_seconds=60)
        await record_failure("files_api", "ck", RuntimeError("peer closed connection"))
        await release_standby("files_api", "ck")

        # 2차(재실행): 파일·서버 정상. 새 leader 가 선점해 작업 중.
        assert await try_acquire_standby("files_api", "ck", ttl_seconds=60) is True

        cache: dict = {}

        async def _new_leader():
            await asyncio.sleep(0.05)  # 101MB 받는 중 — 이번엔 성공
            cache["doc"] = {"uris": ["u"]}
            await release_standby("files_api", "ck")

        async def _waiter():
            v = await wait_for_standby_release(
                "files_api", "ck", timeout=5, find_cache_fn=lambda: cache.get("doc"),
            )
            return v.kind

        results = await asyncio.gather(_new_leader(), *[_waiter() for _ in range(5)])
        kinds = results[1:]
        assert kinds == ["cached"] * 5, f"낡은 실패 기록이 재실행을 오염시킴: {kinds}"


# ── release_standby ───────────────────────────────────────────


class TestReleaseStandby:
    async def test_release_removes_key(self, fake_redis):
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)
        assert fake_redis.get("file_cache:files_api_standby:k1") is not None

        await release_standby("files_api", "k1")
        assert fake_redis.get("file_cache:files_api_standby:k1") is None

    async def test_release_nonexistent_is_safe(self, fake_redis):
        """없는 키 release도 예외 없이 통과."""
        await release_standby("files_api", "nonexistent")  # no-op

    async def test_redis_unavailable_does_not_raise(self, no_redis):
        """Redis 미연결 시 조용히 return (finally 블록 보호)."""
        await release_standby("files_api", "k1")  # no raise

    async def test_redis_exception_swallowed(self, monkeypatch):
        """Redis 장애 시 예외 삼킴 (원본 예외 마스킹 방지)."""
        class ExplodingClient:
            def delete(self, *args, **kwargs):
                raise ConnectionError("redis down")

        monkeypatch.setattr(dedup, "RedisManager", lambda: ExplodingClient())
        await release_standby("files_api", "k1")  # no raise
