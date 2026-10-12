"""rdb_manager 내부 상수 — 핸들러 접속 URL 단일 출처.

운영 정책상 handler 접속 위치는 외부 설정(env / configure(base_url=...))으로
변경할 수 없으며 항상 운영 서버로 고정된다. 동기(__init__)·비동기(aio)
양쪽이 이 값을 공유한다 (엔드포인트 변경 시 이 파일 한 곳만 수정).
"""

HANDLER_BASE_URL: str = "http://192.7.9.45:8700"

# 핸들러가 admin 으로 판정하는 키. SDK 를 거친 호출이 모두 admin 이 되도록 기본으로 싣는다
# (Kiwoom-Handler docs/adr/0001). 핸들러의 같은 상수와 값이 어긋나면 모든 요청이 401 로 드러난다.
# 공개 이름으로 내보내지 않는다 — SDK 밖의 직접 호출에 옮겨 쓰는 길을 안내하지 않기 위해서다.
_ADMIN_KEY: str = "iamadmin123"
