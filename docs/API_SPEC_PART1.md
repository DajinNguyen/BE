# 다진(dajin) API 명세서 — Part 1 (MVP 핵심)

| 항목 | 내용 |
| --- | --- |
| 문서 버전 | v0.1 |
| 작성일 | 2026-09-29 |
| 기준 | `API_SPEC.md`(전체 명세) 중 `FUNCTIONAL_SPEC.md` **P0(시연 필수)** 기능만 추림 |
| 목적 | 백엔드를 여러 파트로 나눠 개발할 때, **1파트에서 구현할 범위**를 명확히 하기 위함 |
| 범위 | 지금 완성된 프론트 UI(차트 탭·AI 리포트 탭·재무 분석 탭·뉴스 탭·용어 설명 시트)와 바로 연결되는 API만 포함 |

> 이 문서에 없는 엔드포인트(인증, 관심 기업, 용어 진행상황 등)는 **Part 2 이후**로 미룹니다. 전체 목록과 이유는 문서 맨 아래 §7 참고.
>

---

## 1. 공통 규칙

| 항목 | 내용 |
| --- | --- |
| Base URL | 프론트 `.env`의 `VITE_API_BASE_URL` |
| 경로 규칙 | 복수형 리소스명, kebab-case. JSON 키는 `snake_case` |
| 인증 | 🚫 **이 파트에서는 인증이 필요 없음.** 모든 엔드포인트는 로그인 여부와 무관하게 동작 (로그인은 Part 2) |
| 오류 응답 | `{ "detail": "사용자에게 보여줄 메시지", "code": "ERROR_CODE" }` |
| 예시 값 표시 | 확인 전 숫자는 `{ "value": 숫자, "is_sample": true }` |

---

## 2. 기업 검색 · 조회 (F-01, F-02, F-08)

| 메서드 | 경로 | 설명 | 응답 타입 |
| --- | --- | --- | --- |
| GET | `/api/companies?query=` | 이름(한/영)·종목코드 검색 | `CompanySummary[]` |
| GET | `/api/companies/popular` | 홈 “많이 보는 회사” | `CompanySummary[]` |
| GET | `/api/companies/{stock_code}` | 회사 상세 (시세 요약 + 사업 정보 + 타임라인) | `CompanyDetail` |

### GET /api/companies?query=

- 매칭 규칙: 공백 무시, 영어 대소문자 무시, 한글명·영문명·종목코드 중 하나라도 포함되면 매치
- 결과가 없으면 빈 배열 `[]` (에러 아님)

```json
[
  {
    "stock_code": "005930",
    "corp_code": "00126380",
    "name": "삼성전자",
    "name_en": "Samsung Electronics",
    "sector": "반도체·전자제품",
    "market": "KOSPI",
    "current_price": { "value": 128500, "is_sample": true },
    "change_rate": { "value": 1.82, "is_sample": true },
    "has_report": true
  }
]
```

### GET /api/companies/{stock_code}

- 없는 종목코드 → `404 COMPANY_NOT_FOUND`
- `CompanySummary` 필드 + 아래 필드 포함 (기업 정보 탭 F-08도 이 엔드포인트 하나로 이미 커버됨 — 별도 구현 불필요)

```json
{
  "stock_code": "005930",
  "corp_code": "00126380",
  "name": "삼성전자",
  "name_en": "Samsung Electronics",
  "sector": "반도체·전자제품",
  "market": "KOSPI",
  "current_price": { "value": 128500, "is_sample": true },
  "change_rate": { "value": 1.82, "is_sample": true },
  "has_report": true,
  "market_cap": { "value": 760, "is_sample": true },
  "homepage_url": "https://www.samsung.com/sec/",
  "dart_url": "https://dart.fss.or.kr/",
  "business_description": "삼성전자는 우리가 매일 쓰는 스마트폰부터...",
  "business_segments": [
    { "code": "DS", "name": "DS(반도체)", "description": "메모리 반도체(HBM 등)를 만들고..." }
  ],
  "timeline": [
    { "year": "2023", "title": "이익이 크게 줄었어요", "description": "...", "is_plan": false },
    { "year": "2026", "title": "AI 시대를 이끌겠다는 목표", "description": "...", "is_plan": true }
  ],
  "timeline_source": "삼성전자 2025년 4분기 실적 발표 (2026년 1월)"
}
```

---

## 3. 재무 · 시세 · 뉴스 (F-03, F-06, F-07)

| 메서드 | 경로 | 설명 | 응답 타입 |
| --- | --- | --- | --- |
| GET | `/api/companies/{stock_code}/financials` | 연도별 실적, 최근 분기, 사업부문별 매출, 지표 | `Financials` |
| GET | `/api/companies/{stock_code}/prices?period=` | 주가 흐름 (`1m`\|`3m`\|`6m`\|`1y`) | `PriceHistory` |
| GET | `/api/companies/{stock_code}/news` | 관련 뉴스 | `NewsItem[]` |

### GET /api/companies/{stock_code}/financials

```json
{
  "stock_code": "005930",
  "unit": "조 원",
  "basis": "연결 기준",
  "source": "삼성전자 공식 실적 발표 (2026년 1월)",
  "annual": [
    { "fiscal_year": 2023, "revenue": 258.94, "operating_income": 6.57 },
    { "fiscal_year": 2024, "revenue": 300.9, "operating_income": 32.7 },
    { "fiscal_year": 2025, "revenue": 333.6, "operating_income": 43.6 }
  ],
  "latest_quarter": {
    "label": "2025년 4분기", "revenue": 93.8, "operating_income": 20.1,
    "note": "분기 기준 역대 최대"
  },
  "segments": {
    "base_label": "2025년 4분기 기준",
    "items": [{ "code": "DS", "name": "DS(반도체)", "revenue": 44.0 }]
  },
  "indicators": {
    "roe": { "value": 9.2, "is_sample": true },
    "debt_ratio": { "value": 27.1, "is_sample": true },
    "per": { "value": 18.5, "is_sample": true },
    "pbr": { "value": 1.9, "is_sample": true }
  }
}
```

- 매출 증가율·영업이익률은 프론트가 `annual[]` 원본 값으로 직접 계산함 (백엔드는 원본 수치만 정확히 내려주면 됨)

### GET /api/companies/{stock_code}/prices?period=

- `period` 외 값이 오면 `422 VALIDATION_ERROR`
- 주가는 pykrx/FinanceDataReader 연동 전까지 `is_sample: true`

```json
{
  "stock_code": "005930",
  "period": "3m",
  "is_sample": true,
  "points": [{ "date": "2026-01-02", "close": 128500 }]
}
```

### GET /api/companies/{stock_code}/news

- 실제 기사가 없는 자리는 지어내지 않고 `is_placeholder: true` + `url: null`

```json
[
  {
    "id": "news-1",
    "title": "삼성전자, 2025년 4분기 매출 93.8조 원… 분기 기준 역대 최대",
    "url": "https://news.samsung.com/kr/",
    "source": "삼성전자 뉴스룸",
    "published_at": "2026-01-15",
    "summary": "2025년 4분기에 매출 93.8조 원, 영업이익 20.1조 원으로...",
    "financial_link": { "label": "2025년 매출", "value": "333.6조 원" },
    "is_placeholder": false
  }
]
```

---

## 4. AI 리포트 (F-04, F-05, F-09)

| 메서드 | 경로 | 설명 | 응답 타입 |
| --- | --- | --- | --- |
| GET | `/api/companies/{stock_code}/report` | 저장된 리포트 조회 | `CompanyReport` |
| POST | `/api/companies/{stock_code}/report` | 새로 생성 (있으면 덮어씀) | `CompanyReport` |

- GET에서 저장된 리포트가 없으면 `404 REPORT_NOT_FOUND` → 프론트는 정상 흐름(`null`)으로 처리
- 리포트를 지원하지 않는 회사(데모 범위 밖)를 열면 `404 REPORT_NOT_READY`
- **동기 응답 1회**로 구현 (요청 → 완료까지 대기 후 응답). 폴링/SSE 전환은 Part 2 이후 검토
- 퀴즈(F-09)는 별도 API 없이 이 응답의 `quiz[]` 필드로 처리됨

```json
{
  "stock_code": "005930",
  "generated_at": "2026-01-20T09:12:00+09:00",
  "generation_seconds": 3.6,
  "is_cached": false,
  "analyzed_sources": ["재무제표", "뉴스", "사업보고서"],
  "summary": "3년 동안 매출과 이익이 크게 늘었어요. 특히 AI용 메모리(HBM)가 잘 팔리면서 2025년 4분기에 역대 가장 많은 매출을 올렸어요.",
  "key_points": [
    {
      "id": "kp-performance",
      "category": "performance",
      "title": "실적",
      "body": "2025년에 매출 333.6조 원, 영업이익 43.6조 원을 벌었어요. 100원어치 팔면 약 13원을 남겼어요.",
      "status": "good",
      "source": "삼성전자 실적 발표 · 2025년 연간 · 연결 기준",
      "base_date": "2026-01-15"
    }
  ],
  "charts": [
    { "id": "chart-1", "type": "annual_revenue", "title": "3년 동안의 매출", "caption": "매출이 3년 연속 늘었어요." }
  ],
  "connection": {
    "chain": ["AI 시대를 이끌겠다는 비전", "HBM4 공급을 시작한다는 소식", "반도체 매출이 가장 큰 비중"],
    "conclusion": "회사의 계획과 실제 숫자가 같은 방향을 가리키고 있어요."
  },
  "strengths": ["3년 동안 매출이 258.94조 원에서 333.6조 원으로 꾸준히 늘었어요."],
  "watch_points": ["2023년에는 영업이익이 6.57조 원까지 줄었던 적이 있어요."],
  "quiz": [
    {
      "id": "q1",
      "question": "삼성전자의 2025년 매출은 얼마였을까요?",
      "options": ["258.94조 원", "300.9조 원", "333.6조 원"],
      "answer_index": 2,
      "explanation": "2025년 매출은 333.6조 원이었어요."
    }
  ],
  "disclaimers": [
    "이 리포트는 기업을 이해하도록 돕는 교육용 정보이며 투자 추천이 아니에요.",
    "숫자는 공식 공시 자료를 그대로 사용하고, AI는 설명만 작성해요."
  ],
  "data_sources": ["금융감독원 DART", "한국거래소", "삼성전자 실적 발표"]
}
```

**AI 문장 생성 규칙**

- 확인된 수치 외의 숫자를 새로 만들지 않음, 매수·매도 권유 표현 금지, “~해요” 체
- `charts[].type`은 `annual_revenue`|`annual_operating_income`|`operating_margin`|`segment_revenue` 중 하나

---

## 5. 용어 (F-10)

| 메서드 | 경로 | 설명 | 응답 타입 |
| --- | --- | --- | --- |
| GET | `/api/terms` | 전체 용어 사전 | `Term[]` |

```json
[
  {
    "id": "revenue",
    "name": "매출액",
    "aliases": ["매출"],
    "easy_meaning": "회사가 물건이나 서비스를 팔아서 받은 돈 전체예요.",
    "example": "매출액이 100조 원이면, 1년 동안 판 것들의 값을 다 더한 게 100조 원이라는 뜻이에요."
  }
]
```

- 홈 화면 “오늘의 용어” 카드는 별도 API 없이 프론트가 이 목록에서 날짜 기준으로 하나를 골라 씀

---

## 6. 에러 코드 (Part 1 범위)

| 코드 | 상태 | 의미 |
| --- | --- | --- |
| `COMPANY_NOT_FOUND` | 404 | 없는 종목코드 |
| `REPORT_NOT_READY` | 404 | 데모 범위 밖이라 리포트 자체를 지원하지 않는 회사 |
| `REPORT_NOT_FOUND` | 404 | 아직 생성된 적 없는 리포트 (정상 흐름) |
| `VALIDATION_ERROR` | 422 | 쿼리/바디 형식 오류 (예: `period` 값 오류) |

---

## 7. Part 1에서 제외한 것 (Part 2 이후)

| 제외 항목 | 관련 기능 | 이유 |
| --- | --- | --- |
| 인증 (`/api/auth/*`) | F-13 | P1. Google OAuth 키 발급 등 외부 의존성 있음 |
| 관심 기업 (`/api/watchlist`) | F-12 | P1. 로그인 이후에나 의미 있는 기능 |
| 용어 진행상황 (`/api/terms/progress`) | F-11 | P1. 로그인과 묶여있음 |
| 여러 종목 일괄 조회 (`?stock_codes=`) | F-12 | 관심 기업 목록 화면 전용이라 Part 1에선 불필요 |
| 헬스체크, CORS 설정 | - | 인프라 파트(별도 담당)에서 처리 |

이 항목들은 기존 전체 명세서(`API_SPEC.md`)에 이미 정리돼 있으니, Part 2 착수 시 그대로 가져다 쓰면 됩니다.
