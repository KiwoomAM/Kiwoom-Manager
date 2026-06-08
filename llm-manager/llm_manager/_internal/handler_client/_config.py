"""SDK 설정 (환경변수 기반)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SDKSettings(BaseSettings):
    """SDK 환경변수 설정.

    환경변수 우선순위: 환경변수 > .env 파일 (CWD 기준).
    모듈 레벨에서 인스턴스화하지 말 것 — `get_settings()` 로 첫 사용 시점에 생성한다.
    """

    # Handler 서비스 연결
    handler_url: str = Field(default="http://localhost:8100", alias="llm_handler_url")
    timeout: float = 120.0

    # MIME 정규화: filetype·mimetypes 감지값 → Gemini API 공식 포맷
    gemini_mime_normalize: dict[str, str] = {
        "audio/mpeg": "audio/mp3",
        "audio/x-wav": "audio/wav",
        "audio/x-flac": "audio/flac",
        "audio/x-aiff": "audio/aiff",
        "video/quicktime": "video/mov",
        "video/x-msvideo": "video/avi",
        "video/x-ms-wmv": "video/wmv",
    }

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_settings: SDKSettings | None = None


def get_settings() -> SDKSettings:
    """SDK 설정을 **첫 사용 시점**에 1회 로드한다 (import 시점 동결 방지).

    모듈 import 시점에 인스턴스화하면 `handler_url` 이 그 순간의 env 로 굳는다.
    소비자(llm-manager 등)는 import 이후 `LLM_HANDLER_URL` 을 세팅하는 경우가 있어,
    client 를 처음 만드는 시점에 읽어야 그 값을 반영한다. `_get_client()` 의
    `_instance is None` 싱글턴과 동일한 lazy 패턴.
    """
    global _settings
    if _settings is None:
        _settings = SDKSettings()
    return _settings
