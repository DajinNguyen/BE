# 다진(dajin) API 명세서

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.3 |
| 작성일 | 2026-10-12 |
| 기준 | 프론트엔드 재구축 버전(`frontend/` origin/main, 2026-10-07 전면 교체분) 실제 코드 + `frontend/docs/report-schema-proposal.md` |
| 목적 | 백엔드가 이 문서만 보고 프론트를 한 줄도 안 고치고 바로 붙을 수 있게 |

> ✅ **v0.3 (2026-10-12)**: 이 문서의 계약을 백엔드 `feature/frontend-sync` 브랜치에서 구현했습니다. 아래 🔴/🟡 표시는 v0.2 당시 "백엔드가 아직 맞추지 않은 부분"이었고, 구현 상태 열과 §14는 v0.3 기준으로 고쳤습니다. 인증·관심 기업·용어 진행상황(§7~9)은 여전히 미구현입니다.
>
> v0.1과의 관계: 2026-10-06 클라이언트 3차 미팅 피드백("기업 상세 = AI 핵심 분석 중심")으로 프론트가 전면 재구축되면서 API 계약이 크게 바뀌었습니다.

---

## 1. 공통 규칙

| 항목 | 내용 |
|---|---|
| Base URL | 프론트 `.env`의 `VITE_API_BASE_URL` |
| 경로 규칙 | 복수형 리소스명, kebab-case. JSON 키는 `snake_case` |
| 인증 방식 | httpOnly 쿠키 (`credentials: 'include'`). 아직 미구현 |
| 오류 응답 | `{ "detail": "사용자에게 보여줄 메시지", "code": "ERROR_CODE" }` |
| 예시 값 표시 | 확인 전 숫자는 `{ "value": 숫자, "is_sample": true }` (`SampleNumber`) |

---

## 2. 기업 (Companies)

| 메서드 | 경로 | 설명 | 구현 상태 |
|---|---|---|---|
| GET | `/api/companies?query=&limit=20` | 검색 (최대 20개) | ✅ 구현됨 (1~20, 정확히 일치하는 기업 먼저) |
| GET | `/api/companies?stock_codes=a,b` | 종목코드 일괄 조회 | ✅ 구현됨 |
| GET | `/api/companies/{stock_code}` | 기업 상세 | ✅ 구현됨 |
| ~~GET~~ | ~~`/api/companies/popular`~~ | ~~인기 기업~~ | **삭제됨** — `/api/home`으로 대체 (백엔드에서도 제거) |

### CompanySummary — 필드 변경 🔴
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
  "one_liner": "스마트폰과 반도체를 만드는 회사예요",
  "market_alert": null
}
```
| 필드 | 상태 |
|---|---|
| `one_liner` | 신규 — 이 회사를 한 문장으로 소개. ✅ 구현됨 |
| `market_alert` | 신규 — 투자경고·관리종목 안내, 없으면 `null`. "주의" 배지로 표시. ✅ 구현됨 |
| `has_report` | 삭제됨 — ✅ 백엔드에서도 제거 |
| `corp_code` | `string \| null` — ✅ 맞춤 |

### CompanyDetail
```json
{
  ...CompanySummary,
  "market_cap": { "value": 760, "is_sample": true },
  "homepage_url": "https://www.samsung.com/sec/",
  "dart_url": "https://dart.fss.or.kr/",
  "business_description": "...",
  "business_segments": [{ "code": "DS", "name": "DS(반도체)", "description": "..." }],
  "timeline": [{ "year": "2023", "title": "...", "description": "...", "is_plan": false }],
  "timeline_source": "..."
}
```
- `market_cap`: 프론트는 `SampleNumber | null`(자료 없으면 `null`), ✅ 백엔드도 nullable로 맞춤

---

## 3. 메인 대시보드 🔴 완전히 새로운 엔드포인트

| 메서드 | 경로 | 설명 | 구현 상태 |
|---|---|---|---|
| GET | `/api/home` | 홈 화면 전체 데이터 | ✅ 구현됨 (거래대금·떠오르는·관심도 목록은 `app/data/home.json` 예시 값, `ai_analyzed`는 저장된 리포트 최신 순 6개) |

**응답 형식**
```json
{
  "base_time": "2026-10-07 15:30",
  "trading_value_top": [
    { "...CompanySummary": "...", "trading_value": { "value": 21450, "is_sample": true } }
  ],
  "rising": {
    "criteria": "기준을 정하는 중이에요. 지금은 예시 목록이에요.",
    "items": ["...CompanySummary[]"]
  },
  "most_viewed": {
    "criteria": "다진에서 최근 7일 동안 많이 본 기업 (집계 기준 정하는 중 · 예시 목록)",
    "items": ["...CompanySummary[]"]
  },
  "ai_analyzed": [
    { "stock_code": "005930", "name": "삼성전자", "headline": "AI 한 줄 결론", "generated_at": "2026-10-07T09:00:00+09:00" }
  ]
}
```

**섹션별 설명**
| 섹션 | 내용 | 비고 |
|---|---|---|
| `trading_value_top` | 거래대금 상위 기업 | 시세 연동되면 실제 거래대금으로 교체 |
| `rising` | 떠오르는 기업 | ⚠️ **선정 기준 미정** — 프론트 목데이터 주석에 "기준을 정하는 중이에요"라고 명시. 기준 정해지기 전까지 백엔드도 예시 목록으로 응답해도 됨 |
| `most_viewed` | 관심도 높은 기업 | ⚠️ **집계 기준 미정** — "최근 7일 조회수" 같은 방식 추정되나 확정 필요. 조회 로그를 쌓는 인프라가 없으면 당장 구현 불가 |
| `ai_analyzed` | AI 리포트가 완료(`status: "done"`)된 기업 목록 | **백엔드가 "전체 기업 중 리포트 완료된 것 목록 조회" 기능을 새로 가져야 함** — 지금은 종목코드 하나씩만 조회 가능 |

---

## 4. 재무 · 시세 · 뉴스 (변경 없음)

| 메서드 | 경로 | 구현 상태 |
|---|---|---|
| GET | `/api/companies/{stock_code}/financials` | ✅ 구현됨, 스키마 동일 |
| GET | `/api/companies/{stock_code}/prices?period=1m\|3m\|6m\|1y` | ✅ 구현됨, 스키마 동일 |
| GET | `/api/companies/{stock_code}/news` | ✅ 구현됨, 스키마 동일 |

---

## 5. AI 리포트 🔴 스키마 전면 교체

`frontend/docs/report-schema-proposal.md`(작성: 프론트 노현승, 대상: AI·백엔드, 상태: **"프론트 기본안 · 바꿀 점은 PR 코멘트로"**)에 정의된 안입니다.

| 메서드 | 경로 | 설명 | 구현 상태 |
|---|---|---|---|
| GET | `/api/companies/{stock_code}/report` | 마지막 성공 내용 + 지금 상태 | ✅ 구현됨 |
| POST | `/api/companies/{stock_code}/report` | 새로 만들기 시작 | ✅ 구현됨 (`generating` 즉시 반환 + 백그라운드 생성) |

### 핵심 변경: 상태 기계(State Machine) 도입
이제 "있다/없다(404)"가 아니라 **`status` 필드로 4가지 상태**를 표현합니다.

| status | 의미 | key_points 등 내용물 |
|---|---|---|
| `none` | 한 번도 만든 적 없음 | 비어 있음 |
| `generating` | 만드는 중 (처음) | 비어 있음 |
| `generating` | 다시 만드는 중 | **이전 리포트 내용 그대로** (화면이 안 비게) |
| `done` | 완료 | 새 내용 |
| `failed` | 실패 (처음) | 비어 있음 + `failure_reason` |
| `failed` | 실패 (재생성) | **이전 리포트 내용 그대로** + `failure_reason` |

- `GET`은 더 이상 404를 쓰지 않습니다. 생성한 적 없으면 `200 OK` + `status: "none"` + 빈 내용물로 응답합니다.
- `POST`는 `status: "generating"`을 즉시 반환해도 되고(프론트가 2초 간격으로 `GET` 폴링), 바로 `status: "done"`을 동기 반환해도 됩니다 — **둘 다 프론트가 그대로 처리**합니다 (`useCompanyReport.ts`의 `refetchInterval`).

### 응답 형식
```json
{
  "stock_code": "005930",
  "status": "done",
  "generated_at": "2026-10-07T09:00:00+09:00",
  "headline": "AI용 메모리가 잘 팔리면서 3년 만에 이익이 크게 늘어난 회사예요",
  "key_points": [
    {
      "category": "performance",
      "title": "매출과 이익이 꾸준히 늘었어요",
      "before": { "label": "2023년 매출", "value": "258.94조 원", "period": "2023년" },
      "after": { "label": "2025년 매출", "value": "333.6조 원", "period": "2025년" },
      "reason": "AI용 메모리(HBM) 수요가 늘면서 반도체 사업 실적이 좋아졌어요.",
      "meaning": "회사가 돈을 점점 더 잘 벌고 있다는 뜻이에요.",
      "evidence": ["financials"]
    }
  ],
  "evidence": {
    "financials": {
      "unit": "조 원",
      "years": ["2023", "2024", "2025"],
      "revenue": [258.94, 300.9, 333.6],
      "operating_income": [6.57, 32.7, 43.6],
      "highlights": [{ "label": "2025년 영업이익률", "value": "13.1%", "source": "삼성전자 실적 발표" }],
      "base_date": "2025년 연간 · 연결 기준"
    },
    "news": [
      { "title": "...", "press": "삼성전자 뉴스룸", "published_at": "2026-01-15", "summary": "...", "url": "https://..." }
    ],
    "vision": {
      "summary": "AI 시대를 이끌겠다는 목표를 밝혔어요.",
      "revenue_mix": [{ "name": "DS(반도체)", "amount": 44.0, "unit": "조 원", "period": "2025년 4분기" }],
      "source": "삼성전자 4분기 실적 발표",
      "base_date": "2026년 1월"
    }
  },
  "quiz": [
    { "question": "삼성전자의 2025년 매출은?", "options": ["258.94조 원", "300.9조 원", "333.6조 원"], "answer_index": 2, "explanation": "..." }
  ],
  "sources": ["금융감독원 DART", "한국거래소", "삼성전자 실적 발표"],
  "failure_reason": null
}
```

### `key_points` — 정확히 5개, 항상 같은 순서·카테고리
| category | 의미 |
|---|---|
| `performance` | 실적 (매출·이익 추이) |
| `revenue_source` | 어디서 돈을 버는지 (사업부문별) |
| `recent_change` | 최근 소식·변화 |
| `risk` | 지켜볼 점 |
| `strength` | 강점 |

예전 스키마의 `charts[]`(그래프 종류 선택)는 없어졌습니다 — 대신 `evidence.financials`의 `years/revenue/operating_income` 배열로 프론트가 직접 그래프를 그립니다. **AI는 숫자를 고르지 않고, `before/after/reason/meaning`과 `evidence` 분류만 작성**합니다.

### 이전 스키마 → 새 스키마 매핑

| 이전 (v0.1, 백엔드 현재) | 새 스키마 (v0.2, 프론트 기대) |
|---|---|
| `summary` | `headline` (1문장으로 축약) |
| `key_points[]` (3~5개, 자유 카테고리) | `key_points[]` (정확히 5개, 고정 카테고리 5종) |
| `charts[]` | 삭제 — `evidence.financials.{years,revenue,operating_income}`로 대체 |
| `connection` | 삭제 — 내용은 `key_points[].reason`/`meaning`에 흡수 |
| `strengths[]` / `watch_points[]` | `key_points[category=strength]` / `key_points[category=risk]`로 흡수 |
| `analyzed_sources[]` + `data_sources[]` | `sources[]` 하나로 통합 |
| `disclaimers[]` | 삭제 — 프론트가 고정 문구로 화면에 직접 표시 (API 응답 불필요) |
| (없음) | `status`, `failure_reason` 신규 |
| `is_cached` / `generation_seconds` | 삭제 — `status`로 충분히 표현됨 |

---

## 6. 용어 (Terms) 🔴 스키마 변경

| 메서드 | 경로 | 구현 상태 |
|---|---|---|
| GET | `/api/terms` | ✅ 구현됨 (66개) |

```json
{
  "id": "revenue",
  "term": "매출액",
  "aliases": ["매출"],
  "category": "financial_statement",
  "level": "beginner",
  "easy": "회사가 물건이나 서비스를 팔아서 받은 돈 전체예요.",
  "example": "매출액이 100조 원이면..."
}
```

| 필드 | 변경 내용 |
|---|---|
| `name` → `term` | 이름 변경 |
| `easy_meaning` → `easy` | 이름 변경 |
| `category` | 🔴 신규: `basic` \| `financial_statement` \| `indicator` \| `market` \| `industry` |
| `level` | 🔴 신규: `beginner` \| `intermediate` |

---

## 7. 인증 (Auth) — 변경 없음, 여전히 미구현
`/api/auth/google`, `/api/auth/google/callback`, `/api/auth/me`, `/api/auth/logout` — v0.1과 동일. 아직 어느 쪽도 구현 안 됨.

## 8. 관심 기업 (Watchlist) — 변경 없음, 여전히 미구현
`GET/POST/DELETE /api/watchlist` — v0.1과 동일. 아직 어느 쪽도 구현 안 됨.

## 9. 용어 진행상황 — 변경 없음, 여전히 미구현
`GET/PUT/DELETE /api/terms/progress` — v0.1과 동일.

---

## 10. 광고 자리 (AdSlot) 🔴 신규 기능 — API 불필요
`VITE_SHOW_ADS` 환경변수로 켜고 끄는 광고 자리(`home-between-sections`, `company-overview-bottom` 등)가 추가됨. **순수 프론트 기능이라 백엔드 API가 필요 없습니다.**

## 11. 용어 게임 (Term Game) 🔴 신규 기능 — API 불필요
용어 카드 외에 퀴즈 형식의 "용어 게임"이 추가됨(`features/term-game/`). 기존 `GET /api/terms` 데이터로 프론트가 자체적으로 문제를 만들어서(`buildQuestions.ts`), **별도 API 불필요**.

## 12. 커뮤니티 탭 🔴 신규 탭 — API 불필요 (자리만)
기업 상세에 "커뮤니티" 탭이 새로 생겼지만 `CommunityTab.tsx`에 "MVP 범위 밖, 자리만 만들어 둠"이라고 명시되어 있어 **지금은 빈 안내 문구만 표시**. API 설계는 추후 과제.

---

## 13. 에러 코드

| 코드 | 상태 | 의미 |
|---|---|---|
| `COMPANY_NOT_FOUND` | 404 | 없는 종목코드 |
| `DATA_NOT_FOUND` | 404 | 재무 자료가 아직 없음 (`/financials`) |
| `VALIDATION_ERROR` | 422 | 쿼리/바디 형식 오류 |
| ~~`REPORT_NOT_READY`~~ | ~~404~~ | 🔴 새 스키마에서는 불필요 — 리포트 미지원 회사도 `status: "none"`으로 응답 (지원 여부 판단 기준 자체를 재검토 필요) |
| ~~`REPORT_NOT_FOUND`~~ | ~~404~~ | 🔴 새 스키마에서는 불필요 — `status: "none"`으로 대체 |

---

## 14. 백엔드 현재 구현 상태 요약 (v0.3, 2026-10-12 기준)

| 구분 | 상태 |
|---|---|
| 기업 검색/상세/재무/시세/뉴스 | ✅ 구현됨 (`one_liner`/`market_alert` 포함, 재무 자료 없으면 `404 DATA_NOT_FOUND`) |
| 종목코드 일괄 조회 | ✅ 구현됨 |
| AI 리포트 (새 스키마 + `none/generating/done/failed` 상태) | ✅ 구현됨 — 숫자(`evidence`)는 서버가 조립, AI는 문장만 |
| `/api/home` 홈 대시보드 | ✅ 구현됨 (선정 기준 미정인 목록은 예시 값) |
| 용어 사전 | ✅ 구현됨 (새 필드명, 66개) |
| 인증 / 관심기업 / 용어 진행상황 | 🔴 미구현 (v0.1과 동일) |

**남은 결정 사항**: `rising`·`most_viewed` 선정 기준, 리포트를 언제 새로 만들지(유효 기간), 재무 자료가 없는 회사의 리포트 지원 범위(현재는 바로 `failed`).
