"""SDK RequestMeta 모델 테스트."""
from __future__ import annotations

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
