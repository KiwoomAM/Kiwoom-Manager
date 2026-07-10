"""stored_file_resolver.py 단위 테스트 — Phase 3.

Phase 3 변경:
- resolve_stored_files: lazy — fetch 안 함, placeholder + validators 만
- fetch_with_fallback (신설 헬퍼): 4·5단계 (resolver.py) 가 호출. get_file → 404 시
  check_cache 로 fallback 처리.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_manager.exceptions import FileResolveError
from llm_manager.models import FileInput, StoredFile


@pytest.fixture()
def mock_fm(monkeypatch):
    """FileManagerHandler() 호출 시 동일 mock 인스턴스 반환 (싱글톤 흉내)."""
    fm_instance = MagicMock()
    fm_instance.get_file = AsyncMock()
    fm_instance.check_cache = AsyncMock()

    fm_class = MagicMock(return_value=fm_instance)
    monkeypatch.setattr("llm_manager._internal.stored_file_resolver.FileManagerHandler", fm_class)
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


def _fi(file_path: str = "abc.pdf", **overrides) -> FileInput:
    """fetch_with_fallback 테스트용 placeholder FileInput."""
    base = {
        "file_path": file_path,
        "file_name": "x.pdf",
        "file_size": 100,
        "file_mtime": 1700000000,
        "mime_type": "application/pdf",
        "extension": ".pdf",
        "data": b"",
        "resolved_via": "primary",
    }
    base.update(overrides)
    return FileInput(**base)


class TestResolveStoredFilesLazy:
    """Phase 3: resolve_stored_files 가 fetch 안 함 (lazy)."""

    async def test_no_fetch_during_resolve(self, mock_fm):
        """fm.get_file / fm.check_cache 가 호출되지 않음."""
        from llm_manager._internal.stored_file_resolver import resolve_stored_files

        result = await resolve_stored_files([_ref("abc.pdf")])

        assert len(result) == 1
        fi = result[0]
        assert fi.data == b""              # placeholder
        assert fi.file_path == "abc.pdf"
        assert fi.file_name == "x.pdf"
        assert fi.mime_type == "application/pdf"
        assert fi.resolved_via is None     # placeholder 단계 — fetch 검증 전
        mock_fm.get_file.assert_not_awaited()
        mock_fm.check_cache.assert_not_awaited()

    async def test_validators_called_before_lazy(self, mock_fm):
        """validators 가 file-manager 호출 전 (fail-fast) 동작."""
        from llm_manager._internal.stored_file_resolver import resolve_stored_files
        from llm_manager.exceptions import UnsupportedFileTypeError

        # mime_type 화이트리스트 외
        bad_ref = StoredFile(
            file_path="bad.zip",
            file_name="x.zip", file_size=1, file_mtime=0,
            mime_type="application/zip",  # SUPPORTED_MIME_TYPES 외
            extension=".zip",
        )

        with pytest.raises(UnsupportedFileTypeError):
            await resolve_stored_files([bad_ref])

        # validators 가 file-manager 호출 전에 raise
        mock_fm.get_file.assert_not_awaited()

    async def test_alias_mime_normalized_before_validation(self, mock_fm):
        """정규화가 검증보다 먼저 일어난다 — 거짓 거부의 해소.

        file-manager 의 build_metadata 는 mimetypes.guess_type 을 쓰므로 `.mp3` 를
        `audio/mpeg` 로 준다. 화이트리스트는 정규화된 `audio/mp3` 만 알고 있다.
        """
        from llm_manager._internal.stored_file_resolver import resolve_stored_files

        ref = _ref("a.mp3", file_name="a.mp3", mime_type="audio/mpeg", extension=".mp3")

        result = await resolve_stored_files([ref])

        assert result[0].mime_type == "audio/mp3"
        mock_fm.get_file.assert_not_awaited()

    async def test_m4a_accepted(self, mock_fm):
        """audio/mp4 는 Gemini 가 실제로 받아주므로 거부하지 않는다."""
        from llm_manager._internal.stored_file_resolver import resolve_stored_files

        ref = _ref("a.m4a", file_name="a.m4a", mime_type="audio/mp4", extension=".m4a")

        result = await resolve_stored_files([ref])

        assert result[0].mime_type == "audio/mp4"


class TestFetchWithFallbackPrimary:
    """fetch_with_fallback: get_file 1차 성공 → bytes 반환, fi 변경 없음."""

    async def test_primary_success_returns_bytes(self, mock_fm):
        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.return_value = (b"\x00\x01\x02", {})
        fi = _fi("abc.pdf")

        data = await fetch_with_fallback(mock_fm, fi)

        assert data == b"\x00\x01\x02"
        assert fi.file_path == "abc.pdf"            # 변경 없음
        assert fi.resolved_via == "primary"          # 변경 없음
        mock_fm.check_cache.assert_not_awaited()


class TestFetchWithFallbackFallback:
    """fetch_with_fallback: 404 → check_cache hit → 새 path 재시도 → fi 갱신."""

    async def test_stale_path_recovers(self, mock_fm, caplog):
        from file_manager.exceptions import FMServiceError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        new_path = "new-uuid.pdf"
        mock_fm.get_file.side_effect = [
            FMServiceError("not found", status_code=404),
            (b"\x10\x20", {}),
        ]
        mock_fm.check_cache.return_value = {"hit": True, "file_path": new_path}

        fi = _fi("stale.pdf")
        with caplog.at_level("WARNING"):
            data = await fetch_with_fallback(mock_fm, fi)

        assert data == b"\x10\x20"
        assert fi.file_path == new_path                # 갱신됨
        assert fi.resolved_via == "fallback"           # 갱신됨
        assert any("stale file_path" in rec.message for rec in caplog.records)
        assert mock_fm.get_file.await_count == 2
        mock_fm.check_cache.assert_awaited_once()


class TestFetchWithFallbackBothMiss:
    """1차 404 + check_cache miss → FileResolveError raise."""

    async def test_raises_file_resolve_error(self, mock_fm):
        from file_manager.exceptions import FMServiceError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = FMServiceError("not found", status_code=404)
        mock_fm.check_cache.return_value = {"hit": False, "file_path": None}

        fi = _fi("ghost.pdf")
        with pytest.raises(FileResolveError, match="not found"):
            await fetch_with_fallback(mock_fm, fi)


class TestFetchWithFallbackNon404:
    """비-404 (5xx / network) 는 그대로 전파."""

    async def test_500_propagates(self, mock_fm):
        from file_manager.exceptions import FMServiceError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = FMServiceError("server error", status_code=500)

        fi = _fi("abc.pdf")
        with pytest.raises(FMServiceError, match="server error"):
            await fetch_with_fallback(mock_fm, fi)

        mock_fm.check_cache.assert_not_awaited()


class TestFetchWithFallbackRace:
    """C4 회귀 보호 — check_cache hit 후 새 path 도 fetch 실패 시 FileResolveError 로 통일."""

    async def test_second_get_file_404_raises_file_resolve_error(self, mock_fm):
        """1차 404 → check_cache hit → 2차도 404 (race) → FileResolveError."""
        from file_manager.exceptions import FMServiceError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = [
            FMServiceError("not found", status_code=404),
            FMServiceError("race delete", status_code=404),
        ]
        mock_fm.check_cache.return_value = {"hit": True, "file_path": "new.pdf"}

        fi = _fi("stale.pdf")
        with pytest.raises(FileResolveError, match="also unfetchable"):
            await fetch_with_fallback(mock_fm, fi)

        assert mock_fm.get_file.await_count == 2

    async def test_second_get_file_500_raises_file_resolve_error(self, mock_fm):
        """1차 404 → check_cache hit → 2차 5xx → 동일하게 FileResolveError 로 wrap."""
        from file_manager.exceptions import FMServiceError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = [
            FMServiceError("not found", status_code=404),
            FMServiceError("internal", status_code=500),
        ]
        mock_fm.check_cache.return_value = {"hit": True, "file_path": "new.pdf"}

        fi = _fi("stale.pdf")
        with pytest.raises(FileResolveError, match="also unfetchable"):
            await fetch_with_fallback(mock_fm, fi)


class TestFetchWithFallbackMetadataNotFound:
    """file-manager mdb-orchestration 회귀 — stale path 는 FMServiceError 404 가 아니라
    MetadataNotFoundError 로 온다. 이것도 not-found 로 보고 check_cache fallback 을 타야 함."""

    async def test_metadata_not_found_recovers(self, mock_fm, caplog):
        """1차 MetadataNotFoundError → check_cache hit → 새 path 재시도 → fi 갱신."""
        from file_manager.exceptions import MetadataNotFoundError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        new_path = "new-uuid.pdf"
        mock_fm.get_file.side_effect = [
            MetadataNotFoundError("stale.pdf"),
            (b"\x10\x20", {}),
        ]
        mock_fm.check_cache.return_value = {"hit": True, "file_path": new_path}

        fi = _fi("stale.pdf")
        with caplog.at_level("WARNING"):
            data = await fetch_with_fallback(mock_fm, fi)

        assert data == b"\x10\x20"
        assert fi.file_path == new_path
        assert fi.resolved_via == "fallback"
        assert mock_fm.get_file.await_count == 2
        mock_fm.check_cache.assert_awaited_once()

    async def test_metadata_not_found_then_check_cache_miss_raises(self, mock_fm):
        """1차 MetadataNotFoundError + check_cache miss → FileResolveError."""
        from file_manager.exceptions import MetadataNotFoundError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = MetadataNotFoundError("ghost.pdf")
        mock_fm.check_cache.return_value = {"hit": False, "file_path": None}

        fi = _fi("ghost.pdf")
        with pytest.raises(FileResolveError, match="not found"):
            await fetch_with_fallback(mock_fm, fi)

    async def test_second_get_file_metadata_not_found_raises(self, mock_fm):
        """1차 not-found → check_cache hit → 2차 MetadataNotFoundError (race) → FileResolveError."""
        from file_manager.exceptions import MetadataNotFoundError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = [
            MetadataNotFoundError("stale.pdf"),
            MetadataNotFoundError("new.pdf"),
        ]
        mock_fm.check_cache.return_value = {"hit": True, "file_path": "new.pdf"}

        fi = _fi("stale.pdf")
        with pytest.raises(FileResolveError, match="also unfetchable"):
            await fetch_with_fallback(mock_fm, fi)

        assert mock_fm.get_file.await_count == 2


class TestFetchWithFallbackInfraError:
    """MDBConnectionError(503) 등 인프라 장애는 not-found 가 아니므로 그대로 전파 (fallback 안 탐)."""

    async def test_mdb_connection_error_propagates(self, mock_fm):
        from file_manager.exceptions import MDBConnectionError

        from llm_manager._internal.stored_file_resolver import fetch_with_fallback

        mock_fm.get_file.side_effect = MDBConnectionError("mdb down")

        fi = _fi("abc.pdf")
        with pytest.raises(MDBConnectionError, match="mdb down"):
            await fetch_with_fallback(mock_fm, fi)

        mock_fm.check_cache.assert_not_awaited()
