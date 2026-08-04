"""migrate_smb_to_fm 단위 테스트."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import file_manager.scripts.migrate_smb_to_fm as migrate_mod
from file_manager.scripts.migrate_smb_to_fm import _process_one, migrate


@pytest.mark.asyncio
async def test_migrate_without_service_name_stops_before_walking(
    tmp_path: Path, monkeypatch
):
    """SERVICE_NAME 없이 실업로드를 시작하면 트리를 걷기 전에 즉시 중단.

    _process_one의 blanket `except Exception`이 FMConfigError를 파일 단위
    "error:" 문자열로 강등하기 때문에, 이 검사가 없으면 설정 하나가 빠졌을 뿐인데
    전 항목이 실패한 매핑 JSON을 종료코드 0으로 남기고 끝난다.
    """
    from file_manager.exceptions import FMConfigError

    monkeypatch.delenv("SERVICE_NAME", raising=False)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.pdf").write_bytes(b"%PDF-1.4 test")
    mapping_out = tmp_path / "out.json"

    fm = MagicMock()
    fm.close = AsyncMock()
    monkeypatch.setattr(migrate_mod, "FileManagerHandler", MagicMock(return_value=fm))

    with pytest.raises(FMConfigError):
        await migrate(source, mapping_out, dry_run=False)

    assert not mapping_out.exists()
    fm.check_cache.assert_not_called()


@pytest.mark.asyncio
async def test_migrate_dry_run_does_not_require_service_name(
    tmp_path: Path, monkeypatch
):
    """dry_run은 업로드를 타지 않으므로 SERVICE_NAME 없이도 돈다."""
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.pdf").write_bytes(b"%PDF-1.4 test")
    mapping_out = tmp_path / "out.json"

    fm = MagicMock()
    fm.build_metadata = MagicMock(return_value={
        "file_name": "a.pdf", "file_mtime": 1, "file_size": 12,
        "mime_type": "application/pdf", "extension": "pdf",
    })
    fm.check_cache = AsyncMock(return_value={"hit": False, "file_path": None})
    fm.close = AsyncMock()
    monkeypatch.setattr(migrate_mod, "FileManagerHandler", MagicMock(return_value=fm))

    results = await migrate(source, mapping_out, dry_run=True)

    assert [r["status"] for r in results] == ["would_upload"]


@pytest.mark.asyncio
async def test_process_one_cache_hit(tmp_path: Path):
    pdf = tmp_path / "20260407_test.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    fm = MagicMock()
    fm.build_metadata = MagicMock(return_value={
        "file_name": "20260407_test.pdf",
        "file_mtime": int(pdf.stat().st_mtime),
        "file_size": pdf.stat().st_size,
        "mime_type": "application/pdf",
        "extension": "pdf",
    })
    fm.check_cache = AsyncMock(return_value={"hit": True, "file_path": "abc.pdf"})
    fm.upload_file = AsyncMock()  # 호출되면 안 됨

    result = await _process_one(pdf, fm, dry_run=False)

    assert result["status"] == "cache_hit"
    assert result["file_path"] == "abc.pdf"
    assert result["file_name"] == "20260407_test.pdf"
    fm.upload_file.assert_not_called()


@pytest.mark.asyncio
async def test_process_one_uploaded(tmp_path: Path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    fm = MagicMock()
    fm.build_metadata = MagicMock(return_value={
        "file_name": "test.pdf", "file_mtime": 1, "file_size": 12,
        "mime_type": "application/pdf", "extension": "pdf",
    })
    fm.check_cache = AsyncMock(return_value={"hit": False, "file_path": None})
    fm.upload_file = AsyncMock(return_value="xyz.pdf")

    result = await _process_one(pdf, fm, dry_run=False)

    assert result["status"] == "uploaded"
    assert result["file_path"] == "xyz.pdf"
    fm.upload_file.assert_called_once()


@pytest.mark.asyncio
async def test_process_one_dry_run(tmp_path: Path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fm = MagicMock()
    fm.build_metadata = MagicMock(return_value={
        "file_name": "test.pdf", "file_mtime": 1, "file_size": 8,
        "mime_type": "application/pdf", "extension": "pdf",
    })
    fm.check_cache = AsyncMock(return_value={"hit": False, "file_path": None})
    fm.upload_file = AsyncMock()

    result = await _process_one(pdf, fm, dry_run=True)

    assert result["status"] == "would_upload"
    assert result["file_path"] is None
    fm.upload_file.assert_not_called()


@pytest.mark.asyncio
async def test_process_one_error(tmp_path: Path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fm = MagicMock()
    fm.build_metadata = MagicMock(return_value={
        "file_name": "test.pdf", "file_mtime": 1, "file_size": 8,
        "mime_type": "application/pdf", "extension": "pdf",
    })
    fm.check_cache = AsyncMock(return_value={"hit": False, "file_path": None})
    fm.upload_file = AsyncMock(side_effect=RuntimeError("boom"))

    result = await _process_one(pdf, fm, dry_run=False)

    assert result["status"].startswith("error:")
    assert "boom" in result["status"]
    assert result["file_path"] is None


import json

from file_manager.scripts.migrate_smb_to_fm import migrate


@pytest.mark.asyncio
async def test_migrate_walks_dir_and_writes_mapping(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "kiwoom-test")  # 실업로드 경로의 전제조건
    src = tmp_path / "smb"
    src.mkdir()
    (src / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (src / "b.pdf").write_bytes(b"%PDF-1.4 b")
    (src / "ignore.txt").write_text("not a pdf")

    out = tmp_path / "mapping.json"

    fm = MagicMock()
    fm.build_metadata = MagicMock(side_effect=lambda **kw: {
        "file_name": kw["file_name"], "file_mtime": kw["file_mtime"],
        "file_size": kw["file_size"], "mime_type": "application/pdf",
        "extension": "pdf",
    })
    fm.check_cache = AsyncMock(return_value={"hit": False, "file_path": None})
    fm.upload_file = AsyncMock(side_effect=["a-uuid.pdf", "b-uuid.pdf"])
    fm.close = AsyncMock()

    monkeypatch.setattr(
        "file_manager.scripts.migrate_smb_to_fm.FileManagerHandler",
        lambda: fm,
    )

    results = await migrate(src, out, dry_run=False)

    assert len(results) == 2
    names = sorted(r["file_name"] for r in results)
    assert names == ["a.pdf", "b.pdf"]

    saved = json.loads(out.read_text())
    assert saved == results
    fm.close.assert_awaited_once()
