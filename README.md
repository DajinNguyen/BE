# 다진(dajin) — Backend

주식 초보자를 위한 기업별 분석 리포트 서비스 **다진(dajin)**의 백엔드 저장소입니다.

## 소개

기업 검색, 재무제표/시세 데이터 수집, AI 기반 초보자 눈높이 분석 리포트 생성을 담당합니다.
전문 용어 대신 쉬운 말로 풀어쓴 리포트를 만드는 것이 목표입니다.

## 주요 역할

- 재무·시세 데이터 수집 (OpenDART, pykrx/FinanceDataReader 연동)
- DB 설계 및 저장
- 이메일/비밀번호 기반 JWT 인증
- AI 리포트 생성 API (Anthropic Claude API 연동)

## 기술 스택

| 영역 | 선택 |
|---|---|
| 프레임워크 | FastAPI (Python 3.11+) |
| ORM | SQLModel |
| DB | PostgreSQL (Docker Compose) |
| 인증 | JWT (python-jose, passlib) |
| 테스트 | pytest |
| 린트/포맷 | Ruff + Black |
| AI 리포트 생성 | Anthropic Claude API (anthropic SDK) |
| 재무 데이터 소스 | OpenDART API |
| 시세 데이터 소스 | pykrx / FinanceDataReader |

## 폴더 구조

```
backend/
├── app/
│   ├── api/          # 라우터 (기능 단위)
│   ├── models/        # SQLModel 모델
│   ├── schemas/        # Pydantic 요청/응답 스키마
│   ├── services/       # 비즈니스 로직 (데이터 수집, 리포트 생성)
│   └── core/          # 설정, 인증, DB 세션
└── tests/
```

## 코딩 컨벤션

- 변수/함수: `snake_case`, 클래스: `PascalCase`, 파일명: `snake_case`
- API 응답 JSON 키는 `snake_case`로 통일
- API 경로: 복수형 리소스명, kebab-case (예: `/api/companies`, `/api/reports/{id}`)

## 실행 방법

```
docker compose up
cd backend && uvicorn app.main:app --reload
```

환경 변수는 `.env`를 사용하며 `.env.example`만 커밋합니다.

## 관련 저장소

- Frontend: [DajinNguyen/FE](https://github.com/DajinNguyen/FE)
