"""file_resolver.py 단위 테스트 — Phase 2.

resolve_stored_files: stored_files 를 file-manager 에서 fetch → FileInput 변환.

FileManagerHandler 는 외부 (file-manager SDK) 클래스이므로 monkeypatch 로 모킹.
패턴은 test_file_register.py 와 동일.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import FileResolveError
from app.models.requests import StoredFile


@pytest.fixture()
def mock_fm(monkeypatch):
    """FileManagerHandler() 호출 시 동일 mock 인스턴스 반환 (싱글톤 흉내)."""
    fm_instance = MagicMock()
    fm_instance.get_file = AsyncMock()
    fm_instance.check_cache = AsyncMock()

    fm_class = MagicMock(return_value=fm_instance)
    monkeypatch.setattr("app.logic.file_resolver.FileManagerHandler", fm_class)
    return fm_instance


def _ref(file_path: str = "abc.pdf", **overrides) -> StoredFile:
    base = {
        "file_path": file_path,
        "file_name": "x.pdf",
        "file_size": 100,
        "file_mtime": 1700000000,
        "mime_type": "application/pdf",
        "extension": ".pdf",
    }
    base.update(overrides)
    return StoredFile(**base)


# 서버는 caller 의 StoredFile 값을 그대로 사용 — fm.get_file 의 metadata 는 무시.
# 따라서 mock 의 get_file 두 번째 반환 (metadata) 는 실제 사용 안 됨. 빈 dict 로 충분.
_FM_META_UNUSED: dict = {}


class TestResolveStoredFilesPrimary:
    """1차 get_file 성공 → resolved_via='primary'."""

    async def test_single_ref_primary_success(self, mock_fm):
        from app.logic.file_resolver import resolve_stored_files

        mock_fm.get_file.return_value = (b"\x00\x01\x02", _FM_META_UNUSED)

        result = await resolve_stored_files([_ref("abc.pdf")])

        assert len(result) == 1
        fi = result[0]
        assert fi.data == b"\x00\x01\x02"
        assert fi.file_path == "abc.pdf"      # caller 값 그대로
        assert fi.mime_type == "application/pdf"  # caller 값 그대로
        assert fi.extension == ".pdf"
        assert fi.resolved_via == "primary"
        mock_fm.check_cache.assert_not_awaited()  # fallback 발동 X


class TestResolveStoredFilesFallback:
    """1차 404 → check_cache hit → 2차 get_file 성공 → resolved_via='fallback'."""

    async def test_stale_path_recovers_via_check_cache(self, mock_fm, caplog):
        from file_manager.exceptions import FMServiceError

        from app.logic.file_resolver import resolve_stored_files

        # 1차 get_file: 404 / 2차 get_file (new_path): 성공
        new_path = "new-uuid.pdf"
        mock_fm.get_file.side_effect = [
            FMServiceError("not found", status_code=404),
            (b"\x10\x20", _FM_META_UNUSED),
        ]
        mock_fm.check_cache.return_value = {"hit": True, "file_path": new_path}

        with caplog.at_level("WARNING"):
            result = await resolve_stored_files([_ref("stale.pdf")])

        assert len(result) == 1
        fi = result[0]
        assert fi.file_path == new_path  # 새 path 로 갱신
        assert fi.file_name == "x.pdf"   # 다른 5 필드는 caller 값 유지
        assert fi.mime_type == "application/pdf"
        assert fi.resolved_via == "fallback"
        assert any("stale file_path" in rec.message for rec in caplog.records)
        assert mock_fm.get_file.await_count == 2
        mock_fm.check_cache.assert_awaited_once()


class TestResolveStoredFilesBothMiss:
    """1차 404 + check_cache miss → FileResolveError raise."""

    async def test_raises_file_resolve_error(self, mock_fm):
        from file_manager.exceptions import FMServiceError

        from app.logic.file_resolver import resolve_stored_files

        mock_fm.get_file.side_effect = FMServiceError("not found", status_code=404)
        mock_fm.check_cache.return_value = {"hit": False, "file_path": None}

        with pytest.raises(FileResolveError, match="not found"):
            await resolve_stored_files([_ref("ghost.pdf")])


class TestResolveStoredFilesNon404Error:
    """비-404 에러 (5xx / network) 는 그대로 전파."""

    async def test_500_propagates_unchanged(self, mock_fm):
        from file_manager.exceptions import FMServiceError

        from app.logic.file_resolver import resolve_stored_files

        mock_fm.get_file.side_effect = FMServiceError("server error", status_code=500)

        with pytest.raises(FMServiceError, match="server error"):
            await resolve_stored_files([_ref("abc.pdf")])

        mock_fm.check_cache.assert_not_awaited()  # fallback 발동 X
