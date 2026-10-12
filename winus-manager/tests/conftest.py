"""테스트 파일들이 함께 쓰는 fixture.

`handler`(가짜 winus-handler)는 test_execution_options 에 정의되어 있고, 여기서 다시 내보내 다른
테스트 파일도 같은 장치를 쓰게 한다.
"""

from tests.test_execution_options import handler  # noqa: F401 — pytest 가 이름으로 찾는 fixture
