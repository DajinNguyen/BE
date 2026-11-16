# 관심 기업

프론트 문서 `docs/frontend-data-requirements.md` 15장에서 요청한 API예요. 로그인이 필요해요 (401 `LOGIN_REQUIRED`).

## `GET /api/me/watchlist`

```json
{ "stock_codes": ["005930", "000660"] }
```

관심 기업이 없으면 `{ "stock_codes": [] }` 예요.

## `PUT /api/me/watchlist`

같은 형식으로 **전체 목록**을 보내면 그대로 저장하고, 저장한 값을 돌려줘요.

- 보낸 순서 그대로 저장해요. 같은 회사는 한 번만 남기고, 없는 종목코드는 버려요.
- 하나를 빼려면 뺀 목록을 다시 보내면 돼요.
- 최대 200개. 넘으면 422 `VALIDATION_ERROR` 예요.
