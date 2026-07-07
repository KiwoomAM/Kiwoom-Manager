"""main()의 이슈 dedup 경로 검증 — skip/생성/soft-fail 분기."""
from bson import ObjectId
from tools.file_cache_dup_scan import gh_issue
from tools.file_cache_dup_scan import __main__ as m


def _dup(name):
    return {"key": {"n": name, "m": 1, "s": 2}, "ids": [ObjectId()], "count": 2}


def test_skips_when_fingerprint_already_open():
    dup = _dup("a.pdf")
    fp = gh_issue.fingerprint(dup["key"])
    created = []
    m.process_dups(
        [dup], "slug", "file-cache-dup", "tok",
        open_fps_fn=lambda *a, **k: {fp},
        create_fn=lambda *a, **k: created.append(a) or {"url": "x"},
    )
    assert created == []  # 열린 이슈 있으니 생성 안 함


def test_creates_when_not_open():
    dup = _dup("a.pdf")
    titles = []
    m.process_dups(
        [dup], "slug", "file-cache-dup", "tok",
        open_fps_fn=lambda *a, **k: set(),
        create_fn=lambda slug, title, body, label, token: titles.append(title) or {"url": "x"},
    )
    assert len(titles) == 1
    assert "a.pdf" in titles[0]


def test_soft_fails_on_create_error_and_continues_next():
    d1, d2 = _dup("a.pdf"), _dup("b.pdf")
    seen = []

    def boom(slug, title, body, label, token):
        seen.append(title)
        if "a.pdf" in title:
            raise RuntimeError("api down")
        return {"url": "x"}

    # 예외가 전파되지 않아야 하고(soft-fail), 실패 후에도 b.pdf는 계속 시도돼야 함
    m.process_dups(
        [d1, d2], "slug", "file-cache-dup", "tok",
        open_fps_fn=lambda *a, **k: set(),
        create_fn=boom,
    )
    assert any("a.pdf" in t for t in seen)
    assert any("b.pdf" in t for t in seen)
