# -*- coding: utf-8 -*-
"""FileManagerHandler 오케스트레이션 테스트 (mdb + file-handler 모킹)."""

import asyncio

import pytest

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


async def test_register_or_get_serializes_same_key(monkeypatch):
    """같은 키 동시 호출 → 락으로 직렬화되어 upload_file은 1회만, 둘은 같은 file_path."""
    fm = FileManagerHandler()
    stored: dict = {}
    upload_calls: list = []

    async def fake_check(meta):
        key = (meta["file_name"], meta["file_mtime"], meta["file_size"])
        if key in stored:
            return {"hit": True, "file_path": stored[key]}
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        # 느린 저장 흉내: 여기서 yield되어, 락이 없으면 다른 코루틴이 끼어들어 둘 다 업로드함.
        await asyncio.sleep(0.01)
        key = (meta["file_name"], meta["file_mtime"], meta["file_size"])
        path = f"path-{len(upload_calls)}"
        upload_calls.append(key)
        stored[key] = path
        return path

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)

    metadata = {
        "file_name": "r.pdf", "file_mtime": 1, "file_size": 2,
        "mime_type": "application/pdf", "extension": "pdf",
    }
    r1, r2 = await asyncio.gather(
        fm.register_or_get(b"X", dict(metadata)),
        fm.register_or_get(b"X", dict(metadata)),
    )

    assert len(upload_calls) == 1                  # 락 덕분에 업로드 1회
    assert r1["file_path"] == r2["file_path"]      # 둘 다 같은(이긴) 경로


async def test_register_or_get_different_keys_dont_block(monkeypatch):
    """다른 키는 서로 막지 않는다(per-key 검증). 전역 락이면 이 핸드셰이크가 데드락→타임아웃."""
    fm = FileManagerHandler()
    a_in = asyncio.Event()
    b_in = asyncio.Event()

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        name = meta["file_name"]
        if name == "A":
            a_in.set()
            await asyncio.wait_for(b_in.wait(), timeout=1.0)  # A는 B가 업로드 시작해야 진행
        else:
            b_in.set()
            await asyncio.wait_for(a_in.wait(), timeout=1.0)  # B는 A가 업로드 시작해야 진행
        return f"path-{name}"

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)

    base = {"file_mtime": 1, "file_size": 2, "mime_type": "application/pdf", "extension": "pdf"}
    rA, rB = await asyncio.gather(
        fm.register_or_get(b"X", {**base, "file_name": "A"}),
        fm.register_or_get(b"Y", {**base, "file_name": "B"}),
    )

    assert rA["file_path"] == "path-A"
    assert rB["file_path"] == "path-B"
