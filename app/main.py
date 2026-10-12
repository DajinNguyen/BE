from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import companies, reports, terms
from app.core.db import init_db
from app.core.errors import register_error_handlers


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="다진(dajin) API", version="0.1.0", lifespan=lifespan)
register_error_handlers(app)
# 로컬 개발: 프론트(Vite) 개발 서버에서 쿠키 포함 요청을 보낼 수 있게 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(companies.router)
app.include_router(reports.router)
app.include_router(terms.router)
