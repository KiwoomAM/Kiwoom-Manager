# -*- coding: utf-8 -*-
"""file_cache 중복 스캐너 엔트리포인트.

매 런: Job Summary(heartbeat+신규파일+중복건수) 기록.
중복>0: 지문 dedup 후 열린 이슈 없는 것만 GitHub 이슈 생성.
자동 삭제 없음(읽기 전용). Mongo/치명 오류는 전파 → 워크플로 fail.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

from tools.file_cache_dup_scan import gh_issue, scan, store, summary

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("file_cache_dup_scan")

KST = timezone(timedelta(hours=9))
CACHE_COLLECTION = "file_cache"


def _kst(dt_utc: datetime) -> str:
    return dt_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def _write_summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    else:
        print(md)


def _issue_body(g: dict) -> str:
    k = g["key"]
    ids = "\n".join(
        f"  - `{i}` (생성 {_kst(i.generation_time)} KST)" for i in g["ids"]
    )
    return (f"file_cache 중복 저장 감지.\n\n"
            f"- file_name: `{k.get('n')}`\n"
            f"- file_mtime: `{k.get('m')}`\n"
            f"- file_size: `{k.get('s')}`\n"
            f"- 건수: **{g['count']}**\n\n"
            f"doc _id 목록:\n{ids}\n")


def process_dups(dups: list[dict], slug: str, label: str, token: str, *,
                 open_fps_fn=gh_issue.open_fingerprints,
                 create_fn=gh_issue.create_issue) -> None:
    """지문 dedup 후 열린 이슈 없는 것만 생성.

    - 이미 열린 이슈(같은 지문) → skip.
    - 이슈 생성 실패는 soft-fail(로그만, 런 유지) — GitHub API 한 번 삐끗이 매일 감시를
      빨강으로 만들지 않게. 한 건 실패해도 나머지 dup은 계속 처리한다.
    """
    open_fps = open_fps_fn(slug, label, token)
    for g in dups:
        fp = gh_issue.fingerprint(g["key"])
        if fp in open_fps:
            logger.info("열린 이슈 존재(skip): %s", fp)
            continue
        try:
            created = create_fn(slug, gh_issue.issue_title(g["key"], fp),
                                _issue_body(g), label, token)
            logger.info("이슈 생성: %s", created.get("url"))
        except Exception:
            logger.exception("이슈 생성 실패 — 알림 누락(런 유지): %s", fp)


def main() -> int:
    lookback = float(os.environ.get("LOOKBACK_HOURS", "25"))
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://192.7.9.45:27017")
    mongo_db = os.environ.get("MONGO_DB", "kw_ai")
    label = os.environ.get("DUP_ISSUE_LABEL", "file-cache-dup")
    token = os.environ.get("GITHUB_TOKEN")
    slug = os.environ.get("GITHUB_REPO", "KiwoomAM/Kiwoom-Manager")

    now_utc = datetime.now(timezone.utc)
    start = scan.window_start(now_utc, lookback)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    col = client[mongo_db][CACHE_COLLECTION]

    groups = store.fetch_dup_groups(col)
    dups = scan.reportable_dups(groups, start)
    new_files = store.fetch_new_files(col, start)
    logger.info("윈도우 신규 %d건, 중복 그룹 %d건", len(new_files), len(dups))

    md = summary.render(_kst(start), _kst(now_utc), new_files, dups)
    _write_summary(md)

    if dups:
        if not token:
            logger.warning("GITHUB_TOKEN 없음 — 이슈 생성 건너뜀(Job Summary는 기록됨)")
        else:
            process_dups(dups, slug, label, token)

    return 0


if __name__ == "__main__":
    sys.exit(main())
