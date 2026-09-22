#!/usr/bin/env python3
"""`VDBManager` 의 바로가기 메서드를 원본 시그니처에서 만들어 `handler.py` 에 써 넣는다.

사람이 관리하는 것은 `vdb_manager/_shortcuts.py` 의 표뿐이다. 이 스크립트가 원본의
시그니처를 그대로 읽어 진짜 `def` 를 만들어 내므로, 원본에 인자가 늘어도 두 벌을 손으로
맞출 일이 없다. 진짜 메서드라 IDE 자동완성과 타입 검사는 손으로 적던 때와 같다
(`docs/adr/0003`).

실행:
    uv run python scripts/generate_shortcuts.py          # handler.py 를 갱신한다
    uv run python scripts/generate_shortcuts.py --check   # 갱신이 필요하면 1 로 끝난다

`--check` 는 테스트가 쓴다. 표나 원본을 고치고 생성을 안 돌리면 그 자리에서 드러난다.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdb_manager._shortcuts import ALIASES, SHORTCUTS  # noqa: E402  (위 경로 주입 뒤라야 한다)

HANDLER = Path(__file__).resolve().parent.parent / "vdb_manager" / "handler.py"
BEGIN = "    # >>> 여기부터 생성된 바로가기 — scripts/generate_shortcuts.py 가 쓴다. 손으로 고치지 말 것"
END = "    # <<< 생성된 바로가기 끝"

_SUB_CLIENT_TYPES = {
    "collection": "CollectionClient",
    "dense": "DenseClient",
    "sparse": "SparseClient",
    "hybrid": "HybridClient",
    "reranker": "RerankerClient",
}


def _original(sub: str, target: str) -> Any:
    module = __import__(f"vdb_manager.{sub}", fromlist=["*"])
    return getattr(getattr(module, _SUB_CLIENT_TYPES[sub]), target)


def _repr(value: Any) -> str:
    """기본값을 이 레포의 따옴표 관례(큰따옴표)로 적는다."""
    if isinstance(value, str):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return repr(value)


def _render_parameters(signature: inspect.Signature) -> tuple[list[str], list[str]]:
    """(선언에 쓸 줄들, 호출에 쓸 줄들) 을 만든다."""
    declared: list[str] = []
    forwarded: list[str] = []
    seen_keyword_only = False
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise SystemExit(
                f"`*args`/`**kwargs` 를 쓰는 원본은 미러링할 수 없다: {parameter.name}. "
                "인자를 펼치거나 _shortcuts.NOT_MIRRORED 에 이유와 함께 등록할 것"
            )
        if parameter.kind is parameter.KEYWORD_ONLY and not seen_keyword_only:
            declared.append("        *,")
            seen_keyword_only = True
        piece = parameter.name
        if parameter.annotation is not parameter.empty:
            piece += f": {parameter.annotation}"
        if parameter.default is not parameter.empty:
            rendered = _repr(parameter.default)
            piece += f" = {rendered}" if parameter.annotation is not parameter.empty else f"={rendered}"
        declared.append(f"        {piece},")
        forwarded.append(f"            {parameter.name}={parameter.name},")
    return declared, forwarded


def _render(name: str, sub: str, target: str) -> str:
    original = _original(sub, target)
    signature = inspect.signature(original)
    declared, forwarded = _render_parameters(signature)
    returns = signature.return_annotation
    suffix = "" if returns is inspect.Signature.empty else f" -> {returns}"

    if declared:
        lines = [f"    def {name}(", "        self,"]
        lines.extend(declared)
        lines.append(f"    ){suffix}:")
    else:
        lines = [f"    def {name}(self){suffix}:"]
    lines.append(f'        """`{_SUB_CLIENT_TYPES[sub]}.{target}` 바로가기. 설명은 원본 docstring 에 있다."""')
    if forwarded:
        lines.append(f"        return self.{sub}.{target}(")
        lines.extend(forwarded)
        lines.append("        )")
    else:
        lines.append(f"        return self.{sub}.{target}()")
    return "\n".join(lines)


def build_block() -> str:
    parts = [BEGIN, ""]
    for name, sub, target in SHORTCUTS:
        parts.append(_render(name, sub, target))
        parts.append("")
    for alias, canonical in ALIASES:
        parts.append(f"    {alias} = {canonical}")
    parts.append("")
    parts.append(END)
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="갱신이 필요한지만 본다")
    args = parser.parse_args()

    source = HANDLER.read_text(encoding="utf-8")
    if BEGIN not in source or END not in source:
        raise SystemExit(f"{HANDLER} 에 생성 구간 표시가 없음 — 표시를 먼저 넣을 것")

    before, rest = source.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    updated = before + build_block() + after

    if updated == source:
        print("생성된 바로가기가 최신이다")
        return 0
    if args.check:
        print("생성된 바로가기가 낡았다 — `uv run python scripts/generate_shortcuts.py` 를 돌릴 것")
        return 1
    HANDLER.write_text(updated, encoding="utf-8")
    print(f"{len(SHORTCUTS)}개 바로가기와 {len(ALIASES)}개 별칭을 다시 썼다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
