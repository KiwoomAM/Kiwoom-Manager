# -*- coding: utf-8 -*-
"""GitHub 이슈 dedup + 생성 — stdlib urllib만 사용(외부 의존 없음).

크롤러 diagnostics.create_issue 패턴 재사용: token은 env GITHUB_TOKEN,
REST로 POST. 열린 이슈 제목의 (지문)으로 중복 이슈 생성을 막는다.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

_GH_ACCEPT = "application/vnd.github+json"


def fingerprint(key: dict) -> str:
    """dup 키 → 제목에 박을 8자 지문."""
    raw = f"{key.get('n')}|{key.get('m')}|{key.get('s')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def issue_title(key: dict, fp: str) -> str:
    name = key.get("n") or "(no name)"
    return f"[file-cache-dup] {name} ({fp})"


def open_fingerprints(slug: str, label: str, token: str, opener=urlopen) -> set[str]:
    """열린 file-cache-dup 이슈 제목에서 (지문) 집합 추출. 100개 초과 시 페이지네이션."""
    fps: set[str] = set()
    page = 1
    while True:
        url = (f"https://api.github.com/repos/{slug}/issues"
               f"?state=open&labels={quote(label)}&per_page=100&page={page}")
        req = Request(url, headers={"Authorization": f"token {token}", "Accept": _GH_ACCEPT})
        issues = json.loads(opener(req, timeout=30).read())
        if not issues:
            break
        for it in issues:
            title = it.get("title", "")
            if title.endswith(")") and "(" in title:
                fps.add(title[title.rfind("(") + 1:-1])
        if len(issues) < 100:
            break
        page += 1
    return fps


def create_issue(slug: str, title: str, body: str, label: str,
                 token: str, opener=urlopen) -> dict:
    url = f"https://api.github.com/repos/{slug}/issues"
    payload = json.dumps({"title": title, "body": body, "labels": [label]}).encode()
    req = Request(url, data=payload, method="POST",
                  headers={"Authorization": f"token {token}",
                           "Accept": _GH_ACCEPT,
                           "Content-Type": "application/json"})
    resp = json.loads(opener(req, timeout=30).read())
    return {"number": resp.get("number"), "url": resp.get("html_url")}
