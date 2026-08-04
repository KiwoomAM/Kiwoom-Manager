# File Manager (client)

파일 I/O + MongoDB 캐시·메타데이터를 **오케스트레이션하는 SDK**. file-handler(파일 저장/반환)와 mdb-handler(캐시 조회/메타데이터 기록)를 직접 호출하여 조합한다. 소비자(예: llm-request)가 `pip install` 후 `import file_manager`로 사용한다.

- **import해서 쓰는 SDK** → 이 패키지 (`file_manager`)
- **파일 I/O 서버** → `../file-handler` (FastAPI, :8600) — 순수 파일 저장/반환만 담당
- **MongoDB 서버** → mdb-handler (mdb-manager SDK 경유, caller="file-manager" 주입)

## 설치 / 사용
```bash
pip install -e packages/file-manager
```
```python
from file_manager import FileManagerHandler
fm = FileManagerHandler()            # 서버 주소 env: FILE_HANDLER_URL (기본 http://192.7.9.45:8600)
metadata = fm.build_metadata(file_name, file_mtime, file_size)
result = await fm.check_cache(metadata)
metadata["file_path"] = result["file_path"] if result["hit"] else await fm.upload_file(data, metadata)
file_bytes, meta = await fm.get_file(metadata["file_path"])
```

## 메서드
`build_metadata`(동기, 로컬 mime/extension 추출) · `check_cache`(mdb-handler 직접 조회) · `upload_file`(file-handler 저장 + mdb 메타데이터 기록) · `get_file`(mdb 메타 조회 + file-handler 바이너리 반환) · `register_or_get` · `register_from_bytes` · `register_from_path` · `materialize_to_tempfile` · `normalize_mtime`.

### 편의 메서드 (이슈 #81)
> `register_from_bytes(data, *, file_name, file_mtime) -> dict`·`register_from_path(path) -> dict`는 `build_metadata`+`register_or_get`을 한 번에 흡수하는 등록 래퍼다. `file_size`는 `len(data)`로 내부 계산하고, `register_from_path`는 경로에서 `file_name`·`file_mtime`을 도출하며 바이트 읽기를 `asyncio.to_thread`로 off-loop 처리한다. 반환·예외 계약은 `register_or_get`과 동일하며, `register_from_path`는 경로가 없으면 추가로 `FileNotFoundError`를 전파한다.

> `materialize_to_tempfile(data, *, suffix="") -> Path`(async context manager)는 바이트를 임시파일로 떨궈 `Path`를 yield하고 블록 종료 시(정상·예외 모두) `unlink`한다 — 경로를 요구하는 하류 추출기(pandas·PyMuPDF 등)에 넘길 때 쓴다. `async with fm.materialize_to_tempfile(data, suffix=".pdf") as p: ...`.

> `normalize_mtime(value) -> int`(staticmethod)는 int·float·숫자 문자열(소수점 포함)·ISO 8601 문자열을 int epoch seconds로 통일한다. 10^12 이상은 밀리초 epoch으로 보고 초로 환산하며, None·공백·변환 실패는 0으로 폴백한다.

> `register_or_get`은 같은 `(file_name, file_mtime, file_size)` 동시 호출을 **Redis standby(SETNX)** 로 **프로세스·워커 넘어** 직렬화한다 — 첫 요청만 업로드, 나머지는 대기 후 캐시 재사용. Redis 미연결/타임아웃 시 직접 업로드로 degrade(가용성 우선, 드물게 중복 가능). 의존: `kiwoom-redis-manager`(redis-handler).

파이프라인 명세는 [`../file-handler/file-manager-pipeline.md`](../file-handler/file-manager-pipeline.md) 참고.

## SERVICE_NAME (등록 주체)
등록 경로는 env `SERVICE_NAME`을 **강제**한다 — 설정돼 있으면 `file_cache` 메타의 `service_name` 필드에 기록하고, 없으면 `FMConfigError`. 순수 조회(`check_cache`/`get_file`)는 없어도 동작한다.

검사 지점이 **둘**인 데는 각각 이유가 있다(규칙 자체는 `_require_service_name()` 하나가 SSOT).
- `upload_file` 진입부 — 파일 저장(POST) **전에** 막는다. 저장 후 실패하면 메타 없는 orphan이 file-handler에 남는다.
- `register_or_get` 진입부 — 캐시 히트로 업로드를 타지 않는 경우에도 막는다. 업로드 안에만 가드가 있으면 히트가 나는 동안 설정 누락이 조용히 지나가고, 처음 보는 파일이 들어오는 순간 요청 도중에 터진다. llm-manager가 사용 시점이 아니라 요청 진입부(`runner.prepare`)에서 검증하는 것과 동형.

그래서 등록 계열 테스트는 `SERVICE_NAME`이 깔려 있어야 한다 — `tests/`의 autouse fixture `service_name_env`가 담당하고, 부재를 검증하는 테스트만 본문에서 `monkeypatch.delenv` 한다.

file-manager는 SDK라 자기 프로세스가 없다 — 이 env는 **SDK를 import한 서비스 프로세스**의 것이다(ETF 컨테이너면 `kiwoom-etf`). mdb-manager의 `X-Caller-Service`(SDK 정체성 = 항상 `file-manager`)와는 다른 축이니 혼동하지 말 것.

**반환 계약은 6필드 고정**이다 — `service_name`은 저장 문서에만 넣는다. `register_or_get`은 입력 메타에 `file_path`만 얹고, `get_file`은 저장 문서에서 `_PUBLIC_METADATA_FIELDS`만 골라 담는다(문서를 통째로 내보내면 옛 문서 6필드 / 새 문서 7필드로 **등록 시점에 따라 모양이 달라진다**). 소비처(Kiwoom-ESG `source_verify`)가 `StoredFile(**out)`으로 펼쳐 넣는데 `StoredFile`이 `extra="forbid"`라, 반환에 필드를 더하면 `ValidationError`로 깨진다.

마이그레이션 CLI(`scripts/migrate_smb_to_fm.py`)는 `--dry-run`이 아니면 루프 진입 전에 `SERVICE_NAME`을 한 번 검사한다 — `_process_one`의 blanket `except Exception`이 `FMConfigError`를 파일 단위 `error:` 문자열로 강등해, 전 항목이 실패한 매핑 JSON을 종료코드 0으로 남기는 것을 막는다.

## 예외
`FMError` · `FMConfigError` · `FMConnectionError` · `FMServiceError` (from `file_manager`).
