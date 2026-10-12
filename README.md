# 다진(dajin) 백엔드

프론트엔드(`DajinNguyen/FE` main) API 계약(`docs/API_SPEC.md` v0.2)에 맞춘 FastAPI 서버예요. 인증은 없어요.

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

## 데이터

- 회사(35곳)·재무·뉴스·용어(66개)·홈 목록은 `app/data/*.json` 시드 데이터예요. 프론트 목 데이터(`frontend/src/mocks`)와 같은 값이에요. OpenDART·pykrx 연동은 이후 파트에서 해요.
- 재무·뉴스 자료는 지금 삼성전자(`005930`)만 있어요. 자료가 없는 회사의 재무는 `404 DATA_NOT_FOUND`, 뉴스는 `[]`예요.
- 주가(`prices`)와 홈의 거래대금·떠오르는 기업·관심도 높은 기업은 연동 전까지 예시 데이터(`is_sample: true`)예요.

## AI 리포트

- `POST`는 생성을 시작하고 바로 `status: "generating"`을 돌려줘요. 실제 생성은 백그라운드에서 하고, 프론트가 2초마다 `GET`으로 확인해요.
- `GET`은 마지막으로 성공한 내용 + 지금 상태(`none` / `generating` / `done` / `failed`)를 돌려줘요. 다시 만드는 중이거나 실패해도 이전 내용은 그대로 담아요.
- 숫자(`evidence`)는 서버가 시드 데이터에서 그대로 조립하고, AI는 문장(`headline`, `key_points`, 퀴즈 등)만 써요.
- 재무 자료가 없는 회사는 AI를 부르지 않고 바로 `failed`로 알려줘요. 5분이 지나도 끝나지 않은 생성(서버 재시작 등)은 `failed`로 바뀌어요.
- 결과는 `ai_reports` 테이블에 회사별로 1건만 저장돼요. 예전 `company_reports` 테이블은 더 이상 쓰지 않아요 (기존 DB에 남아 있어도 무해해요).
