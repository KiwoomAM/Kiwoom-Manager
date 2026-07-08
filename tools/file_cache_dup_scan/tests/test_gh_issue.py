import io
import json
from tools.file_cache_dup_scan import gh_issue


def _resp(obj):
    return io.BytesIO(json.dumps(obj).encode())


def test_fingerprint_stable_and_short():
    key = {"n": "a.pdf", "m": 123, "s": 456}
    fp = gh_issue.fingerprint(key)
    assert fp == gh_issue.fingerprint(key)
    assert len(fp) == 8


def test_issue_title_contains_name_and_fingerprint():
    key = {"n": "report.pdf", "m": 1, "s": 2}
    fp = gh_issue.fingerprint(key)
    title = gh_issue.issue_title(key, fp)
    assert "report.pdf" in title
    assert title.endswith(f"({fp})")


def test_open_fingerprints_extracts_from_titles():
    issues = [{"title": "[file-cache-dup] a.pdf (abc12345)"},
              {"title": "[file-cache-dup] b.pdf (def67890)"}]

    def fake_opener(req, timeout=0):
        return _resp(issues)

    fps = gh_issue.open_fingerprints("KiwoomAM/Kiwoom-Manager", "file-cache-dup",
                                     "tok", opener=fake_opener)
    assert fps == {"abc12345", "def67890"}


def test_open_fingerprints_paginates_past_100():
    page1 = [{"title": f"[file-cache-dup] f{i}.pdf (fp{i:06d})"} for i in range(100)]
    page2 = [{"title": "[file-cache-dup] last.pdf (lastfp00)"}]

    def fake_opener(req, timeout=0):
        url = req.full_url
        if "page=2" in url:
            return _resp(page2)
        if "page=3" in url:
            return _resp([])
        return _resp(page1)

    fps = gh_issue.open_fingerprints("KiwoomAM/Kiwoom-Manager", "file-cache-dup",
                                     "tok", opener=fake_opener)
    assert "lastfp00" in fps
    assert len(fps) == 101


def test_create_issue_posts_and_returns_number_url():
    captured = {}

    def fake_opener(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return _resp({"number": 42, "html_url": "http://x/42"})

    out = gh_issue.create_issue("KiwoomAM/Kiwoom-Manager", "t", "b",
                                "file-cache-dup", "tok", opener=fake_opener)
    assert out == {"number": 42, "url": "http://x/42"}
    assert captured["url"].endswith("/repos/KiwoomAM/Kiwoom-Manager/issues")
    assert captured["body"]["labels"] == ["file-cache-dup"]
