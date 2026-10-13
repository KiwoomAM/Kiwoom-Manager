# -*- coding: utf-8 -*-
"""mdb_manager 내부 상수 — 동기(_client)·비동기(_async_client) 클라이언트가 함께 쓴다."""

# 핸들러가 admin 으로 판정하는 키. SDK 를 거친 호출이 모두 admin 이 되도록 기본으로 싣는다
# (Kiwoom-Handler docs/adr/0001). 핸들러의 같은 상수와 값이 어긋나면 모든 요청이 401 로 드러난다.
# 공개 이름으로 내보내지 않는다 — SDK 밖의 직접 호출에 옮겨 쓰는 길을 안내하지 않기 위해서다.
_ADMIN_KEY: str = "iamadmin123"
