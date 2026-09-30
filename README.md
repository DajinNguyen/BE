# 다진(dajin) 백엔드

API 명세서 Part 1(MVP 핵심) 범위를 구현한 FastAPI 서버예요. 인증은 없어요.

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # DATABASE_URL, ANTHROPIC_API_KEY 설정
uvicorn app.main:app --reload
```

- `DATABASE_URL`을 비워두면 로컬 SQLite(`dajin.db`)를 써요.
- API 문서: http://localhost:8000/docs

## 테스트 · 린트

```bash
pytest
ruff check app tests && black --check app tests
```

## 엔드포인트

| 메서드 | 경로 |
| --- | --- |
| GET | `/api/companies?query=` |
| GET | `/api/companies/popular` |
| GET | `/api/companies/{stock_code}` |
| GET | `/api/companies/{stock_code}/financials` |
| GET | `/api/companies/{stock_code}/prices?period=1m\|3m\|6m\|1y` |
| GET | `/api/companies/{stock_code}/news` |
| GET / POST | `/api/companies/{stock_code}/report` |
| GET | `/api/terms` |

## 데이터

- 회사·재무·뉴스·용어는 `app/data/*.json` 시드 데이터예요. OpenDART·pykrx 연동은 이후 파트에서 해요.
- 주가(`prices`)는 연동 전까지 예시 데이터(`is_sample: true`)예요.
- AI 리포트는 삼성전자(`005930`)만 지원해요. 다른 회사는 `404 REPORT_NOT_READY`예요.
- 리포트 생성은 Claude API를 호출하고, 결과는 `company_reports` 테이블에 회사별로 1건만 저장돼요.
