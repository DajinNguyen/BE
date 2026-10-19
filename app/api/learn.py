from fastapi import APIRouter

from app.schemas.crossword import CrosswordPuzzle, CrosswordSummary
from app.services import data_store

router = APIRouter(prefix="/api/learn", tags=["learn"])


@router.get("/crosswords", response_model=list[CrosswordSummary])
def list_crosswords():
    return data_store.list_crosswords()


@router.get("/crosswords/{puzzle_id}", response_model=CrosswordPuzzle)
def get_crossword(puzzle_id: str):
    return data_store.get_crossword(puzzle_id)
