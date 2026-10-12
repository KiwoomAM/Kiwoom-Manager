# 용어 (CONTEXT)

SDK 여럿에 걸친 말만 적는다. 한 SDK 안의 말은 그 SDK 의 용어집이 맡는다 —
[winus-manager](winus-manager/CONTEXT.md) · [vdb-manager](vdb-manager/CONTEXT.md).
서버에서 일어나는 일의 말은 Kiwoom-Handler 의
[CONTEXT.md](https://github.com/KiwoomAM/Kiwoom-Handler/blob/main/CONTEXT.md) 가 주인이고, 여기서는
다시 정의하지 않고 가리키기만 한다 — 같은 뜻을 두 레포에 적으면 어긋난다.

## Language

**`auth_key`**:
winus·rdb·mdb-manager 가 요청마다 핸들러에 내는 키로, 핸들러의 **접근 등급**(admin·guest)을 정한다.
주지 않으면 SDK 가 admin 키를 싣는다 — 그래서 SDK 를 거친 호출은 모두 admin 이다. 빈 키는 admin 이
아니라 그대로 실려 핸들러가 거절한다.
_Avoid_: 인증 키, 자격증명 — 사람이 누구인지 확인하지 않는다. 인증으로 부르면 인증이 이미 있는 것으로
읽혀 진짜 인증(kiwoom-auth)이 미뤄진다. 이름의 `auth` 는 HTTP `Authorization` 헤더에 실린다는 뜻일 뿐이다.
