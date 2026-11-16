# 비교하기

## `GET /api/compare?stock_codes=005930,000660`

두 회사의 매출·영업이익·영업이익률·매출 증가율·부채비율을 **같은 연도, 같은 기준**으로 맞춰서 줘요. 로그인은 필요 없어요.

```json
{
  "unit": "조 원",
  "fiscal_year": 2025,
  "basis": "연결 기준",
  "comparable": true,
  "notes": [],
  "differences": [
    "2025년 매출은 삼성전자 333.6조 원, SK하이닉스 97.2조 원이에요. 삼성전자 쪽이 3.4배 커요.",
    "100원어치를 팔았을 때 남긴 돈은 삼성전자 13.1원, SK하이닉스 48.6원이에요. (영업이익률)"
  ],
  "companies": [
    {
      "stock_code": "005930", "name": "삼성전자", "sector": "반도체·전자제품", "market": "KOSPI",
      "market_cap": { "value": 1145.9, "is_sample": false },
      "is_financial": false, "has_financials": true,
      "basis": "연결 기준", "source": "DART 2025년 사업보고서 · 2026년 반기보고서",
      "fiscal_year": 2025,
      "metrics": { "revenue": 333.61, "operating_income": 43.6, "operating_margin": 13.1, "revenue_growth": 10.9, "debt_ratio": 29.9 },
      "annual": [ { "fiscal_year": 2024, "revenue": 300.87, "operating_income": 32.73, "operating_margin": 10.9 } ]
    }
  ]
}
```

| 칸 | 뜻 |
|---|---|
| `fiscal_year` | 비교 기준 연도. 두 회사 재무제표에 모두 있는 가장 최근 사업연도. 겹치는 연도가 없으면 `null` |
| `basis` | 두 회사 기준이 같으면 그 기준(`연결 기준`/`별도 기준`), 다르면 `null` |
| `comparable` | 같은 연도·같은 기준·같은 업종 성격(둘 다 금융업이거나 둘 다 아니거나)이라 나란히 봐도 되는지 |
| `notes` | 비교할 때 알아야 할 점. 화면에 그대로 보여줄 수 있는 쉬운 말 |
| `differences` | 숫자로 본 차이. 서버가 계산해서 쓴 문장 (AI가 쓴 글이 아니에요) |
| `companies[]` | 요청한 순서대로 2곳 |
| `companies[].is_financial` | 금융업(은행·증권·보험·카드 등)인지 |
| `companies[].has_financials` | 재무제표를 불러왔는지. `false` 면 `metrics` 가 모두 `null` |
| `companies[].metrics` | 금액은 조 원, 비율은 %. 구할 수 없는 값은 `null` |
| `companies[].annual` | 두 회사에 모두 있는 연도만, 오래된 순 (그래프용) |

- 금액 단위는 재무 탭과 같이 조 원이에요. 작은 회사는 `0.0824` 처럼 와요 (824억 원).
- 재무제표는 연결을 먼저 쓰고, 종속회사가 없어 별도만 있는 회사는 별도예요. 기준이 다르면 `notes` 로 알려줘요.
- 부채비율은 가장 최근 사업보고서 값만 있어요. 한쪽만 새 사업보고서가 나와 앞 연도로 맞춘 경우 그 회사 부채비율은 `null` 이에요.
- 에러: 회사가 2곳이 아니거나 같은 회사면 400 `INVALID_PARAM`, 없는 회사면 404 `COMPANY_NOT_FOUND`.

## `GET /api/companies/{stock_code}/similar?limit=4`

지금 보는 회사와 **같은 산업이고 시가총액이 비슷한 회사**를 가까운 순으로 줘요. `limit` 는 1~4 (기본 4).

응답은 `CompanySummary` 에 두 칸이 더 있어요.

| 칸 | 뜻 |
|---|---|
| `market_cap` | 시가총액 (조 원) |
| `industry` | 같이 속한 산업 `{ industry_id, name }` |

- 속한 산업이 없는 회사는 빈 목록 `[]` 이에요.
- 시가총액이 아주 큰 회사(삼성전자)는 비슷한 크기의 회사가 없어서, 같은 산업에서 그나마 가까운 회사가 나와요.
