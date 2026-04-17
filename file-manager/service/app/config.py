# -*- coding: utf-8 -*-
"""
File Manager Service 환경 설정
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MDB Handler Service URL (메타데이터 저장/조회)
    mdb_handler_url: str = "http://192.7.9.45:8300"
    mdb_database: str = "kw_ai"
    mdb_collection: str = "file_cache"

    # File Server (운영: D:/kw_ai)
    file_storage_root: str = "/mnt/c/Users/황정현/kw_ai"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
