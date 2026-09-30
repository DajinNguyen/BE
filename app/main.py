from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import companies, reports, terms
from app.core.db import init_db
from app.core.errors import register_error_handlers


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="다진(dajin) API", version="0.1.0", lifespan=lifespan)
register_error_handlers(app)
app.include_router(companies.router)
app.include_router(reports.router)
app.include_router(terms.router)
