from datetime import datetime, timezone
from bson import ObjectId
from tools.file_cache_dup_scan import store


class FakeCollection:
    """aggregate/find를 흉내내는 최소 페이크."""
    def __init__(self, groups=None, docs=None):
        self._groups = groups or []
        self._docs = docs or []
        self.last_find_filter = None

    def aggregate(self, pipeline, allowDiskUse=False):
        return iter(self._groups)

    def find(self, filt, projection=None):
        self.last_find_filter = filt
        floor = filt["_id"]["$gte"]
        return iter([d for d in self._docs if d["_id"] >= floor])


def test_fetch_dup_groups_maps_aggregate_output():
    raw = [{"_id": {"n": "a.pdf", "m": 1, "s": 10},
            "ids": [ObjectId(), ObjectId()], "count": 2}]
    out = store.fetch_dup_groups(FakeCollection(groups=raw))
    assert out == [{"key": {"n": "a.pdf", "m": 1, "s": 10},
                    "ids": raw[0]["ids"], "count": 2}]


def test_fetch_new_files_filters_by_window_and_returns_metadata():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    new = ObjectId.from_datetime(datetime(2026, 7, 7, tzinfo=timezone.utc))
    old = ObjectId.from_datetime(datetime(2026, 7, 1, tzinfo=timezone.utc))
    docs = [{"_id": new, "metadata": {"file_name": "new.pdf"}},
            {"_id": old, "metadata": {"file_name": "old.pdf"}}]
    out = store.fetch_new_files(FakeCollection(docs=docs), start)
    assert out == [{"file_name": "new.pdf"}]
