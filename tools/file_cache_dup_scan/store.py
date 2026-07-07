# -*- coding: utf-8 -*-
"""file_cache Mongo 접근 — 읽기 전용(aggregate + 윈도우 조회). 쓰기/삭제 없음."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId

# count>1 인 (file_name, file_mtime, file_size) 그룹만 뽑는다.
DUP_PIPELINE = [
    {"$group": {
        "_id": {"n": "$metadata.file_name",
                "m": "$metadata.file_mtime",
                "s": "$metadata.file_size"},
        "ids": {"$push": "$_id"},
        "count": {"$sum": 1},
    }},
    {"$match": {"count": {"$gt": 1}}},
]


def fetch_dup_groups(collection) -> list[dict]:
    """중복(count>1) 그룹 전량 조회 → 표준 형태로 매핑."""
    return [
        {"key": g["_id"], "ids": g["ids"], "count": g["count"]}
        for g in collection.aggregate(DUP_PIPELINE, allowDiskUse=True)
    ]


def fetch_new_files(collection, start_utc: datetime) -> list[dict]:
    """윈도우 시작 이후 생성된 doc의 metadata 리스트(신규 파일 요약용)."""
    floor = ObjectId.from_datetime(start_utc)
    cursor = collection.find({"_id": {"$gte": floor}}, {"metadata": 1})
    # metadata 없는 이상 문서 한 건이 스캔 전체를 죽이지 않게 방어(읽기 전용 감시).
    # 아래 summary.render 도 dict 를 전제하므로 여기서 걸러 함께 지킨다.
    return [d["metadata"] for d in cursor if d.get("metadata")]
