# PR 작업 플랜 (임시 문서 — PR 제출 후 삭제)

README_v2.md를 섹션별로 작성하면서 `docs/00.pull_request.md`를 구간구간 검토하고 수정할 내용을 메모하는 작업 문서.

---

## 코드 vs PR 초안 불일치 — 사전 확인 필요 항목

README 작성 전에 이미 확인된 차이. PR 수정 시 반드시 반영.

| 항목 | PR 초안 (00.pull_request.md) | 실제 코드 | 상태 |
|------|------------------------------|-----------|------|
| 파일 인자 | `file_paths` 또는 `file_metas` 혼용 | `files: list[FileInput]` (bytes 포함) | ❌ 수정 필요 |
| 반환 타입 | `tuple[LLMResponse, dict]` | `tuple[dict, dict]` (raw dict 반환) | ❌ 수정 필요 |
| 타이밍 인자 | `ttl_seconds`, `context_cache_ttl`, `standby_ttl_seconds` 개별 | `timings: Timings` TypedDict 하나로 통합 | ❌ 수정 필요 |
| ingest 단계 | 언급 없음 | `service/app/logic/ingest.py` 신규 존재 | ❓ 확인 필요 |
| Stage 5 file_manager | "SDK에서 파일 바이트 전송 제거, FileMeta만 전달" | FileInput에 bytes 포함 (직접 전달) | ❌ 수정 필요 |

---

## README 섹션 ↔ PR 검토 대응표

README 섹션을 완성할 때마다 오른쪽 PR 항목 체크.

| README 섹션 | 검토할 PR 항목 | 완료 |
|------------|--------------|------|
| 1. 개요 | 한줄 요약, 1. 변경 배경 (문제 정의 맞는지) | ☐ |
| 2. 핵심 특징 | — | ☐ |
| 3. 디렉터리 구조 | 5. 파일 변경 요약 (새 파일 목록 맞는지) | ☐ |
| 4. 설정 및 준비사항 | 7. 마이그레이션 환경변수 표 | ☐ |
| 5. Quick Start | 7. 마이그레이션 — 기존 사용자 코드 변경 예시 | ☐ |
| 6. llm_request 함수 | 4.1 keying, 4.4 SDK 설계 | ☐ |
| 6a. FileInput | 4.4 SDK 설계, Stage 4/5 | ☐ |
| 6b. Timings | 4.4 SDK 설계 | ☐ |
| 6c. cache_mode/save_mode | — (변경 없음) | ☐ |
| 6d. 반환 타입 | 4.4 SDK 설계 | ☐ |
| 7. 캐시 키 규칙 | 4.1 캐시 키 결정성 (keying.py) | ☐ |
| 8. 파일 처리 (file_handler) | 4.2 파일 분류 전략, 4.3 PDF 분할, Stage 3 | ☐ |
| 8a. Files API URI 캐시 | Stage 2, 4.2 | ☐ |
| 8b. Context Cache | 4.2, 8. PR에 포함 안 된 작업 (없는지 확인) | ☐ |
| 8c. 404 자동 복구 | 4.6 Gemini 404 자동 복구 | ☐ |
| 9. Cache Stampede Dedup | 8. 이 PR에 포함되지 않은 작업 → dedup은 포함됐는지? | ☐ |
| 10. Service API | 3. 최종 아키텍처, Stage 4 Service API 엔드포인트 표 | ☐ |
| 11. MDB 저장 구조 | — (별도 섹션 없음, 4.1에 언급) | ☐ |
| 12. 에러 처리 | 4.5 Service 에러 처리 | ☐ |
| 13. 주의사항 | — | ☐ |
| 14. 마이그레이션 가이드 | 7. 마이그레이션 영향 전체 | ☐ |

---

## PR 섹션별 검토 메모

README 작성하면서 발견한 수정 사항을 여기 기록.

### 1. 변경 배경
- [ ] Files API URI 문제 설명 → 그대로 유효, 확인 필요 없음
- [ ] "파일 처리가 에이전트 책임이었던 문제" → 그대로 유효

### 2. 변경 흐름 (시간순)
- [ ] Stage 2: checksum 알고리즘 설명 확인 (SHA-256(file_name + mtime + size) 맞는지)
- [ ] Stage 4: SDK 분리 코드 예시에서 `file_paths` → `files: list[FileInput]` 수정 필요
- [ ] Stage 5: "SDK에서 파일 바이트 처리 제거" 설명이 현재 코드와 맞는지 재확인

### 3. 최종 아키텍처
- [ ] 아키텍처 다이어그램에서 파일 전달 방식 확인
- [ ] ingest.py 단계 추가 여부 결정 (다이어그램에 넣을지)

### 4. 주요 변경 상세
- [ ] 4.4 SDK 설계: `file_paths`/`file_metas` → `files: list[FileInput]` 수정
- [ ] 4.4 SDK 설계: 반환 타입 `tuple[LLMResponse, dict]` → `tuple[dict, dict]` 수정
- [ ] 4.4 SDK 설계: `ttl_seconds`/`context_cache_ttl`/`standby_ttl_seconds` → `timings: Timings` 수정

### 5. 파일 변경 요약
- [ ] 새로 추가된 파일 목록에 `service/app/logic/ingest.py` 있는지 확인
- [ ] `sdk/llm_request/models.py`에 FileInput, FileKeyingMeta 추가 반영됐는지

### 7. 마이그레이션 영향
- [ ] 환경변수 표 최신화 (기본값 확인)
- [ ] 기존 코드 변경 예시: `file_paths` 제거, `files=` 추가, `timings=` 추가

### 8. 이 PR에 포함되지 않은 작업
- [ ] Cache stampede dedup → 현재 코드에 `dedup.py` 구현됨. "미구현"에서 "구현됨"으로 이동 필요

---

## PR 최종 수정 전 체크리스트

PR 올리기 전에 한 번 더 확인.

- [ ] 파일 인자 `files: list[FileInput]` 통일
- [ ] 반환 타입 `tuple[dict, dict]` 반영
- [ ] `timings: Timings` TypedDict 설명 추가
- [ ] cache stampede dedup "미구현" → "구현됨" 이동
- [ ] ingest.py 언급 추가 여부 결정
- [ ] 한줄 요약 최신화 (file_handler 자동화 + dedup 포함)
- [ ] Stage 5 file_manager 설명 현재 구현 기준으로 재작성
