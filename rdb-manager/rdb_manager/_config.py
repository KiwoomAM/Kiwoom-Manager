"""rdb_manager 내부 상수 — 핸들러 접속 URL 단일 출처.

운영 정책상 handler 접속 위치는 외부 설정(env / configure(base_url=...))으로
변경할 수 없으며 항상 운영 서버로 고정된다. 동기(__init__)·비동기(aio)
양쪽이 이 값을 공유한다 (엔드포인트 변경 시 이 파일 한 곳만 수정).
"""

HANDLER_BASE_URL: str = "http://192.7.9.45:8700"
