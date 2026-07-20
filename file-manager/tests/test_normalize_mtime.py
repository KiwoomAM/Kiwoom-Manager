# -*- coding: utf-8 -*-
"""normalize_mtime 테스트 (이슈 #81, C).

Kiwoom-MN의 _to_int_mtime을 SDK로 승격. 다양한 표현의 file_mtime을
file-manager가 요구하는 int(epoch seconds)로 통일한다.

기대 epoch은 매직 넘버 대신 datetime으로 계산해 로케일/TZ에 무관하게 맞춘다.
"""

from datetime import datetime

from file_manager.handler import FileManagerHandler

normalize = FileManagerHandler.normalize_mtime


def test_int_passthrough():
    assert normalize(1700000000) == 1700000000


def test_float_truncated_to_int():
    assert normalize(1700000000.9) == 1700000000


def test_numeric_string():
    assert normalize("1700000000") == 1700000000


def test_numeric_string_with_decimal_truncated():
    # float 문자열도 float 값과 동일하게 절삭 (float/문자열 비대칭 제거)
    assert normalize("1700000000.9") == 1700000000


def test_millisecond_decimal_string_converted():
    # 소수점 밀리초 문자열도 초로 환산
    assert normalize("1700000000000.5") == 1700000000


def test_iso_date_string():
    expected = int(datetime.fromisoformat("2026-05-11").timestamp())
    assert normalize("2026-05-11") == expected


def test_iso_datetime_with_z_suffix():
    expected = int(datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp())
    assert normalize("2026-01-01T00:00:00Z") == expected


def test_epoch_milliseconds_int_converted_to_seconds():
    # 10^12 이상은 밀리초 epoch으로 보고 초로 환산
    assert normalize(1700000000000) == 1700000000


def test_epoch_milliseconds_numeric_string_converted():
    assert normalize("1700000000000") == 1700000000


def test_epoch_seconds_below_threshold_unchanged():
    # 10^12 미만은 이미 초 → 그대로
    assert normalize(1700000000) == 1700000000


def test_epoch_exactly_at_threshold_treated_as_milliseconds():
    # 정확히 10^12는 밀리초로 간주(>=) → 초로 환산
    assert normalize(1_000_000_000_000) == 1_000_000_000


def test_epoch_just_below_threshold_unchanged():
    # 10^12 바로 아래는 그대로 유지
    assert normalize(999_999_999_999) == 999_999_999_999


def test_none_returns_zero():
    assert normalize(None) == 0


def test_blank_string_returns_zero():
    assert normalize("   ") == 0


def test_unparseable_string_returns_zero():
    assert normalize("not-a-date") == 0
