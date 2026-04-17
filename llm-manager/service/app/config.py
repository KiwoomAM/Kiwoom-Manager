"""Service 설정 (환경변수 기반)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수로부터 설정을 읽어오는 클래스."""

    # 외부 서비스 URL
    mdb_handler_url: str = "http://192.7.9.45:8300"
    llm_handler_url: str = "http://192.7.9.45:8100"
    redis_url: str = "redis://192.7.9.45:7617/0"

    # file_manager Service (포트는 TBD — 담당자 협의 후 확정)
    file_manager_url: str = "http://192.7.9.45:8102"
    file_manager_timeout: float = 30.0

    # MongoDB
    mongodb_database: str = "kw_ai"

    # Server
    host: str = "0.0.0.0"
    port: int = 8101

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
