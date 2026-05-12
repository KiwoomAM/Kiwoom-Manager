"""SDK RequestMeta / StoredFile 모델 테스트."""
from __future__ import annotations

import pytest

from llm_request.models import RequestMeta


class TestRequestMetaFileMeta:
    """RequestMeta 가 file_meta 필드를 받고 노출하는지 (Phase 1)."""

    def test_file_meta_default_none(self):
        m = RequestMeta(source="live", cache_hit=False)
        assert m.file_meta is None

    def test_file_meta_accepts_list_of_dicts(self):
        m = RequestMeta(
            source="live", cache_hit=False,
            file_meta=[{"file_path": "abc.pdf", "file_name": "x.pdf"}],
        )
        assert m.file_meta == [{"file_path": "abc.pdf", "file_name": "x.pdf"}]

    def test_extra_keys_silently_ignored(self):
        """v2 기본 extra='ignore' 라 모르는 키는 조용히 폐기 (옛 SDK + 새 서버 호환)."""
        m = RequestMeta(
            source="cache", cache_hit=True,
            file_meta=[{"file_path": "x"}],
            unknown_future_key="future",  # 무시되어야 함
        )
        assert m.cache_hit is True
        assert not hasattr(m, "unknown_future_key")


class TestRequestMetaContextCacheId:
    """RequestMeta 가 context_cache_id 필드를 받고 노출하는지."""

    def test_context_cache_id_default_none(self):
        m = RequestMeta(source="live", cache_hit=False)
        assert m.context_cache_id is None

    def test_context_cache_id_accepts_string(self):
        m = RequestMeta(
            source="live", cache_hit=False,
            context_cache_id="a" * 64,  # SHA-256 hex 형태
        )
        assert m.context_cache_id == "a" * 64


class TestStoredFileSDK:
    """SDK StoredFile — Service 측 정의와 동일 (동기 정책, Phase 2). 6 필드."""

    def test_accepts_6_required_fields(self):
        from llm_request.models import StoredFile

        sf = StoredFile(
            file_path="abc.pdf",
            file_name="x.pdf",
            file_size=1,
            file_mtime=0,
            mime_type="application/pdf",
            extension=".pdf",
        )
        assert sf.file_path == "abc.pdf"

    def test_extra_keys_forbid_raises(self):
        """server-fill 필드 (resolved_via 등) 를 caller 가 실수로 넘기면 raise (의도)."""
        from pydantic import ValidationError

        from llm_request.models import StoredFile

        with pytest.raises(ValidationError):
            StoredFile(
                file_path="abc.pdf",
                file_name="x.pdf",
                file_size=1,
                file_mtime=0,
                mime_type="application/pdf",
                extension=".pdf",
                resolved_via="primary",  # server-fill → forbid
            )
