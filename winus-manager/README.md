# kiwoom-winus-manager

WINUS(Oracle) 원장을 읽는 `winus-handler` 를 호출하는 클라이언트 SDK. import 이름은 `winus_manager`.

> 서버는 `KiwoomAM/Kiwoom-Handler` 레포의 `winus-handler/` 이고 docker 로 띄운다 (컨테이너
> `kiwoom-winus-handler`, 포트 8200). 이 패키지는 그 서버를 호출하는 클라이언트다. 용어의 뜻은
> [CONTEXT.md](CONTEXT.md) 를 따른다.

## 설치

```toml
dependencies = [
    "kiwoom-winus-manager @ git+https://github.com/KiwoomAM/Kiwoom-Manager.git#subdirectory=winus-manager",
]
```

로컬 개발:
```
pip install -e winus-manager
```

## 사용

```python
import winus_manager
df = winus_manager.query_to_dataframe("SELECT * FROM t WHERE code = :code", {"code": "A001"})
```

### 조회에 이름과 마감 붙이기

```python
df = winus_manager.query_to_dataframe(
    "SELECT ... WHERE 기준일 = :d", {"d": "20260930"},
    client_query_id="pf-nav-batch-20260930",
    query_timeout_ms=3_000,
)

# 비동기도 같다
import winus_manager.aio as winus_aio
rows = await winus_aio.fetch_all_dict(sql, params, query_timeout_ms=3_000)
```

- 두 인자 모두 선택이고 keyword 로만 받는다. 안 넘기면 본문에 싣지 않고 서버가 정한다.
- `query_timeout_ms` 는 이 조회의 **마감**(밀리초)이다. 이 길이가 지나면 서버가 원장에서 조회를
  끊는다. 서버 마감보다 **줄이기만** 할 수 있고(더 길게 보내면 서버 마감으로 깎인다), 기다리는
  시간을 늘리는 인자가 아니다.
- 기다리는 시간은 `configure(timeout=)`(**소비자 타임아웃**)만 바꾼다. 마감이 이 값 이상이면
  보내기 전에 `ValueError` 가 난다 — SDK 가 먼저 포기하면 조회가 주인 없이 원장에서 돈다.

### `client_query_id` 짓는 법

운영자가 관리 목록에서 "어느 소비자의 어떤 조회인지" 알아보고, 이 이름으로 끊는다.

- `<서비스>-<작업>-<식별값>` 으로 짓는다. 예: `pf-nav-batch-20260930`
- 재시도할 때는 **같은 이름**을 쓴다. 타임아웃 뒤 재시도로 원장에 둘이 함께 돌 때 운영자가
  이름 하나로 둘 다 끊을 수 있다.
- 바인드 값이나 개인정보를 넣지 않는다. 이름은 핸들러 로그와 관리 목록에 그대로 남는다.
- 길이 제한은 핸들러가 검증한다.

안 붙여도 조회는 그대로 돌고 마감과 `QueryCancelledError` 도 똑같이 적용된다. 다만 운영자
목록에는 이름 없이 SQL 앞부분과 경과 시간으로만 보이고, 이름으로 끊을 수 없다.

### 실패 처리

```python
import logging

import winus_manager
from winus_manager import (
    ConsumerTimeoutError,
    HandlerConnectionError,
    HandlerError,
    QueryCancelledError,
)

logger = logging.getLogger(__name__)

try:
    rows = winus_manager.fetch_all_dict(sql, params, client_query_id="pf-nav-batch-20260930")
except QueryCancelledError as e:
    # 운영자나 마감, 또는 원본(DBA 세션 종료 등)이 원장에서 끊었다. 그대로 재시도하지 않는다.
    logger.warning("조회 취소됨 [%s]: %s", e.request_id, e.detail)
except ConsumerTimeoutError as e:
    # 응답을 기다리다 그만뒀다. 조회는 원장에서 아직 돌 수 있다.
    logger.warning("응답 대기 시간 초과 [%s]", e.client_query_id)
except HandlerConnectionError:
    ...  # 핸들러에 닿지 못했다. 조회는 돌지 않았으므로 다시 보내도 된다.
except HandlerError as e:
    ...  # e.status_code 로 구분한다. 503 은 잠시 뒤 다시 와도 된다.
         # 500 은 DML 이 커밋 도중 끊겨 반영 여부를 모르는 경우도 포함하므로, 다시 쓰기 전에 반영됐는지 확인한다.
```

- `QueryCancelledError` 는 `HandlerError` 의 하위라 기존 `except HandlerError` 에도 잡힌다.
  **재시도 루프가 있다면 이 예외를 먼저 걸러야 한다.**
- `ConsumerTimeoutError` 뒤에 재시도하면 원장에 같은 조회가 둘 돌 수 있다. 재시도한다면 **같은
  `client_query_id`** 로 보내 운영자가 이름 하나로 둘 다 끊을 수 있게 한다. 기존의
  `except httpx.TimeoutException` 에도 잡히지만 임시 호환이며 다음 major 버전에서 뗀다.
  DML 에서 이 예외가 나면 쓰기가 반영됐는지 알 수 없으므로, 다시 쓰기 전에 반영 여부를 확인한다.
- 연결 시간 초과(`httpx.ConnectTimeout`·`PoolTimeout`)는 요청이 나가지 않은 것이라
  `HandlerConnectionError` 로 올라온다.
- `e.request_id` 는 핸들러가 요청마다 발급한 번호다. 핸들러 로그와 관리 목록에서 그 요청을 찾는다.
- 응답 도중 연결이 끊기는 그 밖의 전송 실패는 감싸지 않는다 — `httpx.TransportError` 가 그대로
  올라온다.

## 운영 정책 (엔드포인트 잠금)

Handler 접속 URL은 `http://192.7.9.45:8200`로 코드에 고정되어 있다. `configure(base_url=...)` 인자와 `WINUS_HANDLER_URL` 환경변수는 무시된다. 자격증명/timeout만 유효.
