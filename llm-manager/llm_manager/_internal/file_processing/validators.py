"""파일 메타 검증 — mime 화이트리스트 + 2GB 초과.

runner의 llm_request_async / llm_request_stream_async 진입 직후 fail-fast로
잘못된 입력을 차단한다 (prepare·MDB 조회 이전).

상세 설계: docs/inline-vs-files-api-strategy.md §2.4, §4.5, §4.6, §7.7
"""

from __future__ import annotations

from llm_manager.exceptions import FileTooLargeError, UnsupportedFileTypeError
from llm_manager.models import FileInput

# ── mime 화이트리스트 ────────────────────────────────────────

SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    # 이미지 (공식 확정)
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
    # 오디오 (공식 확정)
    "audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
    # 문서 — 비전 지원
    "application/pdf",
    # 문서 — 텍스트 추출만 (보수적 포함)
    "text/plain", "text/html", "text/csv", "text/markdown", "text/xml",
    "application/json", "application/xml",
    # 비디오 (보수적)
    "video/mp4", "video/mpeg", "video/mov", "video/avi",
    "video/x-flv", "video/webm", "video/3gpp",
})


def validate_mime_types(files: list[FileInput]) -> None:
    """Gemini 지원 외 mime 타입이 하나라도 있으면 UnsupportedFileTypeError."""
    for fi in files:
        if fi.mime_type not in SUPPORTED_MIME_TYPES:
            raise UnsupportedFileTypeError(
                f"지원하지 않는 파일 타입: mime_type={fi.mime_type}, "
                f"file_name={fi.file_name}"
            )


# ── 개별 파일 크기 한도 ──────────────────────────────────────

MAX_FILE_SIZE: int = 2 * 1024 ** 3  # 2 GB — Gemini Files API 파일당 상한


def validate_file_sizes(files: list[FileInput]) -> None:
    """2GB 초과 파일은 inline·Files API 어느 경로로도 처리 불가 → FileTooLargeError."""
    for fi in files:
        if fi.file_size > MAX_FILE_SIZE:
            raise FileTooLargeError(
                f"파일 크기가 2GB를 초과합니다: "
                f"file_name={fi.file_name}, size={fi.file_size}"
            )
