# -*- coding: utf-8 -*-
"""file_cache 중복 탐지 — 순수 로직(I/O 없음). 윈도우 필터 + dup 그룹 선별."""

from __future__ import annotations

from datetime import datetime, timedelta

from bson import ObjectId


def window_start(now_utc: datetime, lookback_hours: float) -> datetime:
    """스캔 윈도우 시작(UTC) = now - lookback_hours."""
    return now_utc - timedelta(hours=lookback_hours)


def id_in_window(oid: ObjectId, start_utc: datetime) -> bool:
    """ObjectId 생성시각이 윈도우 시작 이후인가. (generation_time은 tz-aware UTC)"""
    return oid.generation_time >= start_utc


def reportable_dups(groups: list[dict], start_utc: datetime) -> list[dict]:
    """count>1 그룹 중 윈도우 내 member가 하나 이상인 것만 반환.

    groups 각 항목: {"key": {"n","m","s"}, "ids": [ObjectId, ...], "count": int}
    """
    return [
        g for g in groups
        if any(id_in_window(i, start_utc) for i in g["ids"])
    ]
