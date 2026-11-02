# 다진(dajin) 백엔드

프론트엔드(`DajinNguyen/FE` main) API 계약(`docs/API_SPEC.md` v0.2 + FE `docs/frontend-data-requirements.md`, 2026-10-19)에 맞춘 FastAPI 서버예요. 로그인은 Google로 해요.

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

## Google 로그인

FE `docs/auth-api-request.md`에 적힌 흐름 그대로예요: `GET /api/auth/google/login` → Google → 콜백 → JWT 쿠키 → `GET /api/auth/me`.

| 메서드 | 경로 | 내용 |
| --- | --- | --- |
| GET | `/api/auth/google/login?redirect=<화면 경로>` | Google 로그인 화면으로 이동해요. 프론트는 이 주소로 페이지를 이동시키면 돼요. `redirect`는 `/`로 시작하는 우리 화면 경로만 받아요(`//evil.com`, `https://…`이면 `/`). 프론트 주소는 요청한 화면(Referer)이 `FRONTEND_ORIGINS`에 있으면 그 주소, 아니면 첫 번째 주소예요. |
| GET | `/api/auth/google?next=<프론트 전체 주소>` | 예전 방식. 그대로 동작해요. |
| GET | `/api/auth/google/callback` | Google이 돌려보내는 곳. 성공하면 로그인 쿠키를 심고 `next`로, 실패하면 `next?login_error=<이유>`로 보내요. |
| GET | `/api/auth/me` | `{ "id", "email", "name", "nickname" }`. 로그인하지 않았으면 `401 LOGIN_REQUIRED` |
| POST | `/api/auth/logout` | 로그인 쿠키를 지워요 (`204`) |

- 설정: `.env`에 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SECRET`을 넣어요. Google Cloud Console의 OAuth 클라이언트(웹 애플리케이션)에 승인된 리디렉션 URI로 `http://localhost:8000/api/auth/google/callback`을 등록해야 해요. 키가 없으면 `/api/auth/google`은 `503 AUTH_NOT_CONFIGURED`예요.
- 받는 정보는 이메일·이름뿐이에요(`openid email profile`). Google이 확인한 이메일만 받아요. 사용자는 `users` 테이블에 저장해요.
- 로그인 쿠키 `dajin_session`: HttpOnly, SameSite=Lax, 기본 7일(`SESSION_DAYS`). 프론트는 요청에 `credentials: 'include'`만 있으면 돼요 (이미 그렇게 돼 있어요).
- 보안: state 값으로 다른 사이트에서 위조한 로그인 요청을 막고, `next`는 허용한 프론트 주소(`FRONTEND_ORIGINS`)로만 돌려보내요.
- `login_error` 값: `cancelled`(사용자가 취소), `state_mismatch`·`state_missing`·`state_invalid`(10분이 지났거나 위조), `email_not_verified`, `google_error`
- 닉네임(`nickname`): 실명 대신 커뮤니티·상단 메뉴에 보여요(10대 사용자 보호). 사용자 번호로 자동으로 만들어서(예: `반짝이는다람쥐1`) 겹치지 않고, 따로 저장하지 않아요. 직접 정하게 할지는 아직 정하지 않았어요.
- 다른 API에서 로그인 사용자가 필요하면 `app/api/deps.py`의 `current_user`(필수, 아니면 `401 LOGIN_REQUIRED`) / `optional_user`(선택)를 쓰면 돼요.

## 프론트와 연결

프론트 `frontend/.env`에 아래처럼 넣고 `npm run dev`를 실행해요. 화면 코드는 고치지 않아도 돼요.

```
VITE_USE_MOCK=false
VITE_API_BASE_URL=http://localhost:8000
```

CORS는 `http://localhost:5173`, `http://localhost:5174`를 허용해 둬서 이 두 포트에서 띄워야 해요.

## 에러 형식

모든 API의 에러는 FE `docs/frontend-data-requirements.md` 0장 형식이에요. `message`는 화면에 그대로 보여줄 수 있는 쉬운 문장이에요.

```json
{ "error": { "code": "COMPANY_NOT_FOUND", "message": "찾는 회사가 없어요. 종목코드를 다시 확인해 주세요." } }
```

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
| GET | `/api/companies/{stock_code}/news?page=1&size=10` |
| GET / POST | `/api/companies/{stock_code}/report` |
| GET | `/api/industries/hot?period=1w\|1m` · `/api/industries/{industry_id}` · `/api/industries/{industry_id}/news?size=5` |
| GET | `/api/market/summary` · `/api/market/news?date=YYYY-MM-DD` |
| GET | `/api/terms` |
| GET | `/api/learn/crosswords` · `/api/learn/crosswords/{puzzle_id}` |
| GET / POST | `/api/auth/google/login` · `/api/auth/me` · `/api/auth/logout` |
| GET / POST | `/api/companies/{stock_code}/posts` (커뮤니티, 아래 참고) |

## 데이터

- 회사(300곳, 코스피·코스닥 시가총액 상위 기준으로 고름)·재무·뉴스·용어(70개)·낱말퀴즈(3개)·홈 목록은 `app/data/*.json` 시드 데이터예요. 프론트 목 데이터(`frontend/src/mocks`)와 같은 값이에요. OpenDART·pykrx 연동은 이후 파트에서 해요.
- 재무는 DART 키가 있으면 DART 재무제표(금융회사 제외 36곳)를 쓰고, 없으면 시드(삼성전자 `005930`만)를 써요. 둘 다 없으면 `404 DATA_NOT_FOUND`예요.
- 뉴스는 실제 기사를 최신순으로 보여줘요. `.env`에 네이버 검색 API 키(`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, NAVER API HUB에서 발급)가 있으면 네이버를, 없거나 받지 못하면 Google 뉴스 RSS(키·가입 없음, 최근 7일)를 써요. Google 뉴스 RSS는 개인적·비상업적 용도로만 쓸 수 있어서, 서비스로 내보낼 때는 네이버 키를 넣어야 해요. 기업 뉴스는 회사 이름, 시장 뉴스는 "증시", 산업 뉴스는 "산업 이름 산업"으로 검색하고, 같은 검색은 10분 캐시해요. 제목·요약·원문 링크만 보내요(Google 뉴스는 요약이 없어요). 둘 다 받지 못하면 시드·예시 뉴스를 써요(시드 뉴스는 삼성전자만 있어요). 응답 형식은 FE `docs/news-api-request.md`(`{ items, page, size, has_next }`)이고, 리포트 근거 뉴스(`evidence.news`)도 같은 형식이에요. 예전 형식으로 저장된 리포트도 읽을 때 바꿔서 돌려줘요.
- 시세(현재가·등락률·시가총액·주가 흐름)와 홈 거래대금 순위는 네이버 시세를 써요. 떠오르는 기업·관심도 높은 기업은 기준이 정해질 때까지 예시 데이터(`is_sample: true`)예요.
- 홈 거래대금 순위(최대 30개)는 다진이 다루는 300개 회사 안에서의 순위예요. 시장 전체 순위가 아니에요.
- 시세 기준일(`price_as_of`, 홈 `base_time`)은 네이버가 준 마지막 체결 시각이에요. 장중이면 그 시각, 끝났으면 "그날 15:30"이에요. 시세를 받지 못하면 직전 장 마감일(평일 15:30 기준, 공휴일은 아직 빼지 않음)이에요.
- 업종 탭(`sector_companies`)은 회사마다 정한 `sector_group`으로 묶고, 탭 안은 시가총액 순이에요. 분류는 프론트 목 데이터와 같아요.
- 홈 거래대금 목록의 `ai_headline`은 그 회사의 마지막 성공 리포트 `headline`이에요. 리포트가 없으면 `null`이에요.
- 재무의 `recent_performance`(최근 사업 성과)는 DART 재무제표 API에 없어서 시드 값을 써요(지금 삼성전자만). 작년 대비 변화율은 예시 값(`yoy_is_sample: true`)이에요. 없으면 `null`이에요.
- PER·PBR·ROE·부채비율(`indicators`)은 DART 재무를 받으면 계산한 값이고, 시드만 쓸 때는 예시 값이에요.

## 커뮤니티 (기업별 게시판)

FE `docs/community-api-request.md` 형식 그대로예요. **보기는 누구나, 쓰기는 로그인한 사람만**이에요.

| 메서드 | 경로 | 로그인 |
| --- | --- | --- |
| GET | `/api/companies/{stock_code}/posts?sort=latest\|popular&category=&page=1&size=10` | 필요 없음 |
| GET | `/api/posts/{post_id}` · `/api/posts/{post_id}/comments` | 필요 없음 |
| POST | `/api/companies/{stock_code}/posts` (글쓰기) | 필요 |
| PUT / DELETE | `/api/posts/{post_id}` (수정·삭제, 작성자만) | 필요 |
| POST | `/api/posts/{post_id}/comments` · DELETE `/api/comments/{comment_id}` | 필요 |
| POST / DELETE | `/api/posts/{post_id}/like` (여러 번 보내도 결과가 같아요) | 필요 |
| POST | `/api/reports` (글·댓글 신고) | 필요 |

- 로그인하지 않고 쓰면 `401 LOGIN_REQUIRED`예요.
- 서버 검사: 제목 1~50자·본문 1~2,000자·댓글 1~500자(`400 INVALID_LENGTH`), 금지어(`400 BANNED_WORD`, 목록은 FE `communityRules.ts`와 같아요), 같은 사람은 글을 30초에 한 번(`429 TOO_FREQUENT`), 같은 대상은 한 번만 신고(`409 ALREADY_REPORTED`), 남의 글·댓글 수정·삭제(`403 NOT_OWNER`).
- 서로 다른 사람의 신고가 3건 쌓이면 숨겨요(`is_hidden: true`, 제목·본문은 빈 문자열). 숨긴 글을 다시 살리는 관리자 화면은 아직 없어요.
- 인기순은 좋아요 많은 순(전체 기간), 같으면 최신순이에요.
- 글을 지우면 댓글·좋아요·신고 기록도 함께 지워요.
- 테이블: `community_posts`, `community_comments`, `community_likes`, `community_reports` (서버를 켤 때 자동으로 만들어요).

## AI 기업 개요 (사업보고서 정리)

시드에 사업부문·회사의 방향이 없는 회사는 DART 최신 사업보고서의 "II. 사업의 내용"을 AI가 쉬운 말로 정리해서 채워요.

- 채우는 칸: `business_segments`(사업부문 2~5개), `timeline`(최근 흐름 + 계획 1개), `timeline_source`(예: "SK하이닉스 2025년 사업보고서 (2026년 3월 공시)"), 시드에 없으면 `business_description`.
- 상세 화면(`GET /api/companies/{stock_code}`)을 처음 열면 백그라운드에서 만들기 시작하고, 다음 요청부터 채워져요. 결과는 `company_overviews` 테이블에 회사별로 저장해요.
- 미리 한꺼번에 만들기: `python -m app.scripts.generate_overviews` (한 곳씩, 사이에 13초 쉬어요. 지정한 회사만 다시: `... generate_overviews 000660`, 최대 개수: `--limit 10`, 유료 키면 `--interval 0`)
- Gemini 무료 등급은 분당 5회·하루 20회예요. AI 리포트와 한도를 같이 쓰니, 무료 키로는 하루에 몇 곳씩 나눠 만들어요. 한도(429)에 걸리면 멈추고, 다시 실행하면 이어서 만들어요.
- 필요한 키: `DART_API_KEY` + `ANTHROPIC_API_KEY` 또는 `GEMINI_API_KEY`. AI 키가 없으면 만들지 않고 시드 값만 보여줘요.
- 숫자는 사업보고서 글과 DART 연도별 재무에 있는 값만 쓰게 하고, 회사의 방향의 지난 연도는 DART 재무가 있는 연도만 남겨요. 실패하면 1시간 뒤에 다시 시도해요.
- 사업보고서 원문에서 잘라낸 글은 `.cache/dart/business_<접수번호>.txt`에 캐시해요.

## 산업

- 산업 10개(AI·반도체·조선·방산·2차전지·바이오·인터넷·자동차·금융·게임)의 설명·관련 기업·기간별 변화율은 `app/data/industries.json` 시드예요. 프론트 목 데이터와 같은 값이에요.
- 뜨는 산업 점수 = 주가 상승률 40% + 거래대금 증가율 30% + 뉴스 언급 증가율 30%, 상위 3개 `high`·다음 3개 `mid`·나머지 `low` (FE `config/industryHeat.ts`와 같은 임시값, PM 확정 필요).
- 변화율·점수·흐름 그래프·산업 뉴스는 아직 집계하지 않아서 예시 값이에요(`is_sample: true`, 뉴스는 언론사 "예시 뉴스"·주소 example.com).
- 산업 페이지 관련 기업의 시가총액·등락률은 네이버 시세를 받으면 실제 값이고, 못 받으면 시드 예시 값이에요. 시가총액 큰 순이에요.
- 기업 상세 `industries`(소속 산업 칩)도 같은 시드에서 찾아요.

## 오늘의 시장

- 지수(코스피·코스닥)·한마디·움직인 이유·시장 뉴스는 아직 실제 자료(지수 시세, AI 정리)를 붙이지 않아서 `app/data/market.json` 예시 값이에요(`is_sample: true`).
- `as_of`는 마지막 장 마감 시각(평일 15:30), `is_previous_session`은 오늘 장이 아직 안 끝났거나 휴장일이면 `true`예요.
- `date`가 YYYY-MM-DD가 아니면 `400 INVALID_PARAM`이에요.

## AI 리포트

- `POST`는 생성을 시작하고 바로 `status: "generating"`을 돌려줘요. 실제 생성은 백그라운드에서 하고, 프론트가 2초마다 `GET`으로 확인해요.
- `GET`은 마지막으로 성공한 내용 + 지금 상태(`none` / `generating` / `done` / `failed`)를 돌려줘요. 다시 만드는 중이거나 실패해도 이전 내용은 그대로 담아요.
- AI가 쓴 설명(제목·핵심 포인트 문장·퀴즈)은 저장한 그대로 쓰고, 근거 숫자(재무 그래프·주요 숫자·상세 시트)·뉴스·자료 기준일은 `GET` 할 때마다 지금 자료(DART·뉴스·시세)로 다시 붙여요. 핵심 포인트 문장 속 숫자는 AI가 쓴 글이라 다시 만들어야 바뀌어요.
- 숫자(`evidence`)는 서버가 시드 데이터에서 그대로 조립하고, AI는 문장(`headline`, `key_points`, 퀴즈 등)만 써요.
- 핵심 포인트의 근거(`key_points[].evidence`)는 AI가 종류(`financials`/`news`/`vision`)만 고르고, 서버가 칩 이름·날짜(`{type, label, date}`)를 붙여요. 예전 형식(종류 이름만)으로 저장된 리포트도 읽을 때 바꿔서 돌려줘요.
- 핵심 포인트의 상세 시트(`key_points[].detail`)는 서버가 채워요: 연도별 매출·영업이익(최근 5년까지), 근거에 뉴스가 있으면 관련 뉴스 3건, 근거에 재무가 있으면 최근 실적 발표 공시.
- `data_as_of`에는 리포트를 만들 때 쓴 재무 기준·주가 기준일·최신 뉴스 날짜가 담겨요.
- 재무 자료가 없는 회사는 AI를 부르지 않고 바로 `failed`로 알려줘요. 5분이 지나도 끝나지 않은 생성(서버 재시작 등)은 `failed`로 바뀌어요.
- Gemini는 리포트 한 건에 50초 넘게 걸리기도 해서 요청당 90초까지 기다리고, 최대 3번 시도해요. Gemini가 붐비면(503·504) 실패까지 최대 약 4분 30초가 걸리고, 예시 리포트가 없는 회사(삼성전자 외)는 `failed`가 돼요.
- 결과는 `ai_reports` 테이블에 회사별로 1건만 저장돼요. 예전 `company_reports` 테이블은 더 이상 쓰지 않아요 (기존 DB에 남아 있어도 무해해요).
