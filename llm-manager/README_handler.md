# LLM Handler Usage

> ⚠️ **클라이언트 SDK 이관됨 (ISSUE #656):** 이 문서의 `llm_handler` / `llm_handler.aio` 클라이언트는
> manager(llm-request)로 흡수되어 `llm_request._internal.handler_client` 가 됐다. 실제 사용은
> `from llm_request import call, stream, embed, session` / `from llm_request.aio import ...` 로 한다.
> 아래 예시의 호출 시그니처·와이어 동작은 그대로 유효(이 서버가 구현하는 계약)하지만 import 경로만 다르다.
> llm-handler 패키지는 이제 **Service(서버)만** 제공한다.

llm_handler 랑 llm_handler.aio 한 프로세스에 import 하면 **죽음.**

자세한 구조 및 개선 사항은 `docs/architecture.md, bottlenecks.md` 참고. 실제 사용케이스는 `quickstarts/` 참고.

## 설치 및 설정

**Docker 환경 (권장):**

```dockerfile
RUN GITHUB_TOKEN=$(cat /tmp/github_token) && \
    REPO="git+https://x-access-token:${GITHUB_TOKEN}@github.com/KIWOOMxSNU/Kiwoom-Rag.git@main" && \
    pip install --no-cache-dir \
        "kiwoom-llm-handler @ ${REPO}#subdirectory=packages/llm-handler/sdk" && \
    rm -f /tmp/github_token
```

**로컬 개발 (editable):**

```bash
# Kiwoom-Rag 레포 클론 후
pip install -e packages/llm-handler/sdk
```

```python
import llm_handler

# 방법 1: 환경변수 (권장) \ main 서버에서는 localhost가 아니라 main 컴 ip
# export LLM_HANDLER_URL=http://localhost:8100

# 방법 2: 코드에서 직접 설정
llm_handler.configure(base_url="http://localhost:8100")
```

### 동기 vs 비동기

| 모듈 | import | 용도 |
| ------ | ------ | ------ |
| `llm_handler` | `from llm_handler import call` | 스크립트, 노트북, 테스트, 배치 작업 |
| `llm_handler.aio` | `from llm_handler.aio import call` | **FastAPI 등 실서비스** (권장) |

함수 이름과 파라미터는 **완전히 동일**하고, 비동기는 `await`만 추가.
실서비스에서 동기 함수를 쓰면 이벤트 루프를 블로킹하여 동시 처리 성능이 저하된다.
각 함수의 비동기 사용법은 [8. 비동기 (Async)](#8-비동기-async) 참고.

> 아래 예시는 동기(`llm_handler`)로 작성되어 있으나, 비동기 환경에서는 `llm_handler.aio`에서 import 후 `await`를 붙이면 된다.

---

## 1. 기본 호출 — `call()`

### 가장 단순한 호출

```python
from llm_handler import call

response = call("대한민국의 수도는?")
print(response.text)

# 대한민국의 수도는 서울입니다.
```

### 시스템 프롬프트 설정

```python
response = call(
    "자기소개를 해줘",
    system="당신은 키움증권의 ESG 평가 전문 AI입니다. 간결하게 답변하세요.",
)
print(response.text)

# 안녕하세요. 저는 키움증권 ESG 평가 AI입니다.
# 기업의 환경(E), 사회(S), 거버넌스(G) 요소를 분석하여 투자 의사결정을 지원합니다.
```

### 온도 및 토큰 제한

```python
# 결정적 응답 (항상 같은 결과)
response = call("1+1은?", temperature=0, max_tokens=50)
print(response.text)  # "2"

# 창의적 응답
response = call("1+1은?", temperature=1.5, max_tokens=200)
print(response.text)  # "수학적으로는 2이지만, 시너지 관점에서는..."
```

### 토큰 사용량 확인

```python
response = call("안녕하세요")
print(response.usage)

# input_tokens=5 output_tokens=12 total_tokens=17 thinking_tokens=None
# cached_tokens=None tool_use_tokens=None
```

Thinking 모드 사용 시 `thinking_tokens`에 사고 토큰 수가 포함된다:

```python
response = call("복잡한 수학 문제를 풀어줘", thinking="high")
print(response.usage)

# input_tokens=15 output_tokens=120 total_tokens=1535 thinking_tokens=1400
```

Context Cache 사용 시 `cached_tokens`로 캐시 활용량 추적:

```python
response = call("질문", cached_content=cache.name)
print(response.usage)

# input_tokens=10 output_tokens=80 total_tokens=120 thinking_tokens=30
# cached_tokens=7913 tool_use_tokens=None
```

> `total_tokens` 검산: `input + output + thinking + tool_use` (cached 미포함).
> `input_tokens`는 cached 미포함 — 실제 모델이 본 입력은 `input_tokens + cached_tokens`.
> ⚠️ `cached_tokens`는 Gemini docs와 달리 "이번 호출 활용량"이 아니라 사용된 캐시 객체의 총 크기와 동일하게 반환됨.

---

## 2. 멀티턴 대화

### 수동 이력 관리 — `history=`

```python
response = call(
    "2번에 대해 자세히 설명해줘",
    history=[
        {"role": "user", "content": "파이썬의 장점 3가지를 알려줘"},
        {"role": "model", "content": "1. 간결한 문법 2. 풍부한 라이브러리 3. 커뮤니티"},
    ],
)
print(response.text)

# 파이썬의 두 번째 장점인 '풍부한 라이브러리'에 대해 설명드리겠습니다.
# 파이썬은 NumPy, Pandas, TensorFlow 등 거의 모든 분야에 검증된 라이브러리가 있어...
```

### 자동 이력 관리 — `session()`

이전 대화를 매번 직접 전달하기 번거로울 때:

```python
from llm_handler import session

sess = session(system="금융 분석 전문가입니다.")

r1 = sess.call("삼성전자 주가 동향을 분석해줘")
print(r1.text)

# "삼성전자는 최근 반도체 업황 회복에 따라..."

r2 = sess.call("원인이 뭘까?")  # 이전 대화를 자동으로 기억
print(r2.text)

# "앞서 말씀드린 주가 상승의 주요 원인은..."

# 이력 확인
print(sess.get_history())

# [{"role": "user", "content": "삼성전자 주가..."}, {"role": "model", "content": "삼성전자는..."}, ...]

# 이력 초기화
sess.clear()
```

---

## 3. 스트리밍 — `stream()`

응답이 생성되는 즉시 청크 단위로 수신:

```python
from llm_handler import stream

for chunk in stream("ESG 평가 기준을 설명해줘"):
    print(chunk.text, end="", flush=True)

# ESG는 환경(Environment), 사회(Social), 거버넌스(Governance)의 약자로...
# (한 글자씩 실시간 출력)
```

전체 텍스트만 필요하면 `call()`을 쓰면 된다. `stream()`은 실시간 출력이 필요할 때만 사용.

---

## 4. 구조화된 출력 — `output=`, `schema=`

### JSON 출력 — dict schema

```python
import json

response = call(
    "대한민국의 수도와 인구를 알려줘",
    schema={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "population": {"type": "integer"},
        },
    },
)
data = json.loads(response.text)
print(data)

# {"city": "서울", "population": 9700000}
```

### JSON 출력 — Pydantic 모델

```python
from pydantic import BaseModel

class StockAnalysis(BaseModel):
    company: str
    sentiment: str
    confidence: float
    summary: str

response = call("삼성전자 실적 분석해줘", schema=StockAnalysis)
data = json.loads(response.text)
print(data)

# {
#   "company": "삼성전자",
#   "sentiment": "positive",
#   "confidence": 0.85,
#   "summary": "반도체 업황 회복과 AI 수요 증가로 실적 개선 전망"
# }
```

### Enum 분류

```python
from enum import Enum

class Sentiment(Enum):
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    NEUTRAL = "Neutral"

response = call("키움증권의 2024년 실적은 매우 좋았다.", schema=Sentiment)
print(response.text)

# Positive
```

### schema 없이 output만 사용

```python
# JSON 출력 강제 (스키마 없이 — 구조는 프롬프트로 지정)
response = call(
    "한국 5대 도시의 이름과 인구를 JSON으로 알려줘",
    output="json",
)
print(response.text)

# {"cities": [{"name": "서울", "population": 9700000}, {"name": "부산", "population": 3400000}, ...]}
```

`output="json"`은 응답 형식만 JSON으로 강제한다. 구조(key-value)를 정확히 제어하려면 `schema=`를 사용.

---

## 5. Tool Calling (Function Calling)

### Function Declaration — 함수 정의 + 호출

모델이 필요한 함수를 선택하고 인자를 생성. 실제 **함수 실행은 호출자(에이전트)가 직접 수행**:

```python
tools = [
    {
        "name": "get_stock_price",
        "description": "주식의 현재 가격을 조회합니다",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
            },
            "required": ["ticker"],
        },
    },
]

response = call("삼성전자 주가 알려줘", tools=tools)

# function call 응답: text는 None, function_calls에 호출 정보
print(response.text)            # None
print(response.function_calls)  # [FunctionCall(name="get_stock_price", args={"ticker": "005930"}, id="...")]
```

### Multi-turn — 함수 실행 결과 전달

에이전트가 함수를 실행한 뒤, 결과를 `function_response`로 전달하면 최종 텍스트 응답 생성:

```python
fc = response.function_calls[0]

# 에이전트가 실제 함수 실행
result = {"price": 87500, "currency": "KRW", "change": "+1.2%"}

# 함수 결과를 multi-turn으로 전달
final = call(
    "삼성전자 주가 알려줘",
    tools=tools,
    history=[
        {"role": "user", "content": "삼성전자 주가 알려줘"},
        {"role": "model", "content": [{"type": "text", "text": ""}]},
        {"role": "tool", "content": [
            {"type": "function_response", "function_response": {
                "name": fc.name,
                "response": result,
            }}
        ]},
    ],
)
print(final.text)  # "삼성전자 현재 주가는 87,500원으로 전일 대비 1.2% 상승했습니다."
```

### tool_config — Function Calling 모드

```python
# 반드시 함수를 호출하도록 강제
response = call("삼성전자에 대해 알려줘", tools=tools, tool_config="any")
```

| 모드 | 동작 |
| --- | --- |
| `"auto"` | 모델이 판단 (`tool_config=None` 과 동일한 native 기본 동작) |
| `"any"` | 반드시 함수 호출 |
| `"none"` | 함수 호출 금지 (텍스트만) |
| `"validated"` | AUTO 처럼 자율 선택하되, 호출 시 `allowed_function_names` 화이트리스트만 허용 (Gemini 3) |

### Function Declaration + Output Schema

Tool calling과 구조화된 출력을 **동시에 사용** 가능 (Gemini 3 모델):

```python
# 모델이 함수를 안 부르고 직접 답할 때 → JSON schema를 따르도록 강제
response = call(
    "삼성전자 현재 주가가 얼마야?",
    tools=tools,
    output="json",
    schema={"type": "object", "properties": {"answer": {"type": "string"}}},
)

if response.function_calls:
    print("함수 호출:", response.function_calls)
else:
    print("직접 답변 (JSON):", response.text)
```

FunctionDeclaration에 `response_json_schema`를 추가하면, 함수 반환값 구조를 모델에게 알려줄 수 있다:

```python
tools = [{
    "name": "get_stock_price",
    "description": "주가 조회",
    "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}},
    "response_json_schema": {  # 함수 반환값 구조
        "type": "object",
        "properties": {"price": {"type": "number"}, "currency": {"type": "string"}},
    },
}]
```

### Gemini Built-in Tools

Gemini 자체 내장 도구. FunctionDeclaration과 달리 **Gemini가 실행까지 직접 처리**:

| Tool | 용도 | 전달 방식 |
| ------ | ------ | ---------- |
| **google_search** | 실시간 웹 검색 (Grounding) | `{"google_search": {}}` |
| **code_execution** | Python 코드 작성 및 실행 | `{"code_execution": {}}` |
| **url_context** | URL 페이지 내용 읽기 | `{"url_context": {}}` |
| **google_maps** | 장소/길찾기/로컬 정보 | `{"google_maps": {}}` |
| **file_search** | 문서 RAG (인덱싱 + 시맨틱 검색) | `{"file_search": {"file_search_store_names": [...]}}` |

```python
# Google Search — 실시간 정보 기반 답변
response = call("오늘 코스피 지수 알려줘", tools=[{"google_search": {}}])
print(response.text)
```

FunctionDeclaration과 Built-in Tool을 **혼합 사용** 가능:

```python
response = call(
    "삼성전자 현재 주가를 검색하고, get_stock_price 함수로도 조회해줘",
    tools=[
        {"name": "get_stock_price", "description": "주가 조회", "parameters": {...}},
        {"google_search": {}},
    ],
)
```

Gemini 3 부터 혼합 paradigm 응답을 받으려면 `tool_config.include_server_side_tool_invocations=True` opt-in 이 필요한데, handler 가 `tools` 안에 두 종류가 섞여있음을 감지하면 **자동으로 활성화**하므로 호출자는 별도 설정 불필요.

### Server-side Tool 응답 — `tool_calls` / `grounding` / `code_blocks`

Gemini server-side 도구 (google_search, code_execution, url_context, file_search) 결과는 다음 필드로 노출:

| 필드 | 의미 | 등장 시점 |
|------|------|----------|
| `response.tool_calls` | server-side tool 호출 흔적 (`tool_type` + `args` + `id`) | Gemini 3 mixed paradigm 시 |
| `response.tool_responses` | tool_call.id 와 짝 (`response` dict) | 위와 동일 |
| `response.grounding` | google_search 인용 메타데이터 (`queries`, `chunks`, `supports`) | 최종 text emit 시 |
| `response.code_blocks` | code_execution 이 생성한 코드 (`language`, `code`) | code_execution 도구 사용 시 |
| `response.code_results` | code 실행 결과 (`outcome`, `output`) | 위와 동일 |

```python
resp = call("오늘 코스피 지수 알려줘", tools=[{"google_search": {}}])
if resp.grounding:
    for chunk in resp.grounding.chunks:
        print(f"{chunk.title}: {chunk.uri}")
```

⚠️ **`grounding.chunks[].uri` 는 vertexai redirect URL** 이고 **수시간~1일 내 만료** 됨. 영구 저장 시 `title` + 캡처 시각 위주로 활용 권장.

### Mixed paradigm round-trip — `Session.send_tool_results()`

function_decl + google_search 같은 mixed paradigm 에서 client 가 함수 실행 결과를 회신할 때:

```python
session = llm_handler.session(model="gemini-3.5-flash")
resp1 = session.call(
    "삼성전자 주가 + 영업이익 비율 계산해줘",
    tools=[
        {"name": "get_stock_price", "description": "...", "parameters": {...}},
        {"google_search": {}},
    ],
)

# 사용자 정의 함수 실행
fc = resp1.function_calls[0]
result = my_get_stock_price(**fc.args)

# tool 결과 회신 — Session 이 history 와 role 을 자동 관리
resp2 = session.send_tool_results([(fc.id, fc.name, result)])
print(resp2.text)             # 최종 답변 (검색 + 계산 통합)
print(resp2.grounding.chunks) # 출처
```

내부적으로 `Session` 은 `response.assistant_message` (role="model" + 모든 server-side part + thought_signature 통째 보존) 를 history 에 자동 echo 하므로, **사용자 코드는 일반 function calling 과 동일** — mixed paradigm 임을 의식할 필요 없음.

### Advanced 사용자 — `call(messages=...)` 명시 path

직접 history 를 통제하고 싶다면 `messages: list[Content]` 파라미터로 전체 대화를 명시. `contents` / `history` 와 mutually exclusive:

```python
from llm_handler import call, Content, ContentPart, FunctionResponse

# Turn 1
r1 = call("삼성전자 주가 + 영업이익", tools=[func_decl, {"google_search": {}}])
fc = r1.function_calls[0]

# Turn 2 — messages 명시, role 실수 차단 + type-safe
r2 = call(
    messages=[
        Content(role="user", content="삼성전자 주가 + 영업이익"),
        r1.assistant_message,  # role="model" + parts (Content 객체 그대로)
        Content(role="tool", content=[
            ContentPart(
                type="function_response",
                function_response=FunctionResponse(
                    name=fc.name, response={"price": 87500}, id=fc.id),
            ),
        ]),
    ],
    tools=[func_decl, {"google_search": {}}],
)
```

| 형태 | 의미 | mutually exclusive |
| --- | --- | --- |
| `call(contents="...")` | 단일 새 user 발화 | with `messages` |
| `call(contents="...", history=[...])` | 새 발화 + dict 형식 이전 history | with `messages` |
| `call(messages=[Content, ...])` | 전체 대화 명시 (advanced) | with `contents`/`history` |

→ 두 path 가 동시에 들어가거나 둘 다 없으면 SDK 단에서 `ValueError`.

---

## 6. Thinking 모드

복잡한 추론에 Gemini의 사고 기능을 활성화:

```python
response = call(
    "23 x 47 x 89를 계산하고 소수인지 판별해줘",
    thinking="medium",
)
print(response.text)

# 23 × 47 = 1081
# 1081 × 89 = 96,209

# 96,209의 소수 판별:
# - 96,209 = 23 × 47 × 89
# - 3개의 소인수로 분해되므로 소수가 아닙니다.
```

| 레벨 | 사고 깊이 | 토큰 소모 |
|------|----------|----------|
| `"minimal"` | 최소 | 적음 |
| `"low"` | 낮음 | |
| `"medium"` | 중간 | |
| `"high"` | 깊음 | 많음 |

### 기본값 (`thinking=None` 일 때 모델별 동작)

SDK 가 `thinking` 인자를 명시하지 않으면 (`None`) Gemini 백엔드의 모델별 default 가 적용됨:

| 모델 | 기본 동작 | 비용 영향 |
|------|----------|----------|
| **Gemini 3 Flash/Pro** | **`HIGH` 자동 활성** | thinking 토큰 대량 사용 (수백~수천), latency ↑ |

> **Gemini 3 사용 시 비용 통제** — 명시적으로 `thinking="minimal"` 또는 `"low"` 보내지 않으면 모든 호출에서 thinking 토큰이 자동 소비됨. 단순 분류/추출 작업에는 `thinking="minimal"` 권장.

---

## 7. 파일 기반 질의

파일을 포함한 멀티파트 입력은 `contents` 리스트로 통합되었다.
텍스트, `FileRef`, `Part` 객체를 하나의 리스트에 조합하여 전달한다.

> MIME 타입 자동 감지, 정규화, Gemini 지원 포맷 목록은 [MIME 타입 처리](#mime-type-handling) 참고.

### 방법 1: `upload_file()` + `FileRef` (대용량 파일)

Gemini File API에 파일을 업로드하고, 반환된 `FileRef`를 `contents`에 포함:

```python
from llm_handler import call, upload_file

# 1. 파일 업로드 (MIME은 확장자에서 자동 감지)
file_ref = upload_file("/path/to/report.pdf", display_name="ESG보고서")
print(file_ref.uri)    # "https://generativelanguage.googleapis.com/..."
print(file_ref.state)  # "ACTIVE"

# 2. contents 리스트에 FileRef와 텍스트를 함께 전달
response = call(
    [file_ref, "이 보고서의 핵심 내용을 3줄로 요약해줘"],
)
print(response.text)

# 1. 탄소배출량 전년 대비 15% 감축 달성
# 2. 임직원 다양성 지수 30% 향상
# 3. 사외이사 비율 60% 유지, 종합 등급 AA
```

### 방법 2: `Part.from_uri()` (공개 URL)

이미 접근 가능한 URI가 있을 때. `mime_type` 필수:

```python
from llm_handler import call
from llm_handler import Part

response = call([
    Part.from_uri(
        "https://example.com/chart.png",
        mime_type="image/png",
    ),
    "이 차트를 분석해줘",
])
print(response.text)
```

### 방법 3: `Part.from_bytes()` (인라인 바이너리)

메모리에 있는 바이너리 데이터를 직접 전달. MIME은 `filetype` 라이브러리로 자동 감지:

```python
from llm_handler import call
from llm_handler import Part

with open("/path/to/image.jpg", "rb") as f:
    image_data = f.read()

response = call([
    Part.from_bytes(image_data),  # mime_type 자동 감지
    "이 이미지에 무엇이 보이나요?",
])
print(response.text)
```

### 방법 4: `Part.from_file()` (로컬 파일 -- 가장 간편)

로컬 파일 경로만 넘기면 읽기 + MIME 감지를 자동 처리:

```python
from llm_handler import call
from llm_handler import Part

response = call([
    Part.from_file("/path/to/financial_report.pdf"),
    "이 재무제표의 영업이익 추이를 분석해줘",
])
print(response.text)
```

### `media_resolution` 옵션

이미지/동영상 입력 시 해상도를 지정하여 토큰 사용량을 조절할 수 있다:

```python
response = call(
    [Part.from_file("/path/to/highres_chart.png"), "분석해줘"],
    media_resolution="low",  # "low" | "medium" | "high" | 
)
```

#### 미디어 유형별 토큰 수 (Gemini 3 기준)

| MediaResolution        | 이미지 | 동영상     | PDF            |
|------------------------|--------|------------|----------------|
| `UNSPECIFIED` (기본값) | 1120   | 70         | 560            |
| `"low"`                | 280    | 70         | 280 + 텍스트   |
| `"medium"`             | 560    | 70         | 560 + 텍스트   |
| `"high"`               | 1120   | 280        | 1120 + 텍스트  |

#### 기본값 (`media_resolution=None` 일 때)

SDK 가 `media_resolution` 인자를 명시하지 않으면 Gemini 가 `UNSPECIFIED` 로 처리 — **이미지 1 장당 1120 토큰, PDF 1 페이지당 560 토큰**. 텍스트 전용 호출은 영향 없지만 이미지/PDF 다수 처리 시 비용 차이 큼.

> PDF/일반적인 동영상은 "medium", 이미지/복합적 동영상은 "high" 가 권장됨.

### 복합 입력 예시

여러 파일과 텍스트를 자유롭게 조합:

```python
response = call([
    Part.from_file("/path/to/q1_report.pdf"),
    Part.from_file("/path/to/q2_report.pdf"),
    "위 두 분기 보고서를 비교 분석해줘",
])
```

> `contents`에 문자열(`str`)만 전달하면 기존과 동일하게 동작한다: `call("질문")`

### 파일 관리 — `delete_file()`, `get_file()`, `list_files()`

업로드한 파일의 조회, 목록 확인, 삭제:

```python
from llm_handler import upload_file, get_file, list_files, delete_file

# 파일 업로드
ref = upload_file("/path/to/report.pdf", display_name="ESG보고서")
print(ref.display_name)       # "ESG보고서"
print(ref.size_bytes)         # 1234567
print(ref.expiration_time)    # "2026-04-18 12:00:00+00:00" (48시간 후)

# 파일 존재 확인 (없으면 None)
info = get_file(ref.name)
if info is None:
    print("파일이 만료됨 — 재업로드 필요")

# 전체 파일 목록
for f in list_files():
    print(f"{f.display_name}: {f.name}")

# 파일 삭제 (이미 삭제된 경우에도 에러 없음)
delete_file(ref.name)
```

---

## 8. 비동기 (Async)

FastAPI 같은 async 환경에서는 `llm_handler.aio`를 사용한다.
함수 이름과 파라미터는 동기와 **완전히 동일**하고, `await`만 추가.

### 기본 호출

```python
from llm_handler.aio import call

response = await call("안녕하세요")
print(response.text)
```

### 스트리밍

```python
from llm_handler.aio import stream

async for chunk in stream("ESG 평가해줘"):
    print(chunk.text, end="")
```

### 구조화된 출력

```python
from llm_handler.aio import call

response = await call("분석해줘", schema=StockAnalysis)
data = response.to_dict()
```

### Tool Calling

```python
from llm_handler.aio import call

response = await call("삼성전자 주가", tools=[{"google_search": {}}])
print(response.text)
```

### 세션

```python
from llm_handler.aio import session

sess = session(system="금융 분석가입니다.")
r1 = await sess.call("삼성전자 분석해줘")
r2 = await sess.call("더 자세히")  # 이전 대화 기억
```

### 파일 업로드

```python
from llm_handler.aio import upload_file, call

file_ref = await upload_file("/path/to/report.pdf")
response = await call([file_ref, "요약해줘"])
```

### 파일 관리

```python
from llm_handler.aio import upload_file, get_file, list_files, delete_file

ref = await upload_file("/path/to/report.pdf")
info = await get_file(ref.name)       # FileRef | None
files = await list_files()             # list[FileRef]
await delete_file(ref.name)            # 404도 안전
```

### Context Cache

```python
from llm_handler.aio import create_cache, call, delete_cache, get_cache, update_cache

cache = await create_cache(large_text, model="gemini-3.5-flash", ttl=3600)
response = await call("분석해줘", cached_content=cache.name)
info = await get_cache(cache.name)          # CacheRef
updated = await update_cache(cache.name, ttl=7200)  # TTL 연장
await delete_cache(cache.name)
```

### FastAPI 연동 예시

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from llm_handler.aio import call, stream

app = FastAPI()

@app.post("/chat")
async def chat(message: str):
    response = await call(message, service_name="chatbot")
    return {"reply": response.text}

@app.post("/chat/stream")
async def chat_stream(message: str):
    async def generate():
        async for chunk in stream(message, service_name="chatbot"):
            yield chunk.text
    return StreamingResponse(generate(), media_type="text/plain")
```

---

## 9. 에러 핸들링

```python
from llm_handler import call
from llm_handler.exceptions import HandlerConnectionError, HandlerError

try:
    response = call("안녕")
except HandlerConnectionError:
    # Handler 서비스에 도달 불가 (다운 / 네트워크 단절)
    ...
except HandlerError as e:
    # Handler 가 에러 응답 (6 sub-class 모두 catch)
    print(f"[{e.error_type}] {e.message} ({e.status_code})")
```

`HandlerError` 는 6 sub-class 로 분류됨 — `ValidationError`, `ContentBlockedError`,
`TransportError`, `ProviderError`, `HandlerOpsError`, `HandlerInternalError`.
세밀한 분기, 시나리오별 어떤 에러가 발생하는지, 예외 계층 등은 별도 문서 참고:

> **자세한 에러 핸들링 가이드**: [docs/error-handling.md](docs/error-handling.md)

---

## 10. Context Cache (대용량 문서)

대량 텍스트를 캐싱하여 반복 호출 시 비용 절감 (최소 토큰: Flash 1,024 / Pro 4,096):

```python
from llm_handler import create_cache, call, delete_cache, get_cache, update_cache, list_caches

# 1. 캐시 생성 — call()과 동일한 contents 포맷
cache = create_cache(
    large_text,  # str | list[str | FileRef | Part]
    model="gemini-3.5-flash",
    system="ESG 전문가로서 답변하세요.",
    display_name="esg-report",
    ttl=3600,  # 1시간
)
print(cache.total_token_count)  # 캐시된 토큰 수

# 2. 캐시 참조하여 여러 번 질문 (토큰 비용 절감)
r1 = call("환경 항목 평가해줘", cached_content=cache.name)
r2 = call("거버넌스 항목 평가해줘", cached_content=cache.name)

# 3. 캐시 관리
info = get_cache(cache.name)              # 상세 조회
print(info.expire_time)
updated = update_cache(cache.name, ttl=7200)  # TTL 2시간으로 연장
all_caches = list_caches()                # 전체 목록

# 4. 정리
delete_cache(cache.name)
```

---

## 11. 텍스트 임베딩 — `embed()`

텍스트를 벡터로 변환. VDB 파이프라인, 유사도 검색, 분류 등에 활용:

### 기본 호출

```python
from llm_handler import embed

result = embed("키움증권의 2024년 실적은?")
print(len(result.embeddings[0].values))  # 3072 (기본 차원)
print(result.embeddings[0].values[:5])   # [0.013, -0.028, 0.041, ...]
```

### 배치 임베딩

여러 텍스트를 한 번에 임베딩. 입력 순서 = 출력 순서:

```python
result = embed(["키움증권 실적", "삼성전자 배당 정책", "코스피 전망"])
print(len(result.embeddings))  # 3

for i, emb in enumerate(result.embeddings):
    print(f"[{i}] 차원: {len(emb.values)}")
```

### task_type — 용도별 최적화

임베딩 목적에 맞는 task_type을 지정하면 품질이 향상된다:

```python
# 검색 시스템: 쿼리와 문서를 다르게 임베딩
query_emb = embed("키움증권 실적", task_type="RETRIEVAL_QUERY")
doc_emb = embed("키움증권은 2024년...", task_type="RETRIEVAL_DOCUMENT", title="키움 실적 리포트")
```

| task_type | 용도 |
|-----------|------|
| `"RETRIEVAL_QUERY"` | 검색 쿼리 |
| `"RETRIEVAL_DOCUMENT"` | 문서 인덱싱 (`title` 사용 가능) |
| `"SEMANTIC_SIMILARITY"` | 유사도 비교 |
| `"CLASSIFICATION"` | 텍스트 분류 |
| `"CLUSTERING"` | 클러스터링 |
| `"QUESTION_ANSWERING"` | Q&A |
| `"FACT_VERIFICATION"` | 팩트 체크 |
| `"CODE_RETRIEVAL_QUERY"` | 코드 검색 |

### 차원 축소

기본 3072 차원이 너무 크면 `output_dimensionality`로 줄일 수 있다:

```python
result = embed("텍스트", output_dimensionality=768)
print(len(result.embeddings[0].values))  # 768
```

> 차원 축소 시 L2 정규화 권장. Matryoshka Representation Learning 덕분에 768 차원에서도 성능 저하가 적다.

### 통계 확인

```python
result = embed("안녕하세요")
stats = result.embeddings[0].statistics
if stats:
    print(f"토큰 수: {stats.token_count}, 잘림: {stats.truncated}")
```

> `statistics`는 Gemini API 응답에 따라 `None`일 수 있다.

### 모델 선택

```python
# 기본값: gemini-embedding-001 (텍스트 전용, 안정)
result = embed("텍스트", model="gemini-embedding-001")
```

| 모델 | 입력 | 최대 토큰 | 차원 |
|------|------|----------|------|
| `gemini-embedding-001` (기본) | 텍스트 | 2,048 | 128~3,072 |
| `gemini-embedding-2-preview` | 멀티모달 | 8,192 | 128~3,072 |

### 비동기

```python
from llm_handler.aio import embed

result = await embed(["text1", "text2"], task_type="RETRIEVAL_DOCUMENT")
```

---

## API Reference

### 함수 목록

| 함수 | 용도 | 반환 타입 |
| ------ | ------ | ---------- |
| `call(contents, ...)` | 단일 응답 | `LLMResponse` |
| `stream(contents, ...)` | 스트리밍 | `Iterator[StreamChunk]` |
| `embed(contents, ...)` | 텍스트 임베딩 | `EmbedResponse` |
| `session(...)` | 멀티턴 세션 | `Session` |
| `upload_file(path, ...)` | 파일 업로드 | `FileRef` |
| `delete_file(name)` | 파일 삭제 (404 멱등) | `None` |
| `get_file(name)` | 파일 조회 (없으면 None) | `FileRef \| None` |
| `list_files()` | 파일 목록 | `list[FileRef]` |
| `create_cache(...)` | 캐시 생성 | `CacheRef` |
| `delete_cache(name)` | 캐시 삭제 | `None` |
| `get_cache(name)` | 캐시 조회 | `CacheRef` |
| `list_caches()` | 캐시 목록 | `list[CacheRef]` |
| `update_cache(name, ttl)` | 캐시 TTL 갱신 | `CacheRef` |
| `configure(...)` | SDK 설정 | `None` |

---

### `call(contents, ...) → LLMResponse`

단일 LLM 호출. `contents`만 필수이고 나머지는 전부 keyword-only optional.

```python
def call(
    contents: ContentsType,  # 사용자 메시지 (필수, positional)
    *,                       # ContentsType = str | list[str | FileRef | Part]
    system: str = None,      # 시스템 프롬프트
    model: str = None,       # 모델명 (기본: gemini-3.5-flash)
    temperature: float = None,  # 0.0(결정적) ~ 2.0(창의적)
    max_tokens: int = None,  # 최대 출력 토큰 수
    seed: int = None,        # 재현성을 위한 시드값
    output: str = None,      # "json" | "enum" | "text"
    schema: type | dict = None,  # Pydantic / Enum / dict (output 자동 추론)
    thinking: str = None,    # "minimal" | "low" | "medium" | "high"
    media_resolution: str = None,  # "low" | "medium" | "high"
    tools: list[dict] = None,  # Function calling / Built-in 도구 정의
    tool_config: str = None,   # "auto" | "any" | "none"
    # 기본값 None 과 string "none" 은 다르다. None = "auto" 와 같고, "none" 은 함수 호출을 하지 않도록 강제하는 것
    history: list[dict] = None,  # 이전 대화 이력
    cached_content: str = None,  # Context Cache 이름
    service_name: str = None,    # 서비스 식별자 (로깅용)
) -> LLMResponse
```

---

### `embed(contents, ...) → EmbedResponse`

텍스트 임베딩 생성. `contents`만 필수.

```python
def embed(
    contents: str | list[str],     # 임베딩할 텍스트 (필수)
    *,
    model: str = None,             # 모델명 (기본: gemini-embedding-001)
    task_type: str = None,         # 임베딩 작업 유형
    title: str = None,             # 문서 제목 (RETRIEVAL_DOCUMENT 전용)
    output_dimensionality: int = None,  # 출력 벡터 차원 (128-3072)
    service_name: str = None,      # 서비스 식별자 (로깅용)
) -> EmbedResponse
```

---

### `stream(contents, ...) → Iterator[StreamChunk]`

스트리밍 LLM 호출. 파라미터는 `call()`과 동일.

```python
for chunk in stream("설명해줘"):
    print(chunk.text, end="")
```

---

### `session(*, system, model) → Session`

멀티턴 대화 세션. 이력을 자동 관리.

```python
def session(
    *,
    system: str = None,  # 세션 전체에 적용할 시스템 프롬프트
    model: str = None,   # 세션 전체에 적용할 모델명
) -> Session
```

#### `Session` 메서드

| 메서드 | 설명 | 반환 타입 |
| ------ | ------ | ---------- |
| `call(contents, ...)` | 세션 내 호출 (이력 자동 추가). `system`/`model`/`history` 제외, `call()`과 동일 파라미터 | `LLMResponse` |
| `stream(contents, ...)` | 세션 내 스트리밍 (이력 자동 추가). 파라미터 동일 | `Iterator[StreamChunk]` |
| `get_history()` | curated 이력 반환 (유효한 턴만) | `list[dict]` |
| `get_full_history()` | comprehensive 이력 반환 (무효 턴 포함 전체) | `list[dict]` |
| `clear()` | 양쪽 이력 모두 초기화 | `None` |

> **이중 히스토리**: 빈 응답이나 SAFETY/RECITATION 등 비정상 응답은 `get_history()`에서 자동 제외되어
> 다음 요청의 이력을 오염시키지 않는다. 디버깅이 필요하면 `get_full_history()`를 사용.

---

### `upload_file(file_path, ...) → FileRef`

```python
def upload_file(
    file_path: str,              # 업로드할 파일 경로
    display_name: str = None,    # 표시 이름
    mime_type: str = None,       # MIME 타입 (생략 시 확장자에서 자동 감지)
) -> FileRef
```

---

### `Part` 모델

파일/바이너리 데이터를 `contents` 리스트에 포함시키기 위한 모델.
`from llm_handler import Part`로 사용.

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `type` | `str` | `"uri"` \| `"inline"` \| `"file"` |
| `file_uri` | `str \| None` | URI 참조 (`type="uri"`) |
| `data` | `bytes \| None` | 인라인 바이너리 (`type="inline"`) |
| `file_path` | `str \| None` | 로컬 파일 경로 (`type="file"`) |
| `mime_type` | `str \| None` | MIME 타입 (자동 감지 가능) |

#### 팩토리 메서드

| 메서드 | 설명 | mime_type = `None`일 때 자동감지 |
| ------ | ------ | ---------- |
| `Part.from_uri(file_uri, mime_type)` | URI 참조 생성 | `mime_type` 필수 (URL에서 추론 불가) |
| `Part.from_bytes(data, mime_type=None)` | 인라인 바이너리 | `filetype` 라이브러리로 자동 감지 |
| `Part.from_file(path, mime_type=None)` | 로컬 파일 읽기 | `mimetypes` + `filetype`으로 자동 감지 |

> MIME 우선 순위: `mime_type`을 명시적으로 전달하면 감지값보다 **명시값이 우선**된다. 두 값이 불일치하면 warning 로그를 남기고 명시값을 사용한다.

> ⚠️ `application/octet-stream`은 Gemini API에서 지원하지 않는다. `mime_type`은 반드시 구체적인 타입(`image/png`, `application/pdf`, `audio/mp3` 등)으로 지정해야 하며, 미지정 시 SDK 또는 Handler에서 에러가 발생한다.

---

<a id="mime-type-handling"></a>

### MIME 타입 처리

SDK는 파일의 MIME 타입을 **자동 감지 → 정규화 → Gemini API 전달** 3단계로 처리한다.

#### 1단계: SDK 자동 감지

| 메서드 | 감지 방식 |
| ------ | ------ |
| `Part.from_uri(uri, mime_type)` | 감지 없음 — 사용자가 직접 지정 |
| `Part.from_bytes(data)` | `filetype` 라이브러리로 매직 바이트(파일 앞부분의 바이너리 시그니처) 분석 |
| `Part.from_file(path)` | `mimetypes`(확장자 기반) 우선, fallback으로 `filetype`(매직 바이트) |

**MP4 컨테이너 보정**: `.m4a` 파일은 기본적으로 audio라서 `audio/mp4`로 나오지만, `.mp4` 확장자 파일은 오디오여도 `video/mp4`로 나올 떄가 있어서 보정이 필요. `video/mp4`로 감지된 경우, MP4 내부의 트랙 정보를 파싱하여 비디오 트랙이 없으면 `audio/mp4`로 자동 보정한다. `from_bytes()`로 전달해도 오디오로 올바르게 인식된다.

#### 2단계: Gemini 호환 정규화

Python 라이브러리(`filetype`, `mimetypes`)가 반환하는 MIME 타입은 RFC/IANA 표준을 따르지만,
Gemini API는 확장자 기반의 자체 표기를 사용한다. SDK가 자동으로 변환한다.

| 감지값 (라이브러리 반환) | → 정규화 (Gemini에 전달) | 이유 |
| ------ | ------ | ------ |
| `audio/mpeg` | `audio/mp3` | RFC 공식명 vs Gemini 표기 |
| `audio/x-wav` | `audio/wav` | 레거시 `x-` 접두사 제거 |
| `audio/x-flac` | `audio/flac` | 레거시 `x-` 접두사 제거 |
| `audio/x-aiff` | `audio/aiff` | 레거시 `x-` 접두사 제거 |
| `video/quicktime` | `video/mov` | QuickTime 공식명 vs 확장자 표기 |
| `video/x-msvideo` | `video/avi` | 레거시 `x-` 접두사 제거 |
| `video/x-ms-wmv` | `video/wmv` | 레거시 `x-` 접두사 제거 |

> 사용자가 `mime_type`을 명시한 경우에도 동일한 정규화가 적용된다. `Part.from_bytes(data, mime_type="audio/mpeg")`으로 전달하면 Gemini에는 `audio/mp3`로 전달된다.

#### 3단계: Gemini API 지원 MIME 타입

정규화 후 Gemini API에 전달되는 최종 MIME 타입 목록.

##### Image (5종)

| MIME | 대표 확장자 |
| ------ | ------ |
| `image/png` | `.png` |
| `image/jpeg` | `.jpg`, `.jpeg` |
| `image/webp` | `.webp` |
| `image/heic` | `.heic` |
| `image/heif` | `.heif` |

##### Audio (6종 + 비공식 1종)

| MIME | 대표 확장자 | 자동 감지 시 정규화 |
| ------ | ------ | ------ |
| `audio/mp3` | `.mp3` | `audio/mpeg` → 자동 변환 |
| `audio/wav` | `.wav` | `audio/x-wav` → 자동 변환 |
| `audio/flac` | `.flac` | `audio/x-flac` → 자동 변환 |
| `audio/aiff` | `.aiff` | `audio/x-aiff` → 자동 변환 |
| `audio/aac` | `.aac` | 변환 없이 그대로 전달 |
| `audio/ogg` | `.ogg` | 변환 없이 그대로 전달 |
| `audio/mp4` | `.m4a` | 비공식 — 문서에 없지만 동작 (AAC 컨테이너) |

##### Video (9종)

| MIME | 대표 확장자 | 자동 감지 시 정규화 |
| ------ | ------ | ------ |
| `video/mp4` | `.mp4` | 변환 없이 그대로 전달 |
| `video/mpeg` | `.mpg` | 변환 없이 그대로 전달 |
| `video/mov` | `.mov` | `video/quicktime` → 자동 변환 |
| `video/avi` | `.avi` | `video/x-msvideo` → 자동 변환 |
| `video/x-flv` | `.flv` | 변환 없이 그대로 전달 |
| `video/webm` | `.webm` | 변환 없이 그대로 전달 |
| `video/wmv` | `.wmv` | `video/x-ms-wmv` → 자동 변환 |
| `video/3gpp` | `.3gp` | 변환 없이 그대로 전달 |

##### Document / Text

| MIME | 대표 확장자 |
| ------ | ------ |
| `application/pdf` | `.pdf` |
| `text/plain` | `.txt` |
| `text/html` | `.html` |
| `text/markdown` | `.md` |
| `text/xml` | `.xml` |

---

### `delete_file(name) → None`

파일 삭제. 이미 삭제된 파일(404)은 예외 없이 무시 (멱등).

```python
def delete_file(name: str) -> None
```

---

### `get_file(name) → FileRef | None`

파일 조회. 존재하지 않으면 `None` 반환 (404를 예외로 던지지 않음).

```python
def get_file(name: str) -> FileRef | None
```

---

### `list_files() → list[FileRef]`

모든 파일 목록 조회.

```python
def list_files() -> list[FileRef]
```

---

### `create_cache(contents, ...) → CacheRef`

`call()`과 동일한 `contents` 포맷. 캐시에 `tools`/`system`을 설정하면, 이후 `call(cached_content=...)`에서 해당 파라미터를 **다시 전달하면 에러** (Gemini API 제약).

```python
def create_cache(
    contents: ContentsType,      # 캐싱할 콘텐츠 (str | list[str | FileRef | Part])
    *,
    model: str,                  # 모델명
    system: str = None,          # 시스템 프롬프트
    display_name: str = None,    # 표시 이름 (최대 128자)
    ttl: int = 3600,             # TTL (초)
    tools: list[dict] = None,    # 캐시 사용 시 자동 적용할 도구
    tool_config: str = None,     # "auto" | "any" | "none"
) -> CacheRef
```

---

### `get_cache(name) → CacheRef`

캐시 상세 조회.

```python
def get_cache(name: str) -> CacheRef
```

---

### `update_cache(name, ttl) → CacheRef`

캐시 TTL 업데이트. 만료 시간만 수정 가능 (contents, model 등은 생성 후 불변).

```python
def update_cache(name: str, ttl: int) -> CacheRef
```

---

### `configure(base_url, timeout)`

```python
def configure(
    base_url: str = None,    # Handler URL (기본: $LLM_HANDLER_URL 또는 localhost:8100)
    timeout: float = 120.0,  # HTTP 타임아웃 (초)
) -> None
```

---

### 반환 타입

#### `LLMResponse`

`call()` 및 `session.call()`의 반환 타입.

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `text` | `str \| None` | 모델 응답 텍스트 (function call 시 `None`) |
| `model` | `str` | 사용된 모델명 |
| `usage` | `TokenUsage \| None` | 토큰 사용량 |
| `finish_reason` | `str \| None` | 종료 사유 (`"STOP"`, `"MAX_TOKENS"`, `"SAFETY"` 등) |
| `function_calls` | `list[FunctionCall] \| None` | Gemini가 요청한 함수 호출 목록 (tools 사용 시) |
| `request_id` | `str` | 요청 식별자 |

| 메서드 | 설명 | 반환 타입 |
| ------ | ------ | ---------- |
| `to_dict()` | `text`를 JSON 파싱하여 dict 반환. `output="json"` 사용 시 유용 | `dict` |

#### `StreamChunk`

`stream()` 및 `session.stream()`이 yield하는 개별 청크.

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `text` | `str \| None` | 청크 텍스트 |
| `finish_reason` | `str \| None` | 마지막 청크에만 포함 |
| `usage` | `TokenUsage \| None` | 마지막 청크에만 포함 |
| `function_calls` | `list[FunctionCall] \| None` | 함수 호출 정보 (마지막 청크) |

#### `EmbedResponse`

`embed()`의 반환 타입.

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `embeddings` | `list[EmbeddingValue]` | 임베딩 벡터 목록 (입력 순서 보장) |
| `model` | `str` | 사용된 모델명 |
| `request_id` | `str` | 요청 식별자 |

#### `EmbeddingValue`

개별 임베딩 결과.

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `values` | `list[float]` | 임베딩 벡터 |
| `statistics` | `EmbeddingStatistics \| None` | 토큰 통계 |

#### `EmbeddingStatistics`

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `token_count` | `int` | 입력 토큰 수 |
| `truncated` | `bool` | 입력 잘림 여부 |

#### `FunctionCall`

`tools=` 사용 시 Gemini가 요청한 함수 호출 정보.

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `name` | `str` | 함수 이름 |
| `args` | `dict \| None` | 함수 인자 |
| `id` | `str \| None` | 호출 고유 ID (multi-turn 매핑용) |

#### `TokenUsage`

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `input_tokens` | `int` | 입력 토큰 수 (`prompt_token_count`, **cached 미포함**) |
| `output_tokens` | `int` | 출력 토큰 수 (`candidates_token_count`) |
| `total_tokens` | `int` | `input + output + thinking + tool_use` (cached 제외) |
| `thinking_tokens` | `int \| None` | 사고 토큰 수 (thinking 모드 사용 시) |
| `cached_tokens` | `int \| None` | 사용된 캐시 객체의 총 크기 (`cached_content_token_count`) ⚠️ docs와 달리 호출별 활용량이 아닌 캐시 크기 |
| `tool_use_tokens` | `int \| None` | tool 정의가 차지한 prompt 토큰 (`tool_use_prompt_token_count`) |

#### `FileRef`

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `name` | `str` | Gemini File API 리소스명 |
| `uri` | `str` | 파일 URI |
| `mime_type` | `str` | MIME 타입 |
| `state` | `str` | 상태 (`"ACTIVE"` 등) |
| `display_name` | `str \| None` | 업로드 시 지정한 표시 이름 |
| `size_bytes` | `int \| None` | 파일 크기 (바이트) |
| `expiration_time` | `str \| None` | 만료 시각 (48시간 TTL) |

#### `CacheRef`

| 필드 | 타입 | 설명 |
| ------ | ------ | ------ |
| `name` | `str` | 캐시 리소스명 (호출 시 `cached_content=`에 전달) |
| `model` | `str` | 캐시 생성에 사용된 모델 |
| `display_name` | `str \| None` | 표시 이름 |
| `create_time` | `str \| None` | 생성 시각 |
| `expire_time` | `str \| None` | 만료 시각 |
| `update_time` | `str \| None` | 마지막 수정 시각 |
| `total_token_count` | `int \| None` | 캐시된 토큰 수 |

---

### 예외 클래스

`SDKError` (base) → `HandlerConnectionError` (도달 불가) / `HandlerError` (에러 응답).
`HandlerError` 는 6 sub-class (`ValidationError`, `ContentBlockedError`, `TransportError`,
`ProviderError`, `HandlerOpsError`, `HandlerInternalError`) 로 자동 dispatch.

자세한 계층, 시나리오별 어떤 에러가 발생하는지, `error_type` / `origin` 분기 패턴 등:

> [docs/error-handling.md](docs/error-handling.md)
