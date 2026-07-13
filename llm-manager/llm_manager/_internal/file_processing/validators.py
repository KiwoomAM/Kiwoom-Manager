"""파일 메타 검증 — mime 화이트리스트 + 2GB 초과.

runner의 llm_request_async / llm_request_stream_async 진입 직후 fail-fast로
잘못된 입력을 차단한다 (prepare·MDB 조회 이전).

상세 설계: docs/07.inline-vs-files-api-strategy.md §2.4, §4.5, §4.6, §7.7
"""

from __future__ import annotations

from llm_manager._internal.handler_client._config import get_settings
from llm_manager.exceptions import FileTooLargeError, UnsupportedFileTypeError
from llm_manager.models import FileInput

# ── mime 화이트리스트 ────────────────────────────────────────

SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    # 이미지 (공식 확정)
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
    # 오디오 (공식 확정)
    "audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
    # 오디오 — 공식 목록 밖이나 Gemini 가 실제로 받아준다 (.m4a = MP4 컨테이너 + AAC 코덱).
    # 증거: quickstarts/handler_client/04_files.ipynb — 화이트리스트를 거치지 않는
    # handler_client 경로로 test_audio.m4a 를 보내 정상 응답을 받는다.
    "audio/mp4",
    # 문서 — 비전 지원
    "application/pdf",
    # 문서 — 텍스트 추출만 (보수적 포함)
    "text/plain", "text/html", "text/csv", "text/markdown", "text/xml",
    "application/json", "application/xml",
    # 비디오 (보수적). video/x-ms-wmv 는 .wmv 의 실제 탐지 MIME(filetype/OS 매핑)이지만,
    # stored 경로에선 normalize_mime_types 가 검증 전에 video/wmv 로 교정하므로 이 엔트리엔
    # 도달하지 않는다 — 정규화를 거치지 않는 입력을 대비한 방어적 잔류다.
    "video/mp4", "video/mpeg", "video/mov", "video/avi",
    "video/x-flv", "video/webm", "video/3gpp",
    "video/mpg", "video/wmv", "video/x-ms-wmv",
})


def normalize_mime_types(files: list[FileInput]) -> None:
    """mime 을 Gemini 표준형으로 제자리 교정한다. validate_mime_types 보다 먼저 부른다.

    화이트리스트는 정규화된 이름(`audio/mp3`)으로 적혀 있는데, file-manager 의
    build_metadata 는 `mimetypes.guess_type` 이 주는 별칭(`audio/mpeg`)을 싣는다.
    정규화를 검증보다 뒤에 두면 Gemini 가 받아주는 타입이 거짓 거부된다.

    gemini_mime_normalize 는 전체 mime 표가 아니라 **별칭 교정표**다. 맵에 없는 값은
    이미 표준형이거나 미지의 타입이므로 그대로 둔다. 판정은 화이트리스트가 한다.
    """
    normalize = get_settings().gemini_mime_normalize
    for fi in files:
        fi.mime_type = normalize.get(fi.mime_type, fi.mime_type)


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
