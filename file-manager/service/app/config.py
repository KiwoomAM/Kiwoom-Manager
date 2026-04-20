# -*- coding: utf-8 -*-
"""
File Manager Service 환경 설정
"""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MDB Handler Service URL (메타데이터 저장/조회)
    mdb_handler_url: str = "http://192.7.9.45:8300"
    mdb_database: str = "kw_ai"
    mdb_collection: str = "file_cache"

    # File Server (운영: D:/kw_ai) — file_handler 패키지가 os.environ으로 읽는다
    file_storage_root: str = "/mnt/c/Users/황정현/kw_ai"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# file_handler는 FILE_STORAGE_ROOT를 os.environ으로 읽기 때문에,
# pydantic-settings가 .env/기본값에서 읽은 값을 os.environ으로 전파한다.
# 이미 os.environ에 값이 있으면 우선순위를 존중한다.
os.environ.setdefault("FILE_STORAGE_ROOT", settings.file_storage_root)
