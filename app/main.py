import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import (
    auth,
    chat,
    community,
    companies,
    home,
    industries,
    learn,
    market,
    me,
    mock_invest,
    reports,
    terms,
    uploads,
    watchlist,
)
from app.core.config import check_production, get_settings
from app.core.db import engine, init_db
from app.core.errors import register_error_handlers
from app.services import dart, upload_service
from app.services.auth_service import SESSION_COOKIE

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.environment == "production":
        errors, warnings = check_production(settings)
        for w in warnings:
            logger.warning("운영 설정 점검: %s", w)
        if errors:
            raise RuntimeError(
                "운영 설정이 부족해서 서버를 켤 수 없어요:\n- " + "\n- ".join(errors)
            )
    init_db()
    upload_service.upload_dir()  # 사진 폴더가 없으면 만든다
    if dart.get_dart() is not None:
        threading.Thread(target=dart.warm_up, daemon=True).start()
    yield


app = FastAPI(title="다진(dajin) API", version="0.1.0", lifespan=lifespan)
register_error_handlers(app)

# 사용자마다 다른 응답은 브라우저·중간 서버가 저장해 두지 못하게 한다.
# (로그아웃하고 다른 계정으로 로그인했을 때 앞사람의 학습 기록·잔액이 보이면 안 된다)
PERSONAL_PATHS = ("/api/me", "/api/auth")


@app.middleware("http")
async def no_store_for_personal_responses(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    personal = path.startswith(PERSONAL_PATHS)
    if personal or (path.startswith("/api/") and SESSION_COOKIE in request.cookies):
        response.headers["Cache-Control"] = "no-store"
        response.headers.add_vary_header("Cookie")
    return response


# 로컬 개발: 프론트(Vite) 개발 서버에서 쿠키 포함 요청을 보낼 수 있게 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health():
    """배포 후 상태 확인용 (로드밸런서·모니터링이 부른다). DB에 연결되지 않으면 503."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("헬스 체크: DB 연결 실패")
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}


app.include_router(home.router)
app.include_router(companies.router)
app.include_router(reports.router)
app.include_router(terms.router)
app.include_router(learn.router)
app.include_router(auth.router)
app.include_router(community.router)
app.include_router(market.router)
app.include_router(industries.router)
app.include_router(uploads.router)
app.include_router(watchlist.router)
app.include_router(mock_invest.router)
app.include_router(me.router)
app.include_router(chat.router)
# 커뮤니티 사진 (POST /api/uploads 로 올린 파일)
app.mount(
    upload_service.URL_PATH.rstrip("/"),
    StaticFiles(directory=get_settings().upload_dir, check_dir=False),
    name="uploads",
)
