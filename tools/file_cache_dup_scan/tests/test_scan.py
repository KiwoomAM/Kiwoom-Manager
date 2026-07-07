from datetime import datetime, timezone, timedelta
from bson import ObjectId
from tools.file_cache_dup_scan import scan


def _oid_at(dt):
    return ObjectId.from_datetime(dt)


def test_window_start_subtracts_hours():
    now = datetime(2026, 7, 7, 23, 0, 0, tzinfo=timezone.utc)
    assert scan.window_start(now, 25) == now - timedelta(hours=25)


def test_id_in_window_true_when_after_start():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    inside = _oid_at(datetime(2026, 7, 7, 0, 0, 0, tzinfo=timezone.utc))
    assert scan.id_in_window(inside, start) is True


def test_id_in_window_false_when_before_start():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    old = _oid_at(datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc))
    assert scan.id_in_window(old, start) is False


def test_reportable_dups_keeps_group_with_member_in_window():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    g = {"key": {"n": "a.pdf", "m": 1, "s": 10},
         "ids": [_oid_at(datetime(2026, 7, 1, tzinfo=timezone.utc)),
                 _oid_at(datetime(2026, 7, 7, tzinfo=timezone.utc))],
         "count": 2}
    assert scan.reportable_dups([g], start) == [g]


def test_reportable_dups_drops_group_fully_out_of_window():
    start = datetime(2026, 7, 6, 22, 0, 0, tzinfo=timezone.utc)
    g = {"key": {"n": "a.pdf", "m": 1, "s": 10},
         "ids": [_oid_at(datetime(2026, 7, 1, tzinfo=timezone.utc)),
                 _oid_at(datetime(2026, 7, 2, tzinfo=timezone.utc))],
         "count": 2}
    assert scan.reportable_dups([g], start) == []
