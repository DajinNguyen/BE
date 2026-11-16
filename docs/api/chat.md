# AI 챗봇

사용자가 보고 있는 화면의 자료를 서버가 찾아서 AI에게 넘기고, AI는 그 자료를 근거로 쉬운 말로 대답해요.
모두 로그인이 필요해요 (401 `LOGIN_REQUIRED`).

## `POST /api/chat/messages`

요청

```json
{
  "message": "영업이익이 왜 이렇게 늘었어요?",
  "context": { "page": "company", "stock_code": "000660", "tab": "financials" },
  "conversation_id": "12"
}
```

| 칸 | 뜻 |
|---|---|
| `message` | 질문. 1~500자 |
| `context.page` | `home` \| `company` \| `industry` \| `term` \| `compare` \| `other` |
| `context.stock_code` | 기업 화면이면 종목코드 |
| `context.tab` | 기업 화면의 탭: `overview` \| `report` \| `financials` \| `news` \| `community` |
| `context.industry_id` | 산업 화면이면 산업 id |
| `context.term_id` | 용어 카드면 용어 id |
| `context.compare_stock_code` | 비교 화면이면 견주는 회사 (`stock_code` 와 같이) |
| `conversation_id` | 이어서 묻는 대화. 없으면 새 대화를 만들어요 |

`context` 의 칸은 있는 것만 보내면 돼요. 화면 자료가 없으면(`home`, `other`) 일반적인 주식 공부 질문에만 대답해요.

응답

```json
{
  "conversation_id": "12",
  "question": { "id": "31", "role": "user", "content": "영업이익이 왜 이렇게 늘었어요?", "created_at": "2026-11-16T07:00:00Z" },
  "answer": { "id": "32", "role": "assistant", "content": "2023년에는 …", "created_at": "2026-11-16T07:00:00Z" },
  "charged": 5,
  "balance": 45
}
```

- 답변이 제대로 나왔을 때만 5 ADK가 빠져요. `charged` 가 빠진 ADK, `balance` 가 남은 잔액이에요.
- 답변에는 줄바꿈(`\n`)이 들어 있을 수 있어요. 마크다운은 쓰지 않아요.
- 응답이 올 때까지 몇 초 걸려요 (AI를 기다려요).

에러

| 상태 | code | 언제 | ADK |
|---|---|---|---|
| 400 | `INVALID_LENGTH` | 질문이 비어 있음 | 안 빠짐 |
| 402 | `INSUFFICIENT_ADK` | 잔액이 5 ADK보다 적음 (`required`, `balance` 포함) | 안 빠짐 |
| 404 | `COMPANY_NOT_FOUND` \| `INDUSTRY_NOT_FOUND` \| `TERM_NOT_FOUND` \| `CONVERSATION_NOT_FOUND` | 없는 화면·대화 (남의 대화도 404) | 안 빠짐 |
| 429 | `TOO_FREQUENT` | 앞 질문에 답하는 중이거나, 1분에 5개를 넘김 | 안 빠짐 |
| 502 | `CHAT_FAILED` | AI가 답을 만들지 못함 | 안 빠짐 |
| 503 | `CHAT_NOT_CONFIGURED` | 서버에 AI 키가 없음 | 안 빠짐 |

## 투자 추천·예측을 하지 않아요

- 프롬프트 규칙: 화면 자료에 있는 사실·숫자만 쓰기, 매수·매도 추천과 주가·실적 예측 금지, 쉬운 말, 3~5문장.
- 그래도 답변에 "사세요", "오를 거예요", "목표 주가는" 같은 말이 섞이면 서버가 그 답을 버리고
  정해 둔 거절 문장으로 바꿔요. 이때는 `charged: 0` 이에요.
- "사도 될까요?" 같은 질문에 AI가 스스로 "추천은 해 드릴 수 없어요, 대신 이런 자료를 보세요"라고 답한 경우는
  정상 답변이라 5 ADK가 빠져요.

## `GET /api/chat/conversations`

내 대화 목록, 최근에 물은 순 (최대 30개).

```json
[ { "id": "12", "title": "영업이익이 왜 이렇게 늘었어요?", "updated_at": "2026-11-16T07:00:00Z" } ]
```

## `GET /api/chat/conversations/{conversation_id}`

대화 한 건의 질문·답변 (오래된 순). 답변이 나온 질문만 저장돼요.

```json
{ "id": "12", "title": "…", "updated_at": "…", "messages": [ { "id": "31", "role": "user", "content": "…", "created_at": "…" } ] }
```
