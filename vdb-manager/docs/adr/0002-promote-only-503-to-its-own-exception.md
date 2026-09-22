# 503 만 전용 예외로 올린다

`vdb-handler`는 "저장소에 닿지 못했다"(503)와 "닿았는데 그 안에서 실패했다"(500)를 다른
코드로 답한다. 앞은 재시도가 의미 있고 뒤는 없다 — 그 구분을 만든 이유가 그것이다
(`KiwoomAM/Kiwoom-Handler` 의 ADR 0003). 그런데 SDK 가 4xx·5xx 를 전부
`VDBHandlerError` 하나로 올려, **그 구분이 SDK 경계에서 숫자로만 남았다.**
`VDBUnavailableError(VDBHandlerError)` 를 만들어 503 에만 쓴다.

## 왜 하나뿐인가

코드마다 예외를 만들면 **`vdb-handler`의 분류 체계를 SDK 에 복제**하는 것이 된다. `vdb-handler`가 규칙을
넓히면 SDK 만 옛 규칙으로 남는다. `vdb-handler`가 **뜻을 부여한 구분**은 하나뿐이므로 하나만
올린다. 나머지는 `status_code` 로 읽는다.

`VDBHandlerError` 를 상속하므로 기존 `except VDBHandlerError` 는 전과 같이 잡고
`status_code` 도 남는다. 새 갈래를 더하면서 기존 호출자를 깨지 않는다.

## 형제 패키지와 다른 점

사내 SDK 여덟 중 여섯(mdb·rdb·winus·file·api·vdb)이 상태 코드를 예외로 가르지 않는다.
벗어난 둘도 축이 서로 다르다 — redis-manager 는 `503 → RedisRetryExhausted` 로 승격하고
(대신 `status_code` 속성을 버렸다), llm-manager 의 내부 SDK 는 llm-handler 가 붙여 주는
`error_type` 태그로 여섯 클래스에 나눈다. **"일시적 실패를 예외 종류로 표현한다" 는
공통 관례는 없다.**

그래도 여기서 하는 이유는 `vdb-handler` 가 그 구분을 **명시적으로 만들어 두었기**
때문이다. 관례를 따르는 대가가 "`vdb-handler`가 애써 만든 신호를 버리는 것" 이라면 관례가 아니라
신호를 택한다.

llm 식 `error_type` dispatch 는 기각했다. `vdb-handler`가 그런 태그를 보내지 않으므로
`vdb-handler` 부터 바꿔야 한다.

## 재시도는 하지 않는다

SDK 는 갈래를 알려 주는 데까지만 한다. 언제 몇 번 다시 부를지는 호출자의 사정이다.
사내 SDK 전체에서 실제 재시도 루프는 두 곳뿐이고(llm-manager 의 업로드, handler_client 의
`RemoteProtocolError` 1회) 나머지는 전부 같은 방침이다.
