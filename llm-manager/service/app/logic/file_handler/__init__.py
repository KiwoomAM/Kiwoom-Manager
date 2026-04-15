"""file_handler: 파일 업로드/캐싱 자동화 모듈."""

from .resolver import ResolvedFiles, resolve_files, resolve_files_after_404

__all__ = ["ResolvedFiles", "resolve_files", "resolve_files_after_404"]
