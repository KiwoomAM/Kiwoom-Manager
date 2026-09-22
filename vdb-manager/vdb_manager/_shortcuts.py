# -*- coding: utf-8 -*-
"""`VDBManager` 바로가기의 단일 출처.

`VDBManager` 는 하위 클라이언트의 메서드를 같은 인자로 한 벌 더 갖는다. 소비자가
`handler.collection.count_points(...)` 대신 `handler.count_points(...)` 로 부를 수
있게 하는 편의 층이다. 일부만 빼면 "어떤 것은 `handler.` 로 되고 어떤 것은
`handler.collection.` 이어야 한다" 는 외워야 할 경계가 생기므로 미러링은 지킨다.

그 시그니처를 사람이 두 벌 적으면 한쪽이 뒤처지고, 뒤처진 인자는 예외도 경고도 없이
**조용히 기본값으로 떨어진 채** 나간다. 그래서 사람이 관리하는 것은 이 표뿐이고
메서드 본문은 `scripts/generate_shortcuts.py` 가 쓴다 (`docs/adr/0003`).

표에 없는 것은 `NOT_MIRRORED` 에 이유와 함께 등록해야 한다. 이유 없는 빈칸은 다음
사람이 실수인지 결정인지 구별할 수 없다.
"""

from __future__ import annotations

# (바로가기 이름, 하위 클라이언트 속성, 원본 메서드 이름)
SHORTCUTS: tuple[tuple[str, str, str], ...] = (
    ("embed", "dense", "embed"),
    ("embed_batch", "dense", "embed_batch"),
    ("get_embedding_dimension", "dense", "get_embedding_dimension"),
    ("sparse_embed", "sparse", "embed"),
    ("sparse_embed_batch", "sparse", "embed_batch"),
    ("hybrid_embed", "hybrid", "embed"),
    ("hybrid_embed_batch", "hybrid", "embed_batch"),
    ("rerank", "reranker", "rerank"),
    ("hybrid_search", "hybrid", "search"),
    ("dense_search", "dense", "search"),
    ("sparse_search", "sparse", "search"),
    ("create_collection", "collection", "create"),
    ("get_collection_info", "collection", "info"),
    ("delete_collection", "collection", "delete"),
    ("upsert_points", "collection", "upsert_points"),
    ("get_points", "collection", "get_points"),
    ("delete_points", "collection", "delete_points"),
    ("scroll_points", "collection", "scroll_points"),
    ("iter_points", "collection", "iter_points"),
    ("count_points", "collection", "count_points"),
    ("has_points", "collection", "has_points"),
    ("set_payload", "collection", "set_payload"),
    ("overwrite_payload", "collection", "overwrite_payload"),
    ("delete_payload_keys", "collection", "delete_payload_keys"),
    ("clear_payload", "collection", "clear_payload"),
    ("create_payload_index", "collection", "create_payload_index"),
    ("list_payload_indexes", "collection", "list_payload_indexes"),
    ("delete_payload_index", "collection", "delete_payload_index"),
)

# (옛 이름, 지금 이름). v2 시절 이름을 쓰던 소비자를 위해 남긴다.
ALIASES: tuple[tuple[str, str], ...] = (
    ("embed_text", "embed"),
    ("sparse_encode", "sparse_embed"),
    ("sparse_encode_batch", "sparse_embed_batch"),
)

# 미러링하지 않는 것과 그 이유. `<하위 클라이언트>.<메서드>` 로 적는다.
NOT_MIRRORED: dict[str, str] = {
    "collection.is_ready": "status() 가 한 번의 왕복으로 넷을 다 준다 — 다섯을 각각 열면 소비자가 네다섯 번 왕복하게 유도된다",
    "dense.is_ready": "위와 같다",
    "sparse.is_ready": "위와 같다",
    "hybrid.is_ready": "위와 같다",
    "reranker.is_ready": "위와 같다",
    "dense.get_dimension": "get_embedding_dimension 의 별칭. 정규 이름만 미러링한다",
    "dense.embed_text": "embed 의 별칭. 정규 이름만 미러링한다",
    "sparse.encode": "embed 의 별칭. 정규 이름만 미러링한다",
    "sparse.encode_batch": "embed_batch 의 별칭. 정규 이름만 미러링한다",
    "collection.create_collection": "create 의 별칭. handler 쪽 이름이 이미 create_collection 이다",
    "collection.get_collection_info": "info 의 별칭. handler 쪽 이름이 이미 get_collection_info 이다",
    "collection.delete_collection": "delete 의 별칭. handler 쪽 이름이 이미 delete_collection 이다",
}
