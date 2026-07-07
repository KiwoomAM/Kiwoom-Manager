from tools.file_cache_dup_scan import summary


def test_render_zero_dups_shows_ok_badge_and_count():
    md = summary.render("2026-07-06 08:00", "2026-07-07 08:00",
                        new_files=[{"file_name": "a.pdf", "file_size": 10}],
                        dup_groups=[])
    assert "신규 파일: **1건**" in md
    assert "0건" in md and "✅" in md
    assert "a.pdf" in md


def test_render_with_dups_lists_group_and_warn_badge():
    md = summary.render("s", "e",
                        new_files=[],
                        dup_groups=[{"key": {"n": "dup.pdf", "m": 1, "s": 2}, "count": 2}])
    assert "⚠️" in md
    assert "dup.pdf" in md
    assert "× 2" in md
