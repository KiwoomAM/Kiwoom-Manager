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

## 예외
`FMError` · `FMConnectionError` · `FMServiceError` (from `file_manager`).
