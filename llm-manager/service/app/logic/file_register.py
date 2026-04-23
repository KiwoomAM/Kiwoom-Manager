"""파일 등록 단계.

에이전트가 보낸 FileInput 리스트를 file-manager에 등록한다.
check_cache로 존재 확인 → 없으면 upload_file. file-manager가 부여한 file_path를
각 FileInput에 심어두면 후단의 _dump_files_meta가 MDB `file_meta`에 함께 저장한다.

설계: docs/file-manager-bytes-passthrough.md
"""

from __future__ import annotations

import logging

from file_manager import FileManagerHandler

from app.core.errors import CacheError, FileUploadError
from app.models.requests import FileInput

logger = logging.getLogger(__name__)


async def register_files(inputs: list[FileInput]) -> list[FileInput]:
    """각 FileInput을 file-manager에 등록. 신규 파일이면 업로드.

    side-effect 전용 — 입력 리스트를 그대로 반환한다. 파이프라인은 이후
    FileInput 그대로 흐른다.

    단계:
      1. hashing 3필드(file_name, file_size, file_mtime)로 check_cache
      2. 없으면 5필드 meta(+mime_type, extension)로 upload_file
    """
    fm = FileManagerHandler()
    for fi in inputs:
        hash_meta = {
            "file_name": fi.file_name,
            "file_size": fi.file_size,
            "file_mtime": fi.file_mtime,
        }
        try:
            result = await fm.check_cache(hash_meta)
        except Exception as e:
            logger.exception(
                "file-manager check_cache 실패: file_name=%s", fi.file_name,
            )
            raise CacheError(
                f"file-manager check_cache 실패: file_name={fi.file_name}, {e}"
            ) from e

        if result["hit"]:
            fi.file_path = result["file_path"]
            continue

        upload_meta = {
            **hash_meta,
            "mime_type": fi.mime_type,
            "extension": fi.extension,
        }
        try:
            fi.file_path = await fm.upload_file(fi.data, upload_meta)
        except Exception as e:
            logger.exception(
                "file-manager upload_file 실패: file_name=%s", fi.file_name,
            )
            raise FileUploadError(
                f"file-manager upload_file 실패: file_name={fi.file_name}, {e}"
            ) from e

    return inputs
