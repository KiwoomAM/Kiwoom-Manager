"""SMB 공유의 기존 PDF를 File Manager Service에 일괄 업로드.

1회성 CLI. 멱등 (재실행 시 cache_hit으로 자동 스킵).
산출물: filename → file_path 매핑 JSON (Phase B 입력 자료).

사용:
    SERVICE_NAME=<등록 주체> python -m file_manager.scripts.migrate_smb_to_fm \\
        --source-dir /mnt/file_server \\
        --mapping-out /tmp/fm_migration.json \\
        [--dry-run]

env ``SERVICE_NAME``은 업로드 시 필수다(``upload_file``이 file_cache 메타에 기록).
compose가 env를 주입해 주는 서비스와 달리 이 CLI는 직접 세워야 하며, 미설정 시
트리를 걷기 전에 ``FMConfigError``로 중단한다. ``--dry-run``은 업로드를 타지 않아
필요 없다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def _process_one(pdf_path: Path, fm, dry_run: bool) -> dict:
    """단일 PDF 처리. cache 확인 후 dry_run/업로드/에러 분기."""
    meta = fm.build_metadata(
        file_name=pdf_path.name,
        file_mtime=int(pdf_path.stat().st_mtime),
        file_size=pdf_path.stat().st_size,
    )
    cache = await fm.check_cache(meta)
    if cache["hit"]:
        return {**meta, "file_path": cache["file_path"], "status": "cache_hit"}

    if dry_run:
        return {**meta, "file_path": None, "status": "would_upload"}

    try:
        file_path = await fm.upload_file(pdf_path.read_bytes(), meta)
        return {**meta, "file_path": file_path, "status": "uploaded"}
    except Exception as e:
        return {**meta, "file_path": None, "status": f"error:{e}"}


try:
    from file_manager import FileManagerHandler  # noqa: E402
    from file_manager.handler import _require_service_name  # noqa: E402
except ImportError:  # pragma: no cover — 테스트 monkeypatch 대상으로만 사용
    FileManagerHandler = None  # type: ignore[assignment,misc]
    _require_service_name = None  # type: ignore[assignment]


async def migrate(
    source_dir: Path, mapping_out: Path, dry_run: bool = False
) -> list[dict]:
    """source_dir의 모든 *.pdf를 FM에 업로드. mapping_out에 결과 JSON 저장."""
    if not source_dir.exists():
        raise FileNotFoundError(f"source_dir 없음: {source_dir}")

    # 업로드 전제조건(env SERVICE_NAME)을 트리를 걷기 전에 한 번 검사한다.
    # _process_one 의 blanket `except Exception` 이 FMConfigError 를 파일 단위
    # "error:" 문자열로 강등하기 때문에, 이 검사가 없으면 설정 하나가 빠졌을 뿐인데
    # 전 항목이 실패한 매핑 JSON 을 종료코드 0 으로 남기고 끝난다.
    # dry_run 은 upload_file 을 타지 않으므로 요구하지 않는다.
    if not dry_run:
        _require_service_name()

    pdfs = sorted(source_dir.rglob("*.pdf"))
    logger.info("대상 PDF: %d건 (%s)", len(pdfs), source_dir)

    fm = FileManagerHandler()
    results: list[dict] = []
    try:
        try:
            from tqdm import tqdm
            iterator = tqdm(pdfs, desc="migrate", unit="pdf")
            verbose = False
        except ImportError:
            iterator = pdfs
            verbose = True

        for pdf in iterator:
            r = await _process_one(pdf, fm, dry_run)
            results.append(r)
            if verbose:
                logger.info("%s → %s", pdf.name, r["status"])

        mapping_out.parent.mkdir(parents=True, exist_ok=True)
        mapping_out.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("매핑 저장: %s (%d건)", mapping_out, len(results))
    finally:
        await fm.close()

    return results


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
