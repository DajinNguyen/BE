from fastapi import APIRouter

from app.schemas.term import Term
from app.services import data_store

router = APIRouter(prefix="/api/terms", tags=["terms"])


@router.get("", response_model=list[Term])
def list_terms():
    return data_store.get_terms()
