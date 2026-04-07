# File Handler Phase 3: llm-handler 합체 + 서버화

> Phase 2(라이브러리 구현) 완료 후 진행. 담당자 합의 필요.

## 목표

1. llm-handler와 llm-request를 하나의 SDK+Service로 합치기
2. 서버화 (SDK+Service 분리)

## 왜 서버화하는가

| 관점 | 라이브러리 (Phase 2) | 서버화 (Phase 3) |
|------|-------------------|----------------|
| Agent 부담 | redis/mdb/llm handler SDK 전부 agent 메모리에 로드 | SDK 하나만 |
| 의존성 | agent가 모든 handler 의존성 설치 필요 | SDK만 (httpx, pydantic) |
| 설정 관리 | agent마다 Redis URL, MDB URL, LLM URL 다 설정 | Service URL 하나만 |
| 보안 | agent가 Redis/MDB/Gemini API 키 직접 보유 | 키는 서버에만 |
| 배포 | agent 업데이트 = llm-request도 업데이트 | 서비스 독립 배포 |
| 디버깅 | agent 로그에 전부 섞임 | 서비스 로그 분리 |
| 네트워크 | 로컬 함수 호출 (빠름) | HTTP 왕복 추가 (수 ms) |
| 파일 전송 | 로컬이라 비용 0 | SDK→Service 파일 전달 (Redis 조회로 완화) |

## 아키텍처

```
Agent → llm-request SDK → llm-request Service → Gemini
            ↕                    ↕
     Redis (파일 캐시 조회)       MDB (LLM 응답 저장)
     checksum 계산
```

### SDK (agent 머신에서 실행)

- `file_paths` → checksum 계산
- Redis 조회 (같은 파일이 이미 캐싱돼있는지)
- cache hit → **파일을 서버에 안 보냄**, cached_content name만 전달
- cache miss → 파일 바이너리를 서버에 전달

### Service (별도 서버)

- Files API 업로드 (Gemini)
- create_cache (Gemini)
- LLM 호출 (Gemini)
- MDB 저장 (agent_llm)
- Redis 저장 (file_cache)

**핵심 절약**: SDK에서 Redis 조회하여 같은 파일의 반복 네트워크 전송 방지.

## 디렉토리 구조

```
packages/llm-request/
├── pyproject.toml                    # workspace (sdk, service 멤버)
├── sdk/
│   ├── pyproject.toml
│   └── llm_request/
│       ├── __init__.py               # public API
│       ├── _client.py                # httpx 클라이언트
│       ├── checksum.py               # checksum 계산 (Phase 2에서 이동)
│       └── models.py                 # 요청/응답 모델
├── service/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── app/
│       ├── main.py                   # FastAPI 앱
│       ├── config.py
│       ├── core/
│       │   ├── runner.py             # Phase 2 runner 로직 이동
│       │   ├── store.py              # MDB 저장
│       │   └── done_check.py         # 캐시 조회
│       ├── file_handler/             # Phase 2에서 만든 것 이동
│       │   ├── resolver.py
│       │   ├── cache_store.py
│       │   ├── uploader.py
│       │   └── splitter.py
│       ├── models/
│       │   ├── requests.py
│       │   └── responses.py
│       └── routers/
│           ├── generate.py
│           └── files.py
└── plans/
```

## 선행 조건

- Phase 2 구현 완료 + 검증
- llm-handler 담당자와 합의
- 배포 인프라 준비 (Docker, 포트 할당 등)

## TODO (Phase 3 시작 시 상세화)

- [ ] llm-handler 담당자와 합체 범위 합의
- [ ] SDK 신규 작성 (httpx 클라이언트, checksum, models)
- [ ] Service 신규 작성 (FastAPI, Phase 2 로직 이동)
- [ ] llm-handler 엔드포인트 Service에 통합
- [ ] 기존 agent 코드 마이그레이션 (import 변경)
- [ ] 통합 테스트
- [ ] 배포
