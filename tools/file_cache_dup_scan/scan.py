# -*- coding: utf-8 -*-
"""file_cache 중복 탐지 — 순수 로직(I/O 없음). 윈도우 필터 + dup 그룹 선별.

_id 는 mdb-handler 를 거쳐 hex 문자열로 오므로, ObjectId 앞 4바이트(=유닉스 초)를
직접 파싱해 생성시각을 복원한다(bson/pymongo 의존 없음).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def window_start(now_utc: datetime, lookback_hours: float) -> datetime:
    """스캔 윈도우 시작(UTC) = now - lookback_hours."""
    return now_utc - timedelta(hours=lookback_hours)


def oid_time(oid_hex: str) -> datetime:
    """ObjectId hex 앞 4바이트(8자) = 유닉스 초 → tz-aware UTC 시각."""
    return datetime.fromtimestamp(int(oid_hex[:8], 16), tz=timezone.utc)


def id_in_window(oid_hex: str, start_utc: datetime) -> bool:
    """_id(hex) 생성시각이 윈도우 시작 이후인가."""
    return oid_time(oid_hex) >= start_utc


def reportable_dups(groups: list[dict], start_utc: datetime) -> list[dict]:
    """count>1 그룹 중 윈도우 내 member가 하나 이상인 것만 반환.

    groups 각 항목: {"key": {"n","m","s"}, "ids": [hex, ...], "count": int}
    """
    return [
        g for g in groups
        if any(id_in_window(i, start_utc) for i in g["ids"])
    ]
