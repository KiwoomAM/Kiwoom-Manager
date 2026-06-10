# -*- coding: utf-8 -*-
"""file_manager._mdb 단위 테스트."""

import pytest

import file_manager._mdb as mdb_mod
from file_manager.exceptions import MDBConnectionError


class _FakeBound:
    """with_caller(...)가 돌려주는 bound view 흉내."""

    def __init__(self, recorder):
        self._rec = recorder

    async def find_one(self, filter):
        self._rec["find_filter"] = filter
        return self._rec["find_return"]

    async def insert_one(self, document):
        self._rec["insert_doc"] = document
        return self._rec["insert_return"]


class _FakeMdb:
    def __init__(self, recorder):
        self._rec = recorder

    def with_caller(self, caller):
        self._rec["caller"] = caller
        return _FakeBound(self._rec)


@pytest.fixture
def fake_mdb(monkeypatch):
    rec = {"find_return": None, "insert_return": "id-1"}
    monkeypatch.setattr(mdb_mod, "_get_mdb", lambda: _FakeMdb(rec))
    return rec


async def test_find_metadata_injects_caller(fake_mdb):
    fake_mdb["find_return"] = {"metadata": {"file_path": "abc.xlsx"}}
    doc = await mdb_mod.find_metadata({"metadata.file_path": "abc.xlsx"})
    assert doc == {"metadata": {"file_path": "abc.xlsx"}}
    assert fake_mdb["caller"] == "file-manager"
    assert fake_mdb["find_filter"] == {"metadata.file_path": "abc.xlsx"}


async def test_insert_metadata_injects_caller(fake_mdb):
    new_id = await mdb_mod.insert_metadata({"metadata": {"file_path": "abc.xlsx"}})
    assert new_id == "id-1"
    assert fake_mdb["caller"] == "file-manager"
    assert fake_mdb["insert_doc"] == {"metadata": {"file_path": "abc.xlsx"}}


async def test_find_metadata_translates_connection_error(monkeypatch):
    from mdb_manager.exceptions import MDBConnectionError as SdkErr

    class _Boom:
        def with_caller(self, caller):
            return self

        async def find_one(self, filter):
            raise SdkErr("down")

    monkeypatch.setattr(mdb_mod, "_get_mdb", lambda: _Boom())
    with pytest.raises(MDBConnectionError):
        await mdb_mod.find_metadata({"x": 1})


async def test_insert_metadata_translates_handler_error(monkeypatch):
    from mdb_manager.exceptions import MDBHandlerError as SdkHandlerErr

    class _Boom:
        def with_caller(self, caller):
            return self

        async def insert_one(self, document):
            raise SdkHandlerErr("bad request", 400)

    monkeypatch.setattr(mdb_mod, "_get_mdb", lambda: _Boom())
    with pytest.raises(MDBConnectionError):
        await mdb_mod.insert_metadata({"metadata": {}})


async def test_find_metadata_translates_handler_error(monkeypatch):
    from mdb_manager.exceptions import MDBHandlerError as SdkHandlerErr

    class _Boom:
        def with_caller(self, caller):
            return self

        async def find_one(self, filter):
            raise SdkHandlerErr("bad request", 400)

    monkeypatch.setattr(mdb_mod, "_get_mdb", lambda: _Boom())
    with pytest.raises(MDBConnectionError):
        await mdb_mod.find_metadata({"x": 1})


async def test_close_mdb_resets_singleton(monkeypatch):
    closed = {"n": 0}

    class _Closable:
        async def aclose(self):
            closed["n"] += 1

    # 싱글톤을 직접 채워 close_mdb가 닫고 None으로 리셋하는지 검증
    mdb_mod._mdb = _Closable()
    await mdb_mod.close_mdb()
    assert closed["n"] == 1
    assert mdb_mod._mdb is None
    # 두 번째 호출은 안전(no-op)
    await mdb_mod.close_mdb()
    assert closed["n"] == 1
