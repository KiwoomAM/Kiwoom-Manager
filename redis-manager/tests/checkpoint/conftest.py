# -*- coding: utf-8 -*-
"""checkpoint 서브패키지 테스트 — [langgraph] extra(redis-py + langgraph-checkpoint-redis)
가 설치돼 있어야 한다. 미설치 환경(코어만 설치)에서는 이 디렉토리 전체를 skip한다."""
import pytest

pytest.importorskip("redis")
pytest.importorskip("langgraph.checkpoint.redis")
