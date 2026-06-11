"""rdb-manager 테스트 공용 설정.

HTTP SDK 전환 후에는 psycopg 모킹이 필요 없다. 클라이언트 단위 테스트는
httpx.MockTransport로 핸들러 응답을 모의한다.
"""
