"""통합 테스트 conftest — RUN_INTEGRATION=1 환경변수 필요."""
import os

import pytest

if not os.getenv("RUN_INTEGRATION"):
    pytest.skip(
        "통합 테스트 — RUN_INTEGRATION=1 환경변수로 활성화",
        allow_module_level=True,
    )
