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


@pytest.fixture(autouse=True)
def service_name_env(monkeypatch):
    """등록 경로(register_or_get·upload_file)의 전제조건을 깔아 둔다.

    부재를 검증하는 테스트는 본문에서 각자 ``monkeypatch.delenv`` 한다 —
    fixture가 가드 자체를 가리지 않도록.
    """
    monkeypatch.setenv("SERVICE_NAME", "kiwoom-test")


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

    monkeypatch.setenv("SERVICE_NAME", "kiwoom-test")
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
            "service_name": "kiwoom-test",
        }
    }


async def test_upload_file_without_service_name_raises_before_storing(monkeypatch):
    """SERVICE_NAME 미설정이면 FMConfigError.

    파일 바이트를 저장하기 **전에** 막는다 — 저장 후에 터지면 메타 없는
    orphan 파일이 file-handler에 남는다(MetadataNotFoundError의 원인).
    """
    from file_manager.exceptions import FMConfigError

    monkeypatch.delenv("SERVICE_NAME", raising=False)
    calls = {"post": 0, "insert": 0}

    async def fake_post(path, files=None, data=None):
        calls["post"] += 1
        return {"file_path": "uuid123.xlsx"}

    async def fake_insert(document):
        calls["insert"] += 1
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
    with pytest.raises(FMConfigError):
        await fm.upload_file(b"BYTES", metadata)

    assert calls == {"post": 0, "insert": 0}


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


async def test_get_file_drops_fields_outside_the_documented_contract(monkeypatch):
    """저장 문서에 필드가 늘어도 반환은 문서화된 6키 그대로.

    upload_file이 service_name을 저장하면서 저장 문서는 7필드가 됐지만, 옛 문서는
    여전히 6필드다. 문서를 그대로 내보내면 반환 모양이 **파일 등록 시점에 따라**
    달라져, 소비처가 `**meta`로 extra="forbid" 모델에 넣을 때 비결정적으로 깨진다.
    """
    async def fake_find(filter):
        return {
            "metadata": {
                "file_name": "r.xlsx",
                "file_mtime": 1713000000,
                "file_size": 100,
                "file_path": "uuid123.xlsx",
                "mime_type": "application/vnd.ms-excel",
                "extension": "xlsx",
                "service_name": "kiwoom-etf",
            }
        }

    async def fake_get_bytes(path):
        return b"BYTES", {}

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    monkeypatch.setattr(fm._client, "get_bytes_with_headers", fake_get_bytes)

    _data, meta = await fm.get_file("uuid123.xlsx")
    assert set(meta.keys()) == {
        "file_name", "file_mtime", "file_size", "file_path", "mime_type", "extension"
    }


async def test_get_metadata_returns_metadata_without_fetching_bytes(monkeypatch):
    """메타만 조회하고 파일 바이트는 받지 않는다 — 이 메서드의 존재 이유.

    바이트를 받아버리면 get_file과 다를 게 없으므로, file-handler 호출이 일어나면
    실패하도록 fake를 심어 둔다.
    """
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

    async def forbidden_get_bytes(path):
        raise AssertionError(f"바이트를 받으면 안 된다: {path}")

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    monkeypatch.setattr(fm._client, "get_bytes_with_headers", forbidden_get_bytes)

    meta = await fm.get_metadata("uuid123.xlsx")
    assert meta == {
        "file_name": "r.xlsx",
        "file_mtime": 1713000000,
        "file_size": 100,
        "file_path": "uuid123.xlsx",
        "mime_type": "application/vnd.ms-excel",
        "extension": "xlsx",
    }


async def test_get_metadata_drops_fields_outside_the_documented_contract(monkeypatch):
    """반환 6키 고정 — get_file과 같은 계약(등록 시점에 따라 모양이 달라지지 않게)."""
    async def fake_find(filter):
        return {
            "metadata": {
                "file_name": "r.xlsx",
                "file_mtime": 1713000000,
                "file_size": 100,
                "file_path": "uuid123.xlsx",
                "mime_type": "application/vnd.ms-excel",
                "extension": "xlsx",
                "service_name": "kiwoom-etf",
            }
        }

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)

    meta = await fm.get_metadata("uuid123.xlsx")
    assert set(meta.keys()) == {
        "file_name", "file_mtime", "file_size", "file_path", "mime_type", "extension"
    }


def test_metadata_not_found_error_is_importable_from_package():
    """README가 사용자에게 약속하는 예외는 top-level에서 잡을 수 있어야 한다.

    `get_metadata`/`get_file`의 실패 계약이 `MetadataNotFoundError`인데, README 관례인
    `from file_manager import ...`로 꺼낼 수 없으면 소비처가 잡을 방법을 문서에서
    찾지 못한다(문서가 약속한 계약을 패키지가 안 내주는 상태).
    """
    import file_manager
    from file_manager import MetadataNotFoundError

    assert "MetadataNotFoundError" in file_manager.__all__
    assert issubclass(MetadataNotFoundError, file_manager.FMError)


def test_mdb_connection_error_is_importable_from_package():
    """`get_metadata`의 **유일한** 연결 실패 계약 — top-level에서 잡을 수 있어야 한다.

    `get_metadata`는 file-handler를 부르지 않으므로 `FMConnectionError`가 아니라 이쪽만
    난다. export가 없으면 소비처가 `file_manager.exceptions`를 직접 뚫어야 한다 —
    실제로 Kiwoom-ESG `backend/LLM/evaluator.py`가 그렇게 하고 있다.
    """
    import file_manager
    from file_manager import MDBConnectionError

    assert "MDBConnectionError" in file_manager.__all__
    assert issubclass(MDBConnectionError, file_manager.FMError)


async def test_get_file_metadata_matches_get_metadata(monkeypatch):
    """두 메서드의 메타 반환은 하나의 계약이다 — 갈라지는 순간 여기서 깨진다.

    `get_file`이 `get_metadata`에 위임하는 덕에 지금은 자동으로 같지만, 그 구조를
    되돌려 조회·선별을 다시 인라인해도 개별 테스트는 전부 통과한다(각자 자기 fake만
    본다). 그래서 "둘이 같다"를 직접 단정해 드리프트를 막는다. 위임이라는 **구조**가
    아니라 문서가 약속한 **계약**을 잠그는 쪽을 택했다 — 구현을 바꿔도 계약만 지키면
    통과해야 하기 때문.
    """
    stored_doc = {
        "metadata": {
            "file_name": "r.xlsx",
            "file_mtime": 1713000000,
            "file_size": 100,
            "file_path": "uuid123.xlsx",
            "mime_type": "application/vnd.ms-excel",
            "extension": "xlsx",
            "service_name": "kiwoom-etf",
        }
    }

    async def fake_find(filter):
        return stored_doc

    async def fake_get_bytes(path):
        return b"BYTES", {}

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    monkeypatch.setattr(fm._client, "get_bytes_with_headers", fake_get_bytes)

    _data, from_get_file = await fm.get_file("uuid123.xlsx")
    from_get_metadata = await fm.get_metadata("uuid123.xlsx")

    assert from_get_file == from_get_metadata


def test_metadata_not_found_error_is_a_lookup_miss_not_a_server_fault():
    """조회 미스는 4xx다 — `get_metadata`가 공개 lookup이 되면서 의미가 넓어졌다.

    `get_file` 안에서만 던져질 때는 "파일은 있는데 메타가 없다"는 정합성 오류(5xx)가
    맞았다. 이제는 임의 `file_path`로 조회하는 공개 메서드의 실패 계약이라, stale하거나
    오타 난 경로를 넘기는 **정상 흐름**에서도 나온다. 5xx로 두면 소비처가
    `status_code >= 500`으로 건 알림·재시도가 단순 조회 미스마다 울린다.
    """
    from file_manager import MetadataNotFoundError

    assert MetadataNotFoundError("missing.pdf").status_code == 404


async def test_get_metadata_missing_raises_same_error_as_get_file(monkeypatch):
    from file_manager.exceptions import MetadataNotFoundError

    async def fake_find(filter):
        return None

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    with pytest.raises(MetadataNotFoundError):
        await fm.get_metadata("missing.xlsx")


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


async def test_register_or_get_requires_service_name_even_on_cache_hit(monkeypatch):
    """캐시 히트여도 진입부에서 SERVICE_NAME을 요구한다.

    가드가 upload_file 안에만 있으면 히트가 나는 동안은 통과하므로, env를 빠뜨린
    배포가 "처음 보는 파일이 들어오는 순간"에야 요청 도중 터진다. 진입부에서 한 번
    더 막아 설정 실수가 배포 직후 결정적으로 드러나게 한다.
    """
    from file_manager.exceptions import FMConfigError

    monkeypatch.delenv("SERVICE_NAME", raising=False)
    fm = FileManagerHandler()
    calls = {"check": 0}

    async def fake_check(meta):
        calls["check"] += 1
        return {"hit": True, "file_path": "existing.pdf"}

    monkeypatch.setattr(fm, "check_cache", fake_check)

    with pytest.raises(FMConfigError):
        await fm.register_or_get(b"X", dict(_META))

    assert calls["check"] == 0


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
        return "tok"

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
        return "tok"

    async def fake_release(key, token):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "new.pdf"
    assert calls == {"upload": 1, "release": 1}


async def test_register_or_get_return_keeps_six_field_contract(monkeypatch):
    """반환 dict는 5 메타 + file_path = 6 필드. service_name이 새어나가면 안 된다.

    소비처(Kiwoom-ESG source_verify)가 `StoredFile(**out)`으로 펼쳐 넣는데
    StoredFile은 extra="forbid"라, 필드가 하나라도 늘면 ValidationError로 깨진다.
    service_name은 저장 문서에만 남기고 반환 계약은 건드리지 않는다.
    """
    fm = FileManagerHandler()

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        return "new.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return "tok"

    async def fake_release(key, token):
        pass

    # upload_file을 목으로 막으므로 SERVICE_NAME은 필요 없다 — 이 테스트가 보는 것은
    # "업로드 결과를 반환 dict에 합칠 때 필드가 늘지 않는가" 하나뿐이다.
    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert set(out.keys()) == {
        "file_name", "file_mtime", "file_size", "mime_type", "extension", "file_path"
    }


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
        return None

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
        return None

    async def fake_wait(key, timeout, find_cache_fn):
        return None

    async def fake_release(key, token):
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
    """Redis 미연결(try_acquire RuntimeError) → standby 없이 직접 업로드(degrade), release 안 함."""
    fm = FileManagerHandler()
    calls = {"upload": 0, "release": 0}

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "degraded.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        raise RuntimeError("redis down")

    async def fake_release(key, token):
        calls["release"] += 1

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)
    monkeypatch.setattr(dedup_mod, "try_acquire_standby", fake_acquire)
    monkeypatch.setattr(dedup_mod, "release_standby", fake_release)

    out = await fm.register_or_get(b"X", dict(_META))
    assert out["file_path"] == "degraded.pdf"
    assert calls == {"upload": 1, "release": 0}   # degrade는 standby 미보유 → release 없음


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
        return None

    async def fake_wait(key, timeout, find_cache_fn):
        raise RuntimeError("redis hiccup")

    async def fake_release(key, token):
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
        return "tok"

    async def fake_release(key, token):
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
    # 1라운드: 남이 선점(None) → wait None(선점자 실패). 2라운드: 재선점 성공(토큰).
    acquire_seq = iter([None, "tok"])

    async def fake_check(meta):
        return {"hit": False, "file_path": None}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "reacquired.pdf"

    async def fake_acquire(key, ttl_seconds=None):
        return next(acquire_seq)

    async def fake_wait(key, timeout, find_cache_fn):
        return None  # 선점자가 저장 못 하고 standby 해제 → 재선점 유도

    async def fake_release(key, token):
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
