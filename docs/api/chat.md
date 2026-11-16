# AI 챗봇

사용자가 보고 있는 화면의 자료를 서버가 찾아서 AI에게 넘기고, AI는 그 자료를 근거로 쉬운 말로 대답해요.
모두 로그인이 필요해요 (401 `LOGIN_REQUIRED`).

## `POST /api/chat`

프론트 문서 `docs/frontend-data-requirements.md` 18장의 형식을 그대로 따라요.

요청

```json
{
  "message": "영업이익률이 뭐예요?",
  "context": { "page": "company", "tab": "financials", "stock_code": "005930", "company_name": "삼성전자" },
  "conversation_id": null
}
```

| 화면 | context | 서버가 근거로 넘기는 자료 |
|---|---|---|
| 메인 | `{ "page": "main" }` | 없음 (일반적인 주식 공부 질문에만 대답) |
| 기업 상세 | `{ "page": "company", "tab", "stock_code" }` | 회사 소개·시세·재무·지표·저장된 AI 리포트. 뉴스 탭이면 최근 기사 제목 |
| AI 분석 | `{ "page": "report", "stock_code", "report_id" }` | 기업 상세와 같음 (리포트 탭으로 봄) |
| 비교하기 | `{ "page": "compare", "left_code", "right_code" }` | 두 회사 각각의 기업 자료 |
| 산업 | `{ "page": "industry", "industry_id" }` | 산업 설명, 관심이 몰리는 이유, 큰 회사 8곳 |
| 용어 학습 | `{ "page": "learn", "term_id"? }` | 용어의 쉬운 설명·예시 (`term_id` 가 있을 때) |
| 그 밖 | `{ "page": "other" }` | 없음 |

- 이름 칸(`company_name`, `industry_name`, `left_name`, `right_name`)은 받기만 하고 쓰지 않아요. 자료는 코드·id 로 서버가 직접 찾아요.
- 없는 회사·산업·용어가 와도 에러를 내지 않고, 그 자료 없이 대답해요.
- `message` 는 1~500자. 첫 질문은 `conversation_id: null`, 이어서 물으면 서버가 준 id 를 보내요.

응답

```json
{ "answer": "영업이익률은 100원어치를 팔아 남긴 돈의 비율이에요. 삼성전자는 …", "conversation_id": "12", "balance": 45 }
```

- 답변이 제대로 나왔을 때만 5 ADK가 빠져요. `balance` 는 뺀 뒤 잔액이에요.
- 답변에는 줄바꿈(`\n`)이 들어 있을 수 있어요. 마크다운은 쓰지 않아요.
- 응답이 올 때까지 몇 초 걸려요 (AI를 기다려요). 스트리밍은 아직 없어요.

에러

| 상태 | code | 언제 | ADK |
|---|---|---|---|
| 400 | `INVALID_LENGTH` | 질문이 비어 있음 | 안 빠짐 |
| 400 | `INSUFFICIENT_BALANCE` | 잔액이 5 ADK보다 적음 (`required`, `balance` 포함) | 안 빠짐 |
| 404 | `CONVERSATION_NOT_FOUND` | 없는 대화 (남의 대화도 404) | 안 빠짐 |
| 429 | `TOO_FREQUENT` | 앞 질문에 답하는 중이거나, 1분에 5개를 넘김 | 안 빠짐 |
| 502 | `CHAT_FAILED` | AI가 답을 만들지 못함 | 안 빠짐 |
| 503 | `CHAT_NOT_CONFIGURED` | 서버에 AI 키가 없음 | 안 빠짐 |

## 투자 추천·예측을 하지 않아요

- 프롬프트 규칙: 화면 자료에 있는 사실·숫자만 쓰기, 매수·매도 추천과 주가·실적 예측 금지, 쉬운 말, 3~5문장.
- 그래도 답변에 "사세요", "오를 거예요", "목표 주가는" 같은 말이 섞이면 서버가 그 답을 버리고
  정해 둔 거절 문장으로 바꿔요. 이때는 ADK를 빼지 않아요 (`balance` 가 그대로예요).
- "사도 될까요?" 같은 질문에 AI가 스스로 "추천은 해 드릴 수 없어요, 대신 이런 자료를 보세요"라고 답한 경우는
  정상 답변이라 5 ADK가 빠져요.

## 대화 기록 (서버 저장)

지금 프론트는 대화를 기기에서 세션 동안만 보여줘요. 서버에도 답변이 나온 질문·답변을 저장해 두고, 아래 API로 볼 수 있어요.

### `GET /api/chat/conversations`

내 대화 목록, 최근에 물은 순 (최대 30개).

```json
[ { "id": "12", "title": "영업이익이 왜 이렇게 늘었어요?", "updated_at": "2026-11-16T07:00:00Z" } ]
```

### `GET /api/chat/conversations/{conversation_id}`

대화 한 건의 질문·답변 (오래된 순). 답변이 나온 질문만 저장돼요.

```json
{ "id": "12", "title": "…", "updated_at": "…", "messages": [ { "id": "31", "role": "user", "content": "…", "created_at": "…" } ] }
```
