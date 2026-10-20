from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.db import get_session
from app.schemas.home import HomeDashboard
from app.services import home_service

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("", response_model=HomeDashboard)
def get_home(session: Session = Depends(get_session)):
    return home_service.get_home(session)
