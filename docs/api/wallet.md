# 앰똥깡(ADK) 지갑

사용자마다 지갑이 하나 있어요. 얻고 쓰는 판단은 모두 서버가 해요.
**프론트가 "차감해 주세요"라고 보내는 API는 없어요.** 리포트 만들기·챗봇 API를 부르면 서버가 알아서 빼요.

## 규칙

| 일 | ADK | 조건 |
|---|---|---|
| 가입 | +50 | 한 번만. 이 기능 전에 가입한 사용자도 지갑을 처음 볼 때 받아요 |
| 낱말퀴즈 3개 완료 | +20 | [정답 보기]를 쓴 퀴즈는 개수에서 빼요. 하루 2번까지. 같은 퀴즈는 하루에 한 번만 세요 |
| AI 리포트 만들기 | -7 | 리포트가 제대로 나왔을 때만 |
| 챗봇 질문 | -5 | 답변이 제대로 나왔을 때만 (`docs/api/chat.md`) |

- "오늘"은 서버 날짜(한국 시간)예요.
- 쓰는 금액은 AI를 부르기 전에 잡아 두고, 결과가 나오면 확정, 실패하면 돌려놓아요. 그래서 잔액보다 많이 쓸 수 없어요.
- 버튼을 연달아 눌러도 한 번만 빠져요 (만드는 중인 리포트는 다시 시작하지 않아요).
- 로그인하지 않으면 지갑·리포트 만들기·퀴즈 보상 모두 401 `LOGIN_REQUIRED` 예요. 이미 만들어진 리포트 보기(`GET /report`)는 로그인 없이 돼요.

## `GET /api/me/wallet` (로그인 필요)

```json
{
  "balance": 63,
  "unit": "ADK",
  "prices": { "report": 7, "chat": 5 },
  "rewards": { "signup": 50, "puzzle": 20, "puzzles_per_reward": 3, "puzzle_daily_max": 2 },
  "history": [
    { "id": "3", "amount": -7, "reason": "report", "title": "AI 리포트 만들기 (삼성전자)", "created_at": "2026-11-16T06:30:00Z" },
    { "id": "2", "amount": 20, "reason": "puzzle_reward", "title": "낱말퀴즈 3개 완료", "created_at": "2026-11-16T06:10:00Z" },
    { "id": "1", "amount": 50, "reason": "signup", "title": "가입 축하", "created_at": "2026-11-16T06:00:00Z" }
  ]
}
```

- `history` 는 최신 순, 최대 50개. `reason` 은 `signup` | `puzzle_reward` | `report` | `chat`.
- 가격·보상 숫자는 `prices`·`rewards` 를 쓰면 화면에 숫자를 따로 적어 두지 않아도 돼요.

## `POST /api/companies/{stock_code}/report` (바뀜)

- 로그인이 필요해요. 안 했으면 401 `LOGIN_REQUIRED`.
- ADK가 모자라면 시작하지 않고 402 를 돌려줘요.

```json
{ "error": { "code": "INSUFFICIENT_ADK", "message": "ADK가 모자라요. 7 ADK가 필요한데 지금 5 ADK가 있어요.", "required": 7, "balance": 5 } }
```

- 리포트가 `done` 이 되면 7 ADK가 빠져요. `failed` 면 빠지지 않아요. 끝난 뒤 `GET /api/me/wallet` 을 다시 불러 주세요.
- 이미 다른 사람이 만드는 중이면 `generating` 만 돌려주고 ADK는 쓰지 않아요.

## `GET /api/me/usage-credits`, `POST /api/me/puzzle-completions` (칸 추가)

기존 응답에 두 칸이 늘었어요. "리포트 만들기 1회 추가" 보상은 없어지고, 보상 1회마다 20 ADK를 줘요.

| 칸 | 뜻 |
|---|---|
| `reward_adk` | 보상 1회에 주는 ADK (20) |
| `balance` | 지금 내 ADK 잔액 |
