# 배포 체크리스트

## 서버를 켜기 전 (ENVIRONMENT=production)
`ENVIRONMENT=production` 이면 아래가 빠졌을 때 서버가 **켜지지 않아요** (`app/core/config.py` `check_production`).

| 설정 | 값 |
|---|---|
| `JWT_SECRET` | 32자 이상 (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| `COOKIE_SECURE` | `true` (https 필수) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console OAuth 클라이언트 |
| `GOOGLE_REDIRECT_URI` | `https://<API 도메인>/api/auth/google/callback` — Google 콘솔의 승인된 리디렉션 URI에도 똑같이 등록 |
| `DATABASE_URL` | PostgreSQL (`postgresql+psycopg://...`) |
| `FRONTEND_ORIGINS` | 운영 프론트 주소(https)만. 예: `["https://dajin.example"]` |

경고(켜지긴 하지만 로그에 남아요): AI 키 없음, 네이버 비공식 시세 사용, Google 뉴스 RSS 사용(비상업 용도만).

## 상태 확인
`GET /api/health` → `{"status":"ok"}` (DB 연결 실패 시 503). 배포 직후 헬스체크와 로드밸런서에 사용.

## 아직 없는 것 (출시 전 결정·작업 필요)
- 시세·뉴스 정식 데이터 소스 계약 (비공식 API는 상업 서비스에 쓸 수 없음)
- DB 마이그레이션 도구(Alembic) — 지금은 시작할 때 새 컬럼만 자동 추가
- 업로드 사진 저장소 — 지금은 서버 디스크(`UPLOAD_DIR`). 컨테이너 배포 시 볼륨 또는 S3 필요
- 요청 제한은 서버 1대 기준(메모리). 서버를 늘리면 Redis 필요
- DB 백업·복구 절차, 에러 모니터링(Sentry 등), 배포 자동화(CD)·롤백
