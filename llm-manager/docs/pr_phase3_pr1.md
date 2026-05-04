# PR — file-manager: `register_or_get` helper 신설 (Phase 3 PR 1)

> **Spec / Plan:** [packages/llm-request/docs/21.method1-deprecation-lazy-fetch.md](./21.method1-deprecation-lazy-fetch.md), [packages/llm-request/docs/22.phase3-plan.md](./22.phase3-plan.md)
> **다음 PR:** PR 2 — llm-request 의 Method 1 폐기 + lazy fetch (이 PR 머지 후 진행)

# 📝 개요

`FileManagerHandler` 에 `register_or_get(bytes_data, metadata)` 메서드 추가. `check_cache → miss 면 upload_file` 패턴을 한 줄로 처리하는 합성 헬퍼. 6 필드 dict (5 메타 + `file_path`) 반환.

Phase 3 의 prerequisite — agent 가 file-manager 등록 책임을 직접 가져갈 때 사용. agent 코드 boilerplate 제거 + 향후 로직 변경 시 한 곳만 갱신하면 모든 agent 자동 적용.

# 🔗 관련 이슈

(이슈 번호)

# ✅ 변경 사항

| 파일 | 변경 |
|------|------|
| `packages/file-manager/sdk/file_manager/handler.py` | `register_or_get` 메서드 추가 |
| `packages/file-manager/README.md` | API Reference 표 + `register_or_get` 사용 섹션 추가 |

## 메서드 시그니처

```python
async def register_or_get(self, bytes_data: bytes, metadata: dict) -> dict:
    """
    Args:
        bytes_data: 파일 바이트
        metadata: 5 필드 — file_name, file_size, file_mtime, mime_type, extension

    Returns:
        6 필드 dict — metadata + {"file_path": ...}
    """
```


# 🔄 호환성

추가 메서드만 — 기존 동작 변경 없음. Breaking change 없음.

`FileManagerHandler` 가 이미 `__init__.py` 에서 export 됐으므로 외부 사용자는 메서드 추가만으로 즉시 사용 가능 — 패키지 import 변경 없음.