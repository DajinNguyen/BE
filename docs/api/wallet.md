# 앰똥깡(ADK) 지갑

프론트 문서 `docs/frontend-data-requirements.md` 16장의 형식을 그대로 따라요. 얻고 쓰는 판단은 모두 서버가 해요.

## 규칙

| 일 | ADK | 조건 |
|---|---|---|
| 가입 선물 | +50 | 한 번만. 이 기능 전에 가입한 사용자도 지갑을 처음 볼 때 받아요 |
| 낱말퀴즈 3개 완료 | +20 | [정답 보기]를 쓴 퀴즈는 세지 않아요. 하루 2번까지. 같은 퀴즈는 하루에 한 번만 세요 |
| AI 리포트 만들기 | -7 | 시작할 때 빼고, 만들지 못하면 되돌려요 |
| 챗봇 질문 | -5 | 답변이 나온 뒤에만 (`docs/api/chat.md`) |

- "오늘"은 서버 날짜(한국 시간)예요. 날짜가 바뀌면 `reward_count_today` 만 0이 되고 진행도(`done`)는 남아요.
- 잔액보다 많이 쓸 수 없어요. 버튼을 연달아 눌러도 한 번만 빠져요.
- 로그인하지 않으면 지갑·리포트 만들기·챗봇·퀴즈 보상 모두 401 `LOGIN_REQUIRED` 예요. 이미 만들어진 리포트 보기(`GET /report`)는 로그인 없이 돼요.
- 잔액이 모자라면 400 `INSUFFICIENT_BALANCE` 예요. `required`(필요한 ADK)와 `balance`(지금 잔액)도 같이 줘요.

```json
{ "error": { "code": "INSUFFICIENT_BALANCE", "message": "ADK가 모자라요. 7 ADK가 필요한데 지금 5 ADK가 있어요.", "required": 7, "balance": 5 } }
```

## `GET /api/me/wallet`

```json
{
  "balance": 63,
  "puzzle_progress": { "done": 2, "required": 3 },
  "reward_count_today": 1,
  "reward_daily_limit": 2,
  "history": [
    { "id": "3", "type": "spend", "amount": 7, "reason": "AI 리포트 · 삼성전자", "created_at": "2026-11-16T06:30:00Z" },
    { "id": "2", "type": "earn", "amount": 20, "reason": "낱말퀴즈 보상", "created_at": "2026-11-16T06:10:00Z" },
    { "id": "1", "type": "earn", "amount": 50, "reason": "가입 선물", "created_at": "2026-11-16T06:00:00Z" }
  ]
}
```

- `history` 는 최신 순, 최근 10개. `amount` 는 양수예요.
- `balance` 는 지금 쓸 수 있는 ADK예요. 만드는 중인 리포트 값은 이미 뺀 값이에요.

## `POST /api/me/puzzle-completions`

요청 `{ "puzzle_id": "basics-easy", "used_reveal": false }`

```json
{ "puzzle_progress": { "done": 0, "required": 3 }, "rewarded": true, "balance": 83, "reward_count_today": 2, "reward_daily_limit": 2 }
```

- 3/3 이 되면 20 ADK를 주고 0/3 부터 다시 세요. 하루 한도를 넘으면 세지 않아요.
- 퀴즈를 열지 않고(`GET /api/learn/crosswords/{id}` 없이) 보낸 완료, 연 지 15초 안에 온 완료는 세지 않아요.
- 예전 형식(`revealed`, `completed_at`)으로 보내도 받아요.

## `POST /api/companies/{stock_code}/report` (바뀜)

- 로그인 필요. 응답은 기존 리포트 형식에 `balance`(7 ADK를 뺀 뒤 잔액)가 붙어요.
- 리포트가 `failed` 로 끝나면 서버가 7 ADK를 되돌려요. 끝난 뒤 `GET /api/me/wallet` 을 다시 부르면 맞는 잔액이 와요.
- 이미 만드는 중이면 `generating` 과 지금 잔액만 주고 ADK는 쓰지 않아요.

## `POST /api/me/wallet/spend`

요청 `{ "reason": "report" | "chat", "target_id": "…" }` → 응답 `{ "balance": 38 }`

- 바로 빼요. 같은 `reason`·`target_id` 로 다시 보내면 한 번만 빼요.
- **리포트 만들기·챗봇과 같이 부르면 안 돼요.** 그 둘은 각 API 안에서 서버가 이미 빼서, 같이 부르면 두 번 빠져요.

## `GET /api/me/usage-credits`

예전 프론트용이에요 (오늘 받은 보상, 센 퀴즈, 최근 완료 기록). 지금 프론트는 `GET /api/me/wallet` 을 써요.
