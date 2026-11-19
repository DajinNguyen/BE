# 다진 서버 띄우기 (처음 해보는 사람용)

내 컴퓨터를 서버로 써서 다진을 인터넷에 공개하는 방법이에요.
**Docker**로 프로그램들(DB·백엔드·웹)을 한 번에 켜고, **Cloudflare 터널**로 인터넷에 연결해요.
(공유기 설정이 필요 없고, 집 주소(IP)가 드러나지 않고, HTTPS가 자동으로 붙어요.)

```
사용자 ─ https://내도메인 ─▶ Cloudflare ─▶ (터널) ─▶ [서버 컴퓨터]
                                                    web(Nginx) ─┬─ 화면 파일
                                                                └─ /api ─▶ backend ─▶ db
```

> ⚠️ **먼저 읽어 주세요**
> - 서버 컴퓨터가 꺼지거나 인터넷이 끊기면 서비스도 멈춰요. **베타(지인·학교 테스트)용**으로 적당해요.
> - 이 구성이 `ENVIRONMENT=production` 으로 켜지려면 백엔드의 운영 설정 점검 PR(#56)이 머지돼 있어야 해요.
> - 네이버 시세는 비공식 API라 **공개 출시 전에는 정식 데이터로 바꿔야 해요** (docs/DEPLOY.md 참고).

---

## 0. 준비물
- 서버로 쓸 컴퓨터 (항상 켜 둘 수 있는 것. 메모리 4GB 이상 권장)
- 도메인 (연 1~2만 원)
- Cloudflare 계정 (무료)
- Google 계정 (로그인 설정용)

## 1. 도메인 사기
1. https://dash.cloudflare.com 에 가입해요.
2. 왼쪽 메뉴 **Domain Registration → Register Domains** 에서 원하는 이름을 검색해 구매해요.
   (`.com`, `.net`, `.app` 등. 원가로 팔아서 가장 싸고, 연결이 자동이라 제일 쉬워요.)
3. 다른 곳(가비아 등)에서 샀다면 Cloudflare에 사이트를 추가하고 안내하는 **네임서버**로 바꿔야 해요.

## 2. 서버 컴퓨터 준비
1. **Docker 설치**: https://www.docker.com/products/docker-desktop 에서 내 운영체제용을 받아 설치하고 실행해요.
2. **잠자기 끄기** (꺼지면 서비스가 멈춰요)
   - Mac: 시스템 설정 → 잠금 화면/에너지 → "디스플레이가 꺼져도 자동으로 잠자기 방지" 켜기
   - Windows: 설정 → 시스템 → 전원 → 화면 및 절전을 "안 함"
3. Docker Desktop 설정에서 **"로그인할 때 Docker Desktop 시작"** 을 켜요 (재부팅해도 자동으로 올라와요).
4. 코드를 같은 폴더 안에 나란히 받아요.

```bash
mkdir dajin && cd dajin
git clone https://github.com/DajinNguyen/BE.git backend
git clone https://github.com/DajinNguyen/FE.git frontend
```

> 폴더 이름이 꼭 `backend`, `frontend` 여야 해요 (구성 파일이 그 이름으로 서로를 찾아요).

## 3. 비밀값 파일 만들기
```bash
cd backend/deploy
cp .env.example .env
```
`.env` 를 열어서 채워요. (이 파일은 git에 올라가지 않아요. **남에게 보여주지 마세요.**)

| 항목 | 어떻게 구하나요 |
|---|---|
| `DOMAIN` | 1번에서 산 도메인 (`https://` 없이). 예: `dajin.example.com` |
| `POSTGRES_PASSWORD` | 아무 긴 문자열 (한 번 정하면 바꾸지 마세요) |
| `JWT_SECRET` | 터미널에서 `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` 결과 |
| `GOOGLE_CLIENT_ID/SECRET` | 4번에서 만들어요 |
| `CLOUDFLARE_TUNNEL_TOKEN` | 5번에서 만들어요 |
| `ANTHROPIC_API_KEY` 등 | 있는 것만. 없으면 AI 리포트가 데모용 가짜 데이터로 나와요 |

## 4. Google 로그인 설정
1. https://console.cloud.google.com → 프로젝트 선택 → **API 및 서비스 → 사용자 인증 정보**
2. **사용자 인증 정보 만들기 → OAuth 클라이언트 ID → 웹 애플리케이션**
3. **승인된 리디렉션 URI** 에 아래를 **정확히** 넣어요 (내 도메인으로):
   `https://내도메인/api/auth/google/callback`
4. 만들어진 **클라이언트 ID / 보안 비밀번호**를 `.env` 에 넣어요.
5. OAuth 동의 화면이 "테스트" 상태면 **테스트 사용자**로 등록한 계정만 로그인돼요. 일반 공개하려면 "프로덕션으로 게시"를 해야 해요.

## 5. Cloudflare 터널 만들기
1. Cloudflare 대시보드 → **Zero Trust** (처음이면 무료 플랜으로 시작) → **Networks → Tunnels → Create a tunnel**
2. 종류 **Cloudflared** 선택, 이름은 아무거나(예: `dajin`) → 저장
3. 나오는 설치 명령 중 **토큰(`eyJ...` 로 시작하는 긴 문자열)** 만 복사해서 `.env` 의 `CLOUDFLARE_TUNNEL_TOKEN` 에 붙여넣어요. (화면의 설치 명령은 실행하지 않아도 돼요. Docker가 대신 해요.)
4. **Public Hostname** 추가:
   - Domain: 내 도메인 (서브도메인은 비워도 돼요)
   - Service → Type: **HTTP**, URL: **`web:80`**
5. 저장해요.

## 6. 실행
```bash
cd backend/deploy
docker compose --profile tunnel up -d --build
```
처음엔 이미지를 만드느라 5~10분 걸려요. 끝나면 상태를 확인해요.
```bash
docker compose ps          # db, backend, web, tunnel 이 모두 running/healthy 이면 성공
docker compose logs -f backend   # 로그 보기 (Ctrl+C 로 나가기)
```

## 7. 확인
- 이 컴퓨터에서: http://localhost:8080 (터널 없이도 열려요)
- 인터넷에서: https://내도메인 → 화면이 열리고 "Google로 로그인"이 되면 성공이에요 🎉
- 헬스 체크: https://내도메인/api/health → `{"status":"ok"}`

## 8. 코드가 바뀌었을 때 (업데이트)
```bash
cd dajin/backend && git pull
cd ../frontend && git pull
cd ../backend/deploy
docker compose --profile tunnel up -d --build
```

## 9. 백업 (꼭 하세요)
DB와 업로드 사진은 이 컴퓨터에만 있어요. 디스크가 고장나면 사라져요.
```bash
cd backend/deploy && ./backup.sh        # backups/ 폴더에 저장 (14일 보관)
```
매일 자동으로 하려면 `crontab -e` 에 한 줄 추가해요 (경로는 내 것으로):
```
0 4 * * * cd /Users/내이름/dajin/backend/deploy && ./backup.sh >> backups/backup.log 2>&1
```
**`backups/` 폴더는 다른 디스크/클라우드에도 복사해 두세요.** 같은 컴퓨터 안의 백업은 컴퓨터가 고장나면 같이 사라져요.

복구(필요할 때):
```bash
gunzip -c backups/db-날짜.sql.gz | docker compose exec -T db psql -U dajin dajin
```

## 10. 껐다 켜기
```bash
docker compose --profile tunnel down      # 끄기 (데이터는 그대로 남아요)
docker compose --profile tunnel up -d     # 켜기
```
> ⚠️ `docker compose down -v` 는 **데이터(DB·사진)까지 지워요.** 쓰지 마세요.

## 문제가 생겼을 때
| 증상 | 확인 |
|---|---|
| backend 가 계속 재시작해요 | `docker compose logs backend` — "운영 설정이 부족해서…" 메시지에 빠진 항목이 나와요 (`.env` 수정 후 다시 `up -d`) |
| 사이트가 안 열려요 (Cloudflare 오류 1033 등) | `docker compose logs tunnel` — 토큰이 맞는지, 5번의 Public Hostname 이 `web:80` 인지 확인 |
| 구글 로그인이 `redirect_uri_mismatch` | 4번의 리디렉션 URI 가 `https://내도메인/api/auth/google/callback` 과 한 글자도 다르지 않은지 확인 |
| 로그인했는데 풀려요 | `JWT_SECRET` 이 비어 있거나 바뀌었는지 확인 |
| 로그인 후 "테스트 사용자만" 오류 | 4번 5번 항목 (OAuth 동의 화면 게시 또는 테스트 사용자 추가) |
| 디스크가 꽉 차요 | `docker system prune -f` (사용하지 않는 이미지 정리. 볼륨은 지워지지 않아요) |
