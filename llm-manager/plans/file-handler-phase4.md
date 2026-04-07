# File Handler Phase 4: orchestrator(run_many) + 파일 서버

> Phase 3(서버화) 완료 후 진행.

## 목표

1. orchestrator.py에 file_handler 연동 (run_many + 공유 파일)
2. 파일 서버 구축 + MDB에서 파일 존재 여부 체크

---

## Part A: orchestrator(run_many) 파일 연동

### 배경

run_many는 같은 파일 + 다른 질문 N개를 배치로 처리하는 함수. Phase 2에서는 단일 호출만 지원하고 orchestrator.py는 보관만 했음.

### 설계 방향

- run_many에 `file_paths`, `cache_ttl` 파라미터 추가
- 공유 파일 1회 처리 후 모든 state에 주입
- run_many는 N개 배치이므로 context caching 자동 적용 (거의 항상 이득)
- model 통일 검증 (다르면 ValueError)

### run_many 파일 처리 흐름

```
run_many(states, file_paths=[...], cache_ttl=1800)
    ↓
① model 검증: 모든 state의 model 동일한지 확인
    ↓
② 공유 file_paths checksum 계산
    ↓
③ 모든 파일을 하나의 context cache에 통합
   ├─ ≤20MB → inline 데이터를 cache contents에 포함
   ├─ >20MB → Files API 업로드 → URI를 cache contents에 포함
   └─ PDF >50MB → split → Files API → URI들을 cache contents에 포함
    ↓
④ create_cache 시도
   ├─ 성공 → cached_content_name 획득
   └─ 실패 → fallback: 단일 호출 방식 (inline + URI 혼합)
    ↓
⑤ Redis에 저장 (1회)
    ↓
⑥ 모든 state의 llm_input에 파일 참조 주입
    ↓
⑦ 각 state별 기존 파이프라인 (done_check → call → save)
```

### 단일 vs 배치 비교

| | 단일 호출 (Phase 2) | 배치 호출 (Phase 4) |
|--|-------------------|-------------------|
| context caching | opt-in (cache_ttl 명시) | 자동 (N개 배치는 거의 항상 이득) |
| Redis 조회 | O (항상) | X (스킵, 바로 업로드+캐싱) |
| model 통일 | X | O (다르면 ValueError) |
| 혼합 파일 | 각 파일별 최적 경로 독립 | 모든 파일 하나의 cache에 통합 |
| done_check | 1회 | 각 state별 (질문이 다르니까) |

---

## Part B: 파일 서버 + MDB 파일 존재 여부 체크

### 배경

Phase 2에서는 파일 원본을 저장하지 않음. 사용자가 매 호출마다 `file_paths`로 로컬 파일 경로 제공. Redis TTL 만료 시 로컬에서 다시 읽어 재업로드.

### 문제

- 로컬 파일이 삭제된 경우 재업로드 불가
- 여러 agent가 같은 파일을 중복 업로드

### 설계 방향

- **파일 서버**: 파일 원본을 중앙 저장소에 보관
- **MDB**: 파일 서버에 해당 파일이 존재하는지 메타데이터 관리
- 흐름: checksum → MDB에서 파일 서버 존재 여부 조회 → 있으면 파일 서버에서 가져옴 → 없으면 로컬에서 업로드 + 파일 서버에 저장

### 주의

- MDB GridFS는 16MB 제한으로 대용량 파일에 부적합 → 별도 파일 서버 권장
- 파일 서버 선택지: S3, MinIO, NFS, 자체 구축 등 — 인프라 상황에 따라 결정

---

## 미래 고려사항

- **Files API 프로젝트 저장 제한 20GB**: 대량 파일 업로드 시 초과 가능. 사용량 모니터링 + 초과 시 오래된 파일 정리 또는 업로드 제한 로직 필요

## 선행 조건

- Phase 3 (서버화) 완료
- 파일 서버 인프라 구축
- orchestrator.py 현재 상태 확인 (보관 중)

## TODO (Phase 4 시작 시 상세화)

- [ ] orchestrator.py에 file_paths, cache_ttl 파라미터 추가
- [ ] run_many 내부에 공유 파일 처리 로직 구현
- [ ] model 통일 검증
- [ ] run_many_stream도 동일 변경
- [ ] 파일 서버 선정 + 구축
- [ ] MDB에 파일 메타데이터 스키마 설계
- [ ] cache_store.py 확장 (파일 서버 연동)
- [ ] Files API 20GB 제한 모니터링 로직
- [ ] 통합 테스트
