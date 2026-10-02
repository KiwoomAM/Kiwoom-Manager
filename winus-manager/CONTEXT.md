# WINUS Manager

Kiwoom 코드가 WINUS 원장을 읽는 통로다. 원장 접속과 조회 실행은 `winus-handler` 가 맡고,
이 패키지는 그 앞에 서는 HTTP 클라이언트다. 여기에는 **SDK 안에서 일어나는 일**을 부르는
말만 적는다. 서버에서 일어나는 일의 말은 `winus-handler` 의
[CONTEXT.md](https://github.com/KiwoomAM/Kiwoom-Handler/blob/main/winus-handler/CONTEXT.md) 가
주인이고, 여기서는 다시 정의하지 않고 가리키기만 한다 — 같은 뜻을 두 레포에 적으면 어긋난다.

## Language

### 시간이 끊는 것

둘 다 "타임아웃" 으로 읽히지만 지키는 쪽이 다르다. 하나는 기다리기를 그만두는 것이고,
하나는 원장에서 조회를 끊는 것이다.

**소비자 타임아웃** (`configure(timeout=)`, `ConsumerTimeoutError`):
소비자가 응답을 **기다리기를 그만두는** 상한(초)이며, sync·aio 인터페이스마다 `configure()` 로
정한다.
이것이 지나도 **원장에서는 조회가 계속 돈다** — 응답만 버린 것이다. 핸들러에 닿지도 못한
타임아웃은 조회가 돌지 않았으므로 이것이 아니라 연결 실패다.
_Avoid_: 쿼리 타임아웃, 요청 타임아웃

**마감** (`query_timeout_ms`):
뜻과 규칙은 `winus-handler` 용어집이 정한다 — 서버가 지키는 값이다. SDK 에서는 조회마다
`query_timeout_ms`(밀리초)로 요청하며, 소비자 타임아웃과 같거나 길게 잡을 수 없다 — 소비자가
먼저 포기하면 그 뒤로 조회가 주인 없이 돈다.
_Avoid_: `timeout_ms` — 옆의 `timeout` 을 호출마다 덮어쓰는 것으로 읽힌다. 기다리는 시간은
그대로다.

### 결과

**취소됨** (`QueryCancelledError`):
운영자나 마감, 또는 원본(DBA 세션 종료 등)이 원장에서 조회를 의도적으로 끊었다는 결과. 쿼리가 틀린 것도 서버가 잠시 바쁜 것도
아니므로 **그대로 재시도하지 않는다** — 재시도하면 운영자가 끊은 조회가 되살아난다.
_Avoid_: 타임아웃 — 소비자 타임아웃은 원장을 놓아 주지 않지만, 취소됨은 이미 놓아 준 것이다.

### 층

**`winus-handler`**:
이 패키지가 HTTP 로 부르는 서비스. `KiwoomAM/Kiwoom-Handler` 레포의 `winus-handler/` 이고
운영 서버에 떠 있다(주소는 `winus_manager/_config.py`). 마감·취소·차단·`client_query_id` 의 뜻은 그쪽
용어집이 정한다.
_Avoid_: 게이트웨이, Oracle Gateway — 쿼리 번호로 부르는 Query Gateway 는 별개의 서비스다.
