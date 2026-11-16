# 비교하기

프론트 문서 `docs/frontend-data-requirements.md` 17장의 형식을 그대로 따라요. 로그인은 필요 없어요.

## `GET /api/companies/compare?left={code}&right={code}`

left = 비교할 기업, right = 지금 보는 기업.

```json
{
  "base_period": "2025년 연간",
  "basis": "연결 기준",
  "left":  { "stock_code": "000660", "name": "SK하이닉스", "sector": "반도체", "market_cap": 816.7, "is_financial": false,
             "revenue": 97.15, "operating_income": 47.21, "operating_margin": 48.6, "revenue_growth": 46.8, "debt_ratio": 45.9,
             "unit": "조 원", "base_period": "2025년 연간", "basis": "연결 기준" },
  "right": { "stock_code": "005930", "...": "같은 형식" },
  "analysis": null,
  "notes": []
}
```

- **같은 기간**: 두 회사 재무제표에 모두 있는 가장 최근 사업연도로 맞춰요. 한쪽만 새 사업보고서가 나왔으면 앞 연도로 맞춰요.
- **같은 기준**: 연결을 먼저 써요. 종속회사가 없어 별도만 있는 회사는 회사별 `basis` 가 `별도 기준` 으로 와요.
- 값을 구할 수 없으면 `null` 이에요. 재무제표가 없는 회사는 숫자와 `base_period`·`basis` 가 모두 `null` 이에요.
- 겹치는 연도가 없으면 맨 위 `base_period` 는 `""` 이고, 회사마다 자기 최근 연도 값이 와요.
- 부채비율은 가장 최근 사업보고서 값만 있어요. 앞 연도로 맞춘 회사는 `debt_ratio` 가 `null` 이에요.
- `notes`: 기준이 다르거나 금융업이 섞였을 때처럼 알아야 할 점을 쉬운 말로 적어요 (문서 17장에는 없는 추가 칸).
- 에러: 같은 회사면 400 `INVALID_PARAM`, 없는 회사면 404 `COMPANY_NOT_FOUND`.

### `analysis` (한눈에 비교, AI)

```json
{
  "headline": "삼성전자는 여러 가지 사업을 하고, SK하이닉스는 메모리 반도체에 집중해요",
  "points": [ { "title": "규모", "description": "삼성전자의 매출액은 333.61조 원이고, …" } ],
  "reason": "삼성전자는 반도체 외에도 스마트폰, 디스플레이 등 여러 가지 사업을 하는 반면, …",
  "sources": [ { "title": "삼성전자 사업보고서 (2025.12)", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=…", "date": "2026-03-10" } ]
}
```

- 처음 비교하는 조합은 `analysis: null` 로 오고, 그때 백그라운드에서 만들기 시작해요. 10초쯤 뒤 다시 부르면 채워져 있어요.
- 한 번 만든 글은 저장해 두고, 비교 기준 연도가 바뀌면 다시 만들어요. 왼쪽·오른쪽을 바꿔도 같은 글이에요.
- 두 회사 모두 같은 해 숫자가 있을 때만 만들어요.
- 숫자는 서버가 넘긴 값만 쓰게 해요. "더 좋은 회사", 추천, 예측이 섞이거나 넘기지 않은 숫자가 나온 문장은 서버가 빼요.
  한 줄 결론이 규칙을 어기면 분석 전체를 쓰지 않아요 (`null`).
- ADK는 들지 않아요.

## `GET /api/companies/{stock_code}/peers?limit=4`

같은 산업이고 시가총액이 비슷한 회사를 가까운 순으로 줘요. `CompanySummary` 와 같은 형식, 자기 자신은 빼요.

- `limit` 는 1~4 (기본 4). 속한 산업이 없는 회사는 빈 배열이에요.
- 시가총액이 아주 큰 회사(삼성전자)는 비슷한 크기가 없어서, 같은 산업에서 그나마 가까운 회사가 나와요.
