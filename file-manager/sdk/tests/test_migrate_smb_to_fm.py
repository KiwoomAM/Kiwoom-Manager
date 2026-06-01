"""migrate_smb_to_fm 단위 테스트."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from file_manager.scripts.migrate_smb_to_fm import _process_one


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
