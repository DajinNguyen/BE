# 다진(dajin) — 주식 트레이딩 기업별 분석 애플리케이션

## 1. 프로젝트 소개

주식을 처음 시작하는 초보 투자자(특히 10대 포함)를 대상으로, 관심 기업을 검색하면
재무제표·시세·뉴스 등을 기반으로 **AI가 쉬운 말로 풀어쓴 기업 분석 리포트**를 자동
생성해주는 서비스. 전문 용어 대신 초보자가 이해할 수 있는 설명을 우선한다.

- **타겟 사용자**: 주식 초보자 전반 (10대 포함, 특정 연령대에 국한하지 않음)
- **핵심 가치**: "어려운 재무 정보를 쉬운 말로" — 전문가용 HTS/증권 앱과 차별화
- **개발 기간**: 2주 내외 (MVP 범위로 완성도보다 핵심 흐름 검증 우선)

## 2. MVP 핵심 기능

1. 기업 검색 → 재무제표/시세 데이터 조회
2. AI 기반 기업 분석 리포트 자동 생성 (초보자 눈높이 요약)
3. 리포트 결과 화면에서 보기 좋게 시각화

우선순위 밖 (2주 MVP 범위 제외): 모의투자, 커뮤니티, 알림, 로그인 없는 게스트 이용 등은
추후 확장 과제로 남겨둔다.

## 3. 팀 구성 및 역할 분담

3명 모두 풀스택 개발 가능, 특정 분야 특화 없음 → 레이어(기술 계층) 기준으로 분담.

| 담당 | 역할 | 주요 작업 |
|---|---|---|
| 팀원 1 | 프론트엔드 | UI/UX, 리포트 화면, 초보자 친화적 화면 구성 |
| 팀원 2 | 백엔드 · 데이터 | 재무·시세 데이터 수집(API 연동), DB 설계, 인증 |
| 팀원 3 | AI · 리포트 엔진 | 프롬프트 설계, 리포트 생성 로직, 데이터→분석 파이프라인 |

**협업 리듬 (2주)**
- 1일차: API 스펙(요청/응답 포맷) 3인 합의 후 병렬 작업 시작
- 4~5일차: 1차 통합 테스트 (세 파트 연결 확인)
- 마지막 2~3일: UX 다듬기 + 데모 준비 (초보자 관점에서 용어·설명 재점검)

## 4. 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| 백엔드 | FastAPI (Python 3.11+) | 비동기 지원, 자동 API 문서(Swagger) |
| ORM | SQLModel | Pydantic + SQLAlchemy 통합, 스키마·모델 중복 제거 |
| DB | PostgreSQL | Docker Compose로 로컬 구동 |
| 인증 | JWT (python-jose, passlib) | 2주 MVP는 이메일/비밀번호 기반 단순 인증 |
| 테스트(백엔드) | pytest | 핵심 API·리포트 생성 로직 위주로만 작성 |
| 프론트엔드 | React + TypeScript + Vite | 빠른 개발 서버, 타입 안전성 |
| 데이터 페칭 | TanStack Query | 로딩/에러 상태 관리 단순화 |
| 테스트(프론트) | Vitest + React Testing Library | 핵심 컴포넌트 위주로만 작성 |
| AI 리포트 생성 | Anthropic Claude API (anthropic SDK) | 프롬프트로 초보자 눈높이 리포트 생성 |
| 재무 데이터 소스 | OpenDART API | 금융감독원 전자공시, 재무제표 |
| 시세 데이터 소스 | pykrx / FinanceDataReader | 국내 주식 시세, 별도 인증 불필요 |
| 인프라 | Docker Compose + Nginx | backend/frontend/db 컨테이너 + 리버스 프록시 |
| CI | GitHub Actions | lint + test만 (2주 규모에 맞춰 배포 파이프라인은 생략) |

## 5. 폴더 구조 (제안)

```
dajin/
├── backend/
│   ├── app/
│   │   ├── api/            # 라우터 (기능 단위)
│   │   ├── models/          # SQLModel 모델
│   │   ├── schemas/          # Pydantic 요청/응답 스키마
│   │   ├── services/        # 비즈니스 로직 (데이터 수집, 리포트 생성)
│   │   └── core/            # 설정, 인증, DB 세션
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── api/             # API 클라이언트 함수
│   └── tests/
├── docker-compose.yml
└── .github/workflows/
```

## 6. 코딩 컨벤션

### 공통
- 커밋 메시지: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`)
- 브랜치: `main` + `feature/기능명`, PR은 팀원 1명 이상 리뷰 후 머지
- 환경 변수: `.env` 사용, 절대 커밋하지 않음 (`.env.example`만 커밋)

### 백엔드 (Python)
- 포맷터/린터: Ruff + Black
- 네이밍: 변수/함수 `snake_case`, 클래스 `PascalCase`, 파일명 `snake_case`
- API 응답 JSON 키는 `snake_case`로 통일 (Python 쪽과 변환 오버헤드 없이 일치)
- API 경로: 복수형 리소스명, kebab-case (`/api/companies`, `/api/reports/{id}`)

### 프론트엔드 (TypeScript/React)
- 포맷터/린터: ESLint + Prettier
- 네이밍: 컴포넌트 파일/컴포넌트명 `PascalCase`, 그 외 함수/변수 `camelCase`
- 커스텀 훅은 `use` 접두사 (`useCompanyReport`)
- API 호출은 `src/api/` 아래 함수로만 하고 컴포넌트에서 직접 fetch 호출 금지

## 7. 실행 방법 (예정)

스캐폴딩 완료 후 아래 방식으로 실행한다 (실제 스크립트는 초기 세팅 시 확정):

```
docker compose up          # DB 포함 전체 스택 로컬 구동
cd backend && uvicorn app.main:app --reload
cd frontend && npm run dev
```
