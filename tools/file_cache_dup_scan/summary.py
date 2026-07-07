# -*- coding: utf-8 -*-
"""Job Summary 마크다운 렌더 — 매 런 heartbeat + 신규파일 + 중복 요약."""

from __future__ import annotations


def render(window_start_kst: str, window_end_kst: str,
           new_files: list[dict], dup_groups: list[dict]) -> str:
    badge = "✅ 0건" if not dup_groups else f"⚠️ {len(dup_groups)}건"
    lines = [
        "## file_cache 중복 스캔",
        f"- 윈도우: {window_start_kst} ~ {window_end_kst} (KST)",
        f"- 신규 파일: **{len(new_files)}건**",
        f"- 중복: **{badge}**",
    ]
    if dup_groups:
        lines.append("")
        lines.append("### 중복 그룹")
        for g in dup_groups:
            k = g["key"]
            lines.append(f"- `{k.get('n')}` (mtime={k.get('m')}, size={k.get('s')}) × {g['count']}")
    if new_files:
        lines.append("")
        lines.append("<details><summary>신규 파일 목록</summary>")
        lines.append("")
        for m in new_files:
            lines.append(f"- {m.get('file_name')} ({m.get('file_size')} bytes)")
        lines.append("")
        lines.append("</details>")
    return "\n".join(lines)
