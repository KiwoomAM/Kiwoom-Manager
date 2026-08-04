# -*- coding: utf-8 -*-
"""register_from_bytes / register_from_path 편의 메서드 테스트 (이슈 #81).

두 메서드는 기존 build_metadata + register_or_get을 조합하는 얇은 래퍼다.
조합 지점인 register_or_get을 모킹하고, build_metadata는 실제로 돌려
'무엇이 register_or_get에 전달되는가'를 검증한다.
"""

from pathlib import Path

import pytest

from file_manager.handler import FileManagerHandler


@pytest.fixture(autouse=True)
def reset_singleton():
    FileManagerHandler.reset()
    yield
    FileManagerHandler.reset()


@pytest.fixture(autouse=True)
def service_name_env(monkeypatch):
    """등록 경로의 전제조건 — register_or_get이 진입부에서 SERVICE_NAME을 요구한다."""
    monkeypatch.setenv("SERVICE_NAME", "kiwoom-test")


def _capture_register_or_get(fm):
    """register_or_get을 캡처용으로 교체하고 captured dict를 돌려준다."""
    captured = {}

    async def fake_register_or_get(bytes_data, metadata):
        captured["bytes"] = bytes_data
        captured["metadata"] = metadata
        return {**metadata, "file_path": "stored.pdf"}

    fm.register_or_get = fake_register_or_get
    return captured


async def test_register_from_bytes_derives_size_and_delegates():
    """file_size는 len(data)로 내부 계산, mime/extension은 파일명에서 파생,
    결과는 register_or_get 반환값 그대로."""
    fm = FileManagerHandler()
    captured = _capture_register_or_get(fm)

    data = b"hello-world-bytes"
    out = await fm.register_from_bytes(
        data, file_name="report.pdf", file_mtime=1713000000
    )

    assert captured["bytes"] == data
    assert captured["metadata"] == {
        "file_name": "report.pdf",
        "file_mtime": 1713000000,
        "file_size": len(data),
        "mime_type": "application/pdf",
        "extension": "pdf",
    }
    assert out["file_path"] == "stored.pdf"


async def test_register_from_bytes_cache_hit_reuses_without_upload(monkeypatch):
    """캐시 히트면 register_or_get을 통해 기존 file_path 재사용, 업로드 없음."""
    fm = FileManagerHandler()
    calls = {"upload": 0}

    async def fake_check(meta):
        return {"hit": True, "file_path": "existing.pdf"}

    async def fake_upload(data, meta):
        calls["upload"] += 1
        return "new.pdf"

    monkeypatch.setattr(fm, "check_cache", fake_check)
    monkeypatch.setattr(fm, "upload_file", fake_upload)

    data = b"hello"
    out = await fm.register_from_bytes(
        data, file_name="report.pdf", file_mtime=1713000000
    )

    assert out["file_path"] == "existing.pdf"
    assert out["file_size"] == len(data)   # 메타는 여전히 정상 구성
    assert calls["upload"] == 0            # 히트라 업로드 안 함


async def test_register_from_path_derives_name_mtime_size(tmp_path: Path):
    """경로에서 file_name(=name), file_mtime(=int(mtime)), file_size(=len)를 도출."""
    fm = FileManagerHandler()
    captured = _capture_register_or_get(fm)

    p = tmp_path / "규약.pdf"
    p.write_bytes(b"PDFDATA")

    out = await fm.register_from_path(p)

    meta = captured["metadata"]
    assert captured["bytes"] == b"PDFDATA"
    assert meta["file_name"] == "규약.pdf"
    assert meta["file_size"] == len(b"PDFDATA")
    assert meta["file_mtime"] == int(p.stat().st_mtime)
    assert isinstance(meta["file_mtime"], int)
    assert meta["extension"] == "pdf"
    assert out["file_path"] == "stored.pdf"


async def test_register_from_path_missing_file_raises(tmp_path: Path):
    """없는 파일이면 FileNotFoundError를 번역 없이 전파한다 (FAIL-LOUD)."""
    fm = FileManagerHandler()
    _capture_register_or_get(fm)

    with pytest.raises(FileNotFoundError):
        await fm.register_from_path(tmp_path / "does-not-exist.pdf")
