# -*- coding: utf-8 -*-
"""
File Manager Service 환경 설정
"""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MDB Handler Service URL (메타데이터 저장/조회)
    mdb_handler_url: str = "http://mdb-handler:8300"
    mongodb_database: str = "kw_ai"

    # File Server — file_handler 패키지가 os.environ으로 읽는다
    # (docker compose가 컨테이너 내부에서 /data로 주입)
    file_storage_root: str = "/data"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# 캐시 컬렉션은 환경변수로 덮어쓰지 못하게 상수로 고정.
# Why: 서버 환경의 MONGODB_COLLECTION이 다른 값으로 잡혀 있어 캐시가
# 엉뚱한 컬렉션으로 들어가는 사고가 있었음.
MONGODB_CACHE_COLLECTION = "file_cache"

# file_handler는 FILE_STORAGE_ROOT를 os.environ으로 읽기 때문에,
# pydantic-settings가 .env/기본값에서 읽은 값을 os.environ으로 전파한다.
# 이미 os.environ에 값이 있으면 우선순위를 존중한다.
os.environ.setdefault("FILE_STORAGE_ROOT", settings.file_storage_root)
