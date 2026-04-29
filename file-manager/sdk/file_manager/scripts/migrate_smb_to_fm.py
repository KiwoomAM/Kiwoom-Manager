"""SMB 공유의 기존 PDF를 File Manager Service에 일괄 업로드.

1회성 CLI. 멱등 (재실행 시 cache_hit으로 자동 스킵).
산출물: filename → file_path 매핑 JSON (Phase B 입력 자료).

사용:
    python -m file_manager.scripts.migrate_smb_to_fm \\
        --source-dir /mnt/file_server \\
        --mapping-out /tmp/fm_migration.json \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def migrate(
    source_dir: Path, mapping_out: Path, dry_run: bool = False
) -> list[dict]:
    raise NotImplementedError


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--mapping-out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    asyncio.run(migrate(args.source_dir, args.mapping_out, args.dry_run))


if __name__ == "__main__":
    main()
