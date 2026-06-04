# kiwoom-winus-manager

WINUS(Oracle) Gateway 서비스를 호출하는 클라이언트 SDK. import 이름은 `winus_manager`.

> 서버(Oracle Gateway)는 `packages/winus-handler/service`에서 docker로 띄운다 (컨테이너 `kiwoom-winus-handler`, 포트 8200). 이 패키지는 그 서버를 호출하는 클라이언트다.

## 설치

```
pip install "kiwoom-winus-manager @ git+https://github.com/KIWOOMxSNU/Kiwoom-Rag.git@main#subdirectory=packages/winus-manager"
```

로컬 개발:
```
pip install -e packages/winus-manager
```

## 사용

```python
import winus_manager
df = winus_manager.query_to_dataframe("SELECT * FROM t WHERE code = :code", {"code": "A001"})
```

## 운영 정책 (엔드포인트 잠금)

Handler 접속 URL은 `http://192.7.9.45:8200`로 코드에 고정되어 있다. `configure(base_url=...)` 인자와 `WINUS_HANDLER_URL` 환경변수는 무시된다. 자격증명/timeout만 유효.
