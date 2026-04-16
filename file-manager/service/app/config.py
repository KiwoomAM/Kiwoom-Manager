"""file_manager Service 설정."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_STORAGE = Path(__file__).resolve().parent.parent / "storage"


class Settings(BaseSettings):
    """환경변수로부터 설정을 읽어오는 클래스."""

    # 외부 서비스
    mdb_handler_url: str = "http://localhost:8300"

    # MongoDB
    mongodb_database: str = "kw_ai_dev"
    mongodb_collection: str = "file_manager"

    # 파일 저장 디렉토리 (시연용 — 실제 서비스에서는 파일 서버 경로)
    storage_dir: str = str(_DEFAULT_STORAGE)

    # Server
    host: str = "0.0.0.0"
    port: int = 8102

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
