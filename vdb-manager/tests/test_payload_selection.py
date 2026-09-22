# -*- coding: utf-8 -*-
"""받을 payload 항목 고르기의 계약.

순회는 문서마다 저장된 정보를 통째로 끌어온다. 필요한 것이 파일 이름 하나뿐이어도
리포트 본문까지 함께 온다. 게이트웨이가 고르는 통로를 열었으므로(KiwoomAM/Kiwoom-Handler#76)
SDK 가 그 인자를 통과시킨다.

여기서 보는 것은 "인자가 요청 body 에 실려 나가는가" 다. 정말로 덜 받는지는 진짜
서버에 물어야 알고, 그것은 게이트웨이 쪽 e2e 가 본다.
"""

from typing import Any, Dict, List

import pytest

from vdb_manager.collection import CollectionClient
from vdb_manager.handler import VDBManager


class PagingClient:
    """호출마다 다른 응답을 돌려주는 더블.

    `conftest.FakeClient` 는 응답이 하나뿐이라 페이지네이션을 볼 수 없다. 순회는
    "다음 페이지를 어떻게 잇는가" 가 계약이므로 응답이 여럿이어야 한다.
    """

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def post(self, path: str, **kwargs: Any) -> Any:
        self.calls.append({"method": "POST", "path": path, **kwargs})
        return self.responses.pop(0)


def _page(points: List[Dict[str, Any]], next_offset: Any) -> Dict[str, Any]:
    return {"points": points, "next_offset": next_offset}


# ----------------------------------------------------------------------
# 인자가 실려 나가는가
# ----------------------------------------------------------------------


def test_스크롤은_기본으로_payload_전체를_받는다(make_collection):
    """기본값이 True 라 지금 호출자의 동작이 바뀌지 않는다."""
    collection, client = make_collection(_page([], None))

    collection.scroll_points(collection_name="c", limit=10)

    assert client.calls[0]["json"]["with_payload"] is True


def test_고른_항목_목록이_그대로_실린다(make_collection):
    collection, client = make_collection(_page([], None))

    collection.scroll_points(collection_name="c", with_payload=["source_pdf"])

    assert client.calls[0]["json"]["with_payload"] == ["source_pdf"]


def test_payload_가_필요_없으면_False_가_실린다(make_collection):
    collection, client = make_collection(_page([], None))

    collection.scroll_points(collection_name="c", with_payload=False)

    assert client.calls[0]["json"]["with_payload"] is False


def test_id_조회도_같은_인자를_받는다(make_collection):
    collection, client = make_collection({"points": [], "count": 0})

    collection.get_points(collection_name="c", ids=[1], with_payload=["source_pdf"])

    assert client.calls[0]["json"]["with_payload"] == ["source_pdf"]


# ----------------------------------------------------------------------
# 빈 것만 막는다
# ----------------------------------------------------------------------


def test_빈_목록은_왕복_전에_막는다(make_collection):
    """`[]` 는 "고르지 않음" 이 아니라 "고르려다 비어서 나온" 상태다.

    서버도 422 로 막지만 층이 다른 두 방어다. 여기서 막으면 호출 자리에서 알게 된다.
    """
    collection, client = make_collection(_page([], None))

    with pytest.raises(ValueError, match="with_payload"):
        collection.scroll_points(collection_name="c", with_payload=[])

    assert client.calls == [], "막았는데 요청이 나갔다"


def test_id_조회의_빈_목록도_막는다(make_collection):
    collection, client = make_collection({"points": [], "count": 0})

    with pytest.raises(ValueError, match="with_payload"):
        collection.get_points(collection_name="c", ids=[1], with_payload=[])

    assert client.calls == []


def test_공백뿐인_항목명은_SDK_가_판정하지_않는다(make_collection):
    """빈 것만 막고 형식은 판정하지 않는다.

    `"  "` 가 비었는지 알려면 `.strip()` 을 해야 하고 그것은 형식 판정이다. SDK 가
    서버 규칙을 흉내 내기 시작하면 서버가 규칙을 넓힐 때 SDK 만 옛 규칙으로 정당한
    요청을 막는다. 서버가 422 로 거부하며 항목 번호까지 짚어 준다.
    """
    collection, client = make_collection(_page([], None))

    collection.scroll_points(collection_name="c", with_payload=["  "])

    assert client.calls[0]["json"]["with_payload"] == ["  "]


# ----------------------------------------------------------------------
# id 조회는 서버가 준 봉투를 그대로 돌려준다
# ----------------------------------------------------------------------


def test_id_조회는_서버_응답을_그대로_돌려준다(make_collection):
    """알맹이만 꺼내면 서버가 그 봉투에 새 정보를 실을 때 SDK 가 조용히 가린다."""
    collection, _ = make_collection({"points": [{"id": 1}], "count": 1})

    got = collection.get_points(collection_name="c", ids=[1])

    assert got == {"points": [{"id": 1}], "count": 1}


# ----------------------------------------------------------------------
# 순회
# ----------------------------------------------------------------------


def test_순회는_페이지를_이어받아_전부_돌려준다():
    client = PagingClient(
        [
            _page([{"id": 1}, {"id": 2}], 2),
            _page([{"id": 3}], None),
        ]
    )
    collection = CollectionClient(client)  # type: ignore[arg-type]

    got = list(collection.iter_points(collection_name="c", limit=2))

    assert [p["id"] for p in got] == [1, 2, 3]
    assert [call["json"]["offset"] for call in client.calls] == [None, 2]


def test_순회는_이어받기_키가_0_이어도_멈추지_않는다():
    """`0` 은 실재하는 포인트 id 다.

    종료 판정을 falsy 로 하면 첫 페이지에서 멈춘다. 소비자가 이 함정을 주석으로
    적어 두어야 했던 자리이고, 그것을 삼키는 것이 이 메서드의 존재 이유다.
    """
    client = PagingClient(
        [
            _page([{"id": 9}], 0),
            _page([{"id": 0}], None),
        ]
    )
    collection = CollectionClient(client)  # type: ignore[arg-type]

    got = list(collection.iter_points(collection_name="c"))

    assert [p["id"] for p in got] == [9, 0], "0 을 falsy 로 읽어 첫 페이지에서 멈췄다"


def test_순회가_고른_항목을_모든_페이지에_싣는다():
    client = PagingClient([_page([{"id": 1}], 1), _page([], None)])
    collection = CollectionClient(client)  # type: ignore[arg-type]

    list(collection.iter_points(collection_name="c", with_payload=["source_pdf"]))

    assert [call["json"]["with_payload"] for call in client.calls] == [
        ["source_pdf"],
        ["source_pdf"],
    ]


def test_순회는_같은_자리를_두_번_받으면_멈추고_알린다():
    """이어받기 키가 나아가지 않으면 영원히 돈다.

    매일 도는 배치가 조용히 멈춰 서는 것보다 시끄럽게 실패하는 편이 낫다.
    """
    client = PagingClient([_page([{"id": 1}], 5), _page([{"id": 2}], 5)])
    collection = CollectionClient(client)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="이어받기"):
        list(collection.iter_points(collection_name="c"))


# ----------------------------------------------------------------------
# 바로가기
# ----------------------------------------------------------------------


def test_바로가기도_고른_항목을_넘긴다(monkeypatch):
    handler = VDBManager()
    client = PagingClient([_page([], None), {"points": [], "count": 0}])
    handler.collection = CollectionClient(client)  # type: ignore[arg-type]

    handler.scroll_points(collection_name="c", with_payload=["source_pdf"])
    handler.get_points(collection_name="c", ids=[1], with_payload=False)

    assert client.calls[0]["json"]["with_payload"] == ["source_pdf"]
    assert client.calls[1]["json"]["with_payload"] is False
