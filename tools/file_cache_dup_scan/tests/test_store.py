from datetime import datetime, timezone
from tools.file_cache_dup_scan import store


def _hex_at(dt):
    return format(int(dt.timestamp()), "08x") + "0" * 16


class FakeManager:
    """mdb-manager bound view 흉내 — aggregate/find 만."""
    def __init__(self, groups=None, docs=None):
        self._groups = groups or []
        self._docs = docs or []  # 테스트는 최신순으로 정렬해 넣는다

    def aggregate(self, pipeline):
        return list(self._groups)

    def find(self, filter=None, projection=None, sort=None, limit=None):
        docs = self._docs
        return docs[:limit] if limit else docs


def test_fetch_dup_groups_maps_aggregate_output():
    raw = [{"_id": {"n": "a.pdf", "m": 1, "s": 10},
            "ids": ["69f0610700000000000000aa", "69f0610700000000000000bb"], "count": 2}]
    out = store.fetch_dup_groups(FakeManager(groups=raw))
    assert out == [{"key": {"n": "a.pdf", "m": 1, "s": 10},
                    "ids": raw[0]["ids"], "count": 2}]


def test_fetch_new_files_stops_at_first_out_of_window():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    docs = [  # 최신순
        {"_id": _hex_at(datetime(2026, 7, 7, tzinfo=timezone.utc)),
         "metadata": {"file_name": "new.pdf"}},
        {"_id": _hex_at(datetime(2026, 7, 1, tzinfo=timezone.utc)),
         "metadata": {"file_name": "old.pdf"}},  # 윈도우 밖 → 여기서 멈춤
    ]
    out = store.fetch_new_files(FakeManager(docs=docs), start)
    assert out == [{"file_name": "new.pdf"}]


def test_fetch_new_files_skips_docs_without_metadata():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    docs = [  # 둘 다 윈도우 내, 첫 doc 은 metadata 없음(이상 문서)
        {"_id": _hex_at(datetime(2026, 7, 7, 10, tzinfo=timezone.utc))},
        {"_id": _hex_at(datetime(2026, 7, 7, 9, tzinfo=timezone.utc)),
         "metadata": {"file_name": "ok.pdf"}},
    ]
    out = store.fetch_new_files(FakeManager(docs=docs), start)
    assert out == [{"file_name": "ok.pdf"}]  # 죽지 않고 정상 문서만
