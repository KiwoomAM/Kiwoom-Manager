# 503 만 전용 예외로 올린다

`vdb-handler`는 "지금 이 요청을 받지 못한다"(503)와 "받아서 처리하다 실패했다"(500)를
다른 코드로 답한다. 호출자가 다르게 다뤄야 하는 상태인데, SDK 가 4xx·5xx 를 전부
`VDBHandlerError` 하나로 올려 **그 구분이 SDK 경계에서 숫자로만 남았다.**
`VDBUnavailableError(VDBHandlerError)` 를 만들어 503 에만 쓴다.

## 503 을 "재시도하면 되는 오류" 로 읽지 않는다

처음에는 그렇게 적었다가 리뷰에서 고쳤다. 서버 코드를 보면 503 에 성격이 다른 것들이
섞인다.

| 무엇 | 어디서 나오나 | 다시 부르면 |
|---|---|---|
| 저장소에 닿지 못함 | `vector_store` 의 오류 번역기 | 된다 |
| SPLADE·ColBERT 로딩 중 | `ModelNotReadyError` | 된다 |
| **그 모델이 꺼져 있음** | `ModelNotReadyError("SPLADE (disabled)")` | **영영 안 된다** |
| 동시 처리 한계 초과 | `uvicorn --limit-concurrency` | 된다 |

반대로 500 에도 다시 부르면 되는 것이 섞인다. `dense_embedder.embed_text` 가 Gemini
예외를 그대로 다시 올리는데 `main.py` 에는 그것을 받는 핸들러가 없어, **일시적인 Gemini
오류도 500 으로 나간다.**

그래서 이 예외가 뜻하는 것은 "재시도하라" 가 아니라 **"서비스가 지금 받지 못했다"** 는
상태다. 무엇이 일시적인지는 부르는 쪽의 도메인이고, SDK 는 상태를 갈라 주는 데까지만
한다.

## 왜 하나뿐인가

코드마다 예외를 만들면 **`vdb-handler`의 분류 체계를 SDK 에 복제**하는 것이 된다.
`vdb-handler`가 규칙을 넓히면 SDK 만 옛 규칙으로 남는다. 상태의 성격이 뚜렷이 갈리는
경계는 **"받지 못했다" 하나**이므로 그것만 올리고, 나머지는 `status_code` 로 읽는다.

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
