# kiwoom-api-manager

`api-handler` 서버용 **HTTP 클라이언트** 패키지. 소비하는 쪽(백엔드/에이전트)이 `import`해서 사용합니다.

- **import해서 쓰는 클라이언트** → 이 패키지 (`api-manager`)
- **띄우는 서버** → [`../api-handler`](../api-handler) (FastAPI, port 8400)

## 설치

```bash
pip install -e packages/api-manager
```

## 사용

```python
from api_manager import APIManager

api = APIManager()  # Singleton. 서버 주소는 env API_HANDLER_URL (기본 http://192.7.9.45:8400)

# 도구 실행 (동기/비동기)
result = api.execute("finnhub_price", params={"ticker": "AAPL"})
result = await api.aexecute("finnhub_price", params={"ticker": "AAPL"}, format=True)

# 조회
schemas = api.schemas()
status = api.status()
limits = api.rate_limits()
```

서버 엔드포인트·API별 파라미터·도구 목록 등 상세는 [`../api-handler/README.md`](../api-handler/README.md) 참고.
