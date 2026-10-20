from typing import Literal

from pydantic import BaseModel

CrosswordDirection = Literal["across", "down"]
CrosswordDifficulty = Literal["easy", "normal"]


class CrosswordWord(BaseModel):
    number: int  # 단어가 시작하는 칸의 번호
    direction: CrosswordDirection
    row: int  # 시작 칸 (0부터)
    col: int
    answer: str  # 한 칸에 한글 한 음절
    term_id: str
    clue: str


class PuzzleSize(BaseModel):
    rows: int
    cols: int


class CrosswordPuzzle(BaseModel):
    id: str
    title: str
    difficulty: CrosswordDifficulty
    size: PuzzleSize
    words: list[CrosswordWord]


class CrosswordSummary(BaseModel):
    """목록에는 칸 정보 없이 단어 수만."""

    id: str
    title: str
    difficulty: CrosswordDifficulty
    size: PuzzleSize
    word_count: int
