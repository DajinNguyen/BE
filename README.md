# 다진(dajin) 백엔드

프론트엔드(`DajinNguyen/FE` main) API 계약(`docs/API_SPEC.md` v0.2 + FE `docs/frontend-data-requirements.md`, 2026-10-19)에 맞춘 FastAPI 서버예요. 인증은 없어요.

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # DATABASE_URL, ANTHROPIC_API_KEY 설정
uvicorn app.main:app --reload
```

- `DATABASE_URL`을 비워두면 로컬 SQLite(`dajin.db`)를 써요.
- API 문서: http://localhost:8000/docs
- AI 키(`ANTHROPIC_API_KEY` > `GEMINI_API_KEY`)가 하나도 없으면 삼성전자 예시 리포트(`app/data/report_samsung.json`)로 동작해요.

## DART 연동

`.env`에 `DART_API_KEY`를 넣으면 OpenDART를 함께 써요. 키가 없거나 DART가 응답하지 않으면 시드 데이터만으로 동작해요.

- 서버 시작 시 상장 회사 고유번호 목록(`corpCode.xml`)을 받아 `.cache/dart/`에 하루 동안 캐시해요.
- 검색: 회사 이름·영문명·종목코드에 더해 **DART 공식 회사명·영문명**으로도 찾아요 (예: `현대자동차` → 현대차, `케이티` → KT, `KIA CORPORATION` → 기아).
- 모든 회사의 `corp_code`(DART 고유번호)를 DART 값으로 채워요.
- 기업 상세: 시드에 홈페이지가 없으면 DART 기업개황(`company.json`)으로 채워요 (회사별 일주일 캐시).
- 재무: 단일회사 주요계정(`fnlttSinglAcnt.json`)으로 재무 탭과 AI 리포트를 채워요 (보고서별 하루 캐시, `app/services/dart_financials.py`).
  - 연간 실적은 가장 최근 사업보고서 하나로 3년치(당기·전기·전전기), 아직 공시 전이면 그 전 해 것을 써요.
  - 최근 분기는 사업보고서보다 새로운 분기·반기 보고서 중 가장 최근 것의 **3개월** 실적이에요 (`thstrm_amount`, 누적은 `thstrm_add_amount`).
  - 연결재무제표를 먼저 쓰고, 없으면 별도재무제표를 써요.
  - ROE·부채비율은 순이익·부채·자본으로, PER·PBR은 시가총액 ÷ 순이익·자본으로 계산해요. 적자 회사는 PER이 음수로 나와요.
  - 프론트가 금액을 항상 "조 원"으로 보여줘서 작은 회사도 조 단위로 보내요. DART에는 사업부문별 매출이 없어서 시드에 있으면(삼성전자) 합치고, 없으면 빈 목록이에요.
  - ⚠️ 금융회사 5곳(KB금융·신한지주·하나금융지주·우리금융지주·삼성생명)은 주요계정에 매출·영업수익이 없어서 아직 `404 DATA_NOT_FOUND`예요.
- 검색 대상은 시드에 있는 41개 회사예요. DART에는 주가가 없어서, 시세가 없는 회사는 아직 프론트 화면에 띄울 수 없어요.

## 네이버 시세 연동 (비공식)

키 없이 네이버 금융 시세를 받아요. 끄려면 `.env`에 `NAVER_PRICES_ENABLED=false`.

- 현재가·등락률·거래대금·시가총액: 41개 회사를 한 번에 받고 1분 동안 캐시해요. 장중에는 거의 실시간이에요.
- 주가 흐름(`prices`): 1달·3달·6달은 일별, 1년은 주별 종가예요. 종목·기간별로 1시간 캐시해요.
- 받은 값은 `is_sample: false`예요. 네이버가 응답하지 않으면 1분 동안 다시 묻지 않고, 시드 예시 값(`is_sample: true`)으로 동작해요.
- ⚠️ 공식 API가 아니라서 예고 없이 바뀌거나 막힐 수 있어요. 데모 전에 한 번 확인하고, 정식 서비스라면 증권사·공공데이터 API로 바꿔야 해요.

## 프론트와 연결

프론트 `frontend/.env`에 아래처럼 넣고 `npm run dev`를 실행해요. 화면 코드는 고치지 않아도 돼요.

```
VITE_USE_MOCK=false
VITE_API_BASE_URL=http://localhost:8000
```

CORS는 `http://localhost:5173`, `http://localhost:5174`를 허용해 둬서 이 두 포트에서 띄워야 해요.

## 테스트 · 린트

```bash
pytest
ruff check app tests && black --check app tests
```

## 엔드포인트

| 메서드 | 경로 |
| --- | --- |
| GET | `/api/home` |
| GET | `/api/companies?query=&limit=20` · `?stock_codes=a,b` |
| GET | `/api/companies/{stock_code}` |
| GET | `/api/companies/{stock_code}/financials` |
| GET | `/api/companies/{stock_code}/prices?period=1m\|3m\|6m\|1y` |
| GET | `/api/companies/{stock_code}/news` |
| GET / POST | `/api/companies/{stock_code}/report` |
| GET | `/api/terms` |
| GET | `/api/learn/crosswords` · `/api/learn/crosswords/{puzzle_id}` |

## 데이터

- 회사(41곳)·재무·뉴스·용어(70개)·낱말퀴즈(3개)·홈 목록은 `app/data/*.json` 시드 데이터예요. 프론트 목 데이터(`frontend/src/mocks`)와 같은 값이에요. OpenDART·pykrx 연동은 이후 파트에서 해요.
- 재무는 DART 키가 있으면 DART 재무제표(금융회사 제외 36곳)를 쓰고, 없으면 시드(삼성전자 `005930`만)를 써요. 둘 다 없으면 `404 DATA_NOT_FOUND`예요.
- 뉴스 자료는 지금 삼성전자만 있어요. 자료가 없는 회사의 뉴스는 `[]`예요.
- 시세(현재가·등락률·시가총액·주가 흐름)와 홈 거래대금 순위는 네이버 시세를 써요. 떠오르는 기업·관심도 높은 기업은 기준이 정해질 때까지 예시 데이터(`is_sample: true`)예요.
- 홈 거래대금 순위(최대 30개)는 다진이 다루는 41개 회사 안에서의 순위예요. 시장 전체 순위가 아니에요.
- 시세 기준일(`price_as_of`, 홈 `base_time`)은 네이버가 준 마지막 체결 시각이에요. 장중이면 그 시각, 끝났으면 "그날 15:30"이에요. 시세를 받지 못하면 직전 장 마감일(평일 15:30 기준, 공휴일은 아직 빼지 않음)이에요.
- 업종 탭(`sector_companies`)은 회사마다 정한 `sector_group`으로 묶고, 탭 안은 시가총액 순이에요. 분류는 프론트 목 데이터와 같아요.
- 홈 거래대금 목록의 `ai_headline`은 그 회사의 마지막 성공 리포트 `headline`이에요. 리포트가 없으면 `null`이에요.
- PER·PBR·ROE·부채비율(`indicators`)은 DART 재무를 받으면 계산한 값이고, 시드만 쓸 때는 예시 값이에요.

## AI 리포트

- `POST`는 생성을 시작하고 바로 `status: "generating"`을 돌려줘요. 실제 생성은 백그라운드에서 하고, 프론트가 2초마다 `GET`으로 확인해요.
- `GET`은 마지막으로 성공한 내용 + 지금 상태(`none` / `generating` / `done` / `failed`)를 돌려줘요. 다시 만드는 중이거나 실패해도 이전 내용은 그대로 담아요.
- 숫자(`evidence`)는 서버가 시드 데이터에서 그대로 조립하고, AI는 문장(`headline`, `key_points`, 퀴즈 등)만 써요.
- 핵심 포인트의 근거(`key_points[].evidence`)는 AI가 종류(`financials`/`news`/`vision`)만 고르고, 서버가 칩 이름·날짜(`{type, label, date}`)를 붙여요. 예전 형식(종류 이름만)으로 저장된 리포트도 읽을 때 바꿔서 돌려줘요.
- `data_as_of`에는 리포트를 만들 때 쓴 재무 기준·주가 기준일·최신 뉴스 날짜가 담겨요.
- 재무 자료가 없는 회사는 AI를 부르지 않고 바로 `failed`로 알려줘요. 5분이 지나도 끝나지 않은 생성(서버 재시작 등)은 `failed`로 바뀌어요.
- Gemini는 리포트 한 건에 50초 넘게 걸리기도 해서 요청당 90초까지 기다리고, 최대 3번 시도해요. Gemini가 붐비면(503·504) 실패까지 최대 약 4분 30초가 걸리고, 예시 리포트가 없는 회사(삼성전자 외)는 `failed`가 돼요.
- 결과는 `ai_reports` 테이블에 회사별로 1건만 저장돼요. 예전 `company_reports` 테이블은 더 이상 쓰지 않아요 (기존 DB에 남아 있어도 무해해요).
