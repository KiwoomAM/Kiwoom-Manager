# -*- coding: utf-8 -*-
"""FileManagerHandler 오케스트레이션 테스트 (mdb + file-handler 모킹)."""

import pytest

import file_manager._dedup as dedup_mod
import file_manager.handler as handler_mod
from file_manager.handler import FileManagerHandler


@pytest.fixture(autouse=True)
def reset_singleton():
    FileManagerHandler.reset()
    yield
    FileManagerHandler.reset()


async def test_check_cache_hit(monkeypatch):
    captured = {}

    async def fake_find(filter):
        captured["filter"] = filter
        return {"metadata": {"file_path": "abc.xlsx"}}

    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    fm = FileManagerHandler()
    result = await fm.check_cache(
        {"file_name": "r.xlsx", "file_mtime": 1713000000, "file_size": 100}
    )
    assert result == {"hit": True, "file_path": "abc.xlsx"}
    assert captured["filter"] == {
        "metadata.file_name": "r.xlsx",
        "metadata.file_mtime": 1713000000,
        "metadata.file_size": 100,
    }


async def test_check_cache_miss(monkeypatch):
    async def fake_find(filter):
        return None

    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    fm = FileManagerHandler()
    result = await fm.check_cache(
        {"file_name": "r.xlsx", "file_mtime": 1, "file_size": 2}
    )
    assert result == {"hit": False, "file_path": None}


async def test_upload_file_stores_then_records_metadata(monkeypatch):
    posted = {}
    inserted = {}

    async def fake_post(path, files=None, data=None):
        posted["path"] = path
        posted["files"] = files
        posted["data"] = data
        return {"file_path": "uuid123.xlsx"}

    async def fake_insert(document):
        inserted["doc"] = document
        return "id-1"

    fm = FileManagerHandler()
    monkeypatch.setattr(fm._client, "post", fake_post)
    monkeypatch.setattr(handler_mod, "insert_metadata", fake_insert)

    metadata = {
        "file_name": "r.xlsx",
        "file_mtime": 1713000000,
        "file_size": 100,
        "mime_type": "application/vnd.ms-excel",
        "extension": "xlsx",
    }
    file_path = await fm.upload_file(b"BYTES", metadata)

    assert file_path == "uuid123.xlsx"
    assert posted["path"] == "/v1/files/upload"
    assert posted["data"] == {"extension": "xlsx"}
    assert posted["files"]["file"][0] == "r.xlsx"
    assert inserted["doc"] == {
        "metadata": {
            "file_name": "r.xlsx",
            "file_mtime": 1713000000,
            "file_size": 100,
            "file_path": "uuid123.xlsx",
            "mime_type": "application/vnd.ms-excel",
            "extension": "xlsx",
        }
    }


async def test_get_file_returns_bytes_and_metadata(monkeypatch):
    async def fake_find(filter):
        assert filter == {"metadata.file_path": "uuid123.xlsx"}
        return {
            "metadata": {
                "file_name": "r.xlsx",
                "file_mtime": 1713000000,
                "file_size": 100,
                "file_path": "uuid123.xlsx",
                "mime_type": "application/vnd.ms-excel",
                "extension": "xlsx",
            }
        }

    async def fake_get_bytes(path):
        assert path == "/v1/files/uuid123.xlsx"
        return b"BYTES", {}

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    monkeypatch.setattr(fm._client, "get_bytes_with_headers", fake_get_bytes)

    data, meta = await fm.get_file("uuid123.xlsx")
    assert data == b"BYTES"
    assert meta["file_path"] == "uuid123.xlsx"
    assert set(meta.keys()) == {
        "file_name", "file_mtime", "file_size", "file_path", "mime_type", "extension"
    }


async def test_get_file_missing_metadata_raises(monkeypatch):
    from file_manager.exceptions import MetadataNotFoundError

    async def fake_find(filter):
        return None

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    with pytest.raises(MetadataNotFoundError):
        await fm.get_file("missing.xlsx")


async def test_close_also_closes_mdb(monkeypatch):
    closed = {"mdb": False, "client": False}

    async def fake_close_mdb():
        closed["mdb"] = True

    async def fake_client_close():
        closed["client"] = True

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "close_mdb", fake_close_mdb)
    monkeypatch.setattr(fm._client, "close", fake_client_close)

    await fm.close()
    assert closed == {"mdb": True, "client": True}


_META = {
    "file_name": "규약.pdf", "file_mtime": 1, "file_size": 2,
    "mime_type": "application/pdf", "extension": "pdf",
}


async def test_register_or_get_cache_hit_skips_standby(monkeypatch):
    """캐시 히트면 standby·업로드 모두 안 함."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "acquire": 0}

    async def fake_check(meta):
        return {"hit": True, "file_path": "existing.pdf"}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "new.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        calls["acquire"] += 1
        return True

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "existing.pdf"
    assert calls == {"upload": 0, "acquire": 0}


async def test_register_or_get_acquire_uploads_and_releases(monkeypatch):
    """standby 선점 → 업로드 1회 + release."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "release": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "new.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return True

    async def fake_release(key):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "new.pdf"
    assert calls == {"upload": 1, "release": 1}


async def test_register_or_get_not_acquired_reuses_winner(monkeypatch):
    """미선점 → 대기 후 남이 저장한 결과 재사용, 업로드 안 함."""
    fm = FileManagerHandler()
    calls = {"upload": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "new.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return False

    async def fake_wait(key, timeout, find_cache_fn):
        return {"hit": True, "file_path": "winner.pdf"}

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "wait_for_standby_release", fake_wait)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "winner.pdf"
    assert calls["upload"] == 0


async def test_register_or_get_wait_timeout_falls_back_to_upload(monkeypatch):
    """미선점 + 대기 timeout(None) → 직접 업로드(폴백), 내 락 아니니 release 안 함."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "release": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "fallback.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return False

    async def fake_wait(key, timeout, find_cache_fn):
        return None

    async def fake_release(key):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "wait_for_standby_release", fake_wait)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "fallback.pdf"
    assert calls == {"upload": 1, "release": 0}


async def test_register_or_get_redis_down_degrades_to_upload(monkeypatch):
    """Redis 미연결(try_acquire RuntimeError) → 직접 업로드(degrade), release 시도(무해)."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "release": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "degraded.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        raise RuntimeError("redis down")

    async def fake_release(key):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "degraded.pdf"
    assert calls == {"upload": 1, "release": 1}


async def test_register_or_get_wait_raises_degrades_to_upload(monkeypatch):
    """wait_for_standby_release가 예외를 던져도 전파하지 않고 직접 업로드로 degrade."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "release": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "fallback.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return False

    async def fake_wait(key, timeout, find_cache_fn):
        raise RuntimeError("redis hiccup")

    async def fake_release(key):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "wait_for_standby_release", fake_wait)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "fallback.pdf"   # 예외 전파 대신 직접 업로드
    assert calls == {"upload": 1, "release": 0}  # 미선점이라 release 안 함


async def test_register_or_get_releases_on_upload_failure(monkeypatch):
    """선점 후 upload가 실패해도 finally에서 release가 호출된다."""
    fm = FileManagerHandler()
    calls = {"release": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        raise RuntimeError("upload boom")

    async def fake_acquire(key, ttl_seconds=None):
        return True

    async def fake_release(key):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    with pytest.raises(RuntimeError):
        await fm.register_or_get(b"X", dict(_META))
    assert calls["release"] == 1   # 업로드 실패에도 standby 해제


async def test_register_or_get_reacquires_after_wait_none(monkeypatch):
    """미선점→대기 None(선점자 실패로 standby 풀림)이면 직접 업로드가 아니라
    재선점 후 업로드로 직렬화된다 (thundering herd 회귀 방지)."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "release": 0}
    # 1라운드: 남이 선점(False) → wait None(선점자 실패). 2라운드: 재선점 성공(True).
    acquire_seq = iter([False, True])

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "reacquired.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return next(acquire_seq)

    async def fake_wait(key, timeout, find_cache_fn):
        return None  # 선점자가 저장 못 하고 standby 해제 → 재선점 유도

    async def fake_release(key):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "wait_for_standby_release", fake_wait)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "reacquired.pdf"
    assert calls["upload"] == 1    # 직접 업로드 아니라 재선점한 1명만 업로드
    assert calls["release"] == 1   # 재선점 성공분은 release
