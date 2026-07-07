# -*- coding: utf-8 -*-
"""file_cache 접근 — mdb-handler(:8300) 경유. 읽기 전용(aggregate + 윈도우 조회).

러너는 Mongo(:27017)에 직접 못 붙으므로, 사내 표준 경로인 mdb-manager SDK(→ mdb-handler)를
쓴다. file-manager 와 동일한 caller("file-manager")로 file_cache 를 읽는다. 쓰기/삭제 없음.
"""

from __future__ import annotations

from datetime import datetime

from mdb_manager import MongoDBManager

from tools.file_cache_dup_scan import scan

CALLER = "file-manager"
CACHE_COLLECTION = "file_cache"

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

# 윈도우 신규 파일 조회 시 최신순으로 넉넉히 훑을 상한(전체 컬렉션을 매번 끌지 않게).
NEW_FILES_LIMIT = 2000


def get_manager(database: str):
    """file_cache 에 caller="file-manager"로 bound된 mdb-manager 뷰 반환.

    base_url 은 env MDB_HANDLER_URL(기본 http://192.7.9.45:8300)에서 읽는다.
    """
    return MongoDBManager(database=database, collection=CACHE_COLLECTION).with_caller(CALLER)


def fetch_dup_groups(mgr) -> list[dict]:
    """중복(count>1) 그룹 전량 조회 → 표준 형태로 매핑. ids 는 hex 문자열 리스트."""
    return [
        {"key": g["_id"], "ids": g["ids"], "count": g["count"]}
        for g in mgr.aggregate(DUP_PIPELINE)
    ]


def fetch_new_files(mgr, start_utc: datetime, limit: int = NEW_FILES_LIMIT) -> list[dict]:
    """윈도우 시작 이후 생성된 doc의 metadata 리스트(신규 파일 요약용).

    최신순(_id desc)으로 limit 건까지 훑고, 윈도우보다 오래된 doc을 만나면 멈춘다.
    metadata 없는 이상 문서는 걸러 스캔이 죽지 않게 한다.
    """
    docs = mgr.find({}, projection={"metadata": 1}, sort=[("_id", -1)], limit=limit)
    out: list[dict] = []
    for d in docs:
        if scan.oid_time(d["_id"]) < start_utc:
            break  # 최신순 정렬이라 이후는 전부 윈도우 밖
        if d.get("metadata"):
            out.append(d["metadata"])
    return out
