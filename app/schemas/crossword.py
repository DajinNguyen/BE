import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_CELL_KEY = re.compile(r"^\d{1,2},\d{1,2}$")

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


# ---------------------------------------------------------------- 서버 검증용 (정답을 내려주지 않는 구조)


class CrosswordWordOut(BaseModel):
    """퀴즈 응답의 단어. 정답(answer)은 설정(CROSSWORD_EXPOSE_ANSWERS)이 켜져 있을 때만 들어간다."""

    number: int
    direction: CrosswordDirection
    row: int
    col: int
    answer_length: int  # 칸 수 (정답 글자 수)
    term_id: str
    clue: str
    answer: str | None = None


class CrosswordPuzzleOut(BaseModel):
    id: str
    title: str
    difficulty: CrosswordDifficulty
    size: PuzzleSize
    words: list[CrosswordWordOut]


class CrosswordCheckRequest(BaseModel):
    """지금까지 채운 칸. 키는 "행,열", 값은 한 글자."""

    entries: dict[str, str] = Field(default_factory=dict, max_length=400)

    @field_validator("entries")
    @classmethod
    def _valid_cells(cls, entries: dict[str, str]) -> dict[str, str]:
        for key, value in entries.items():
            if not _CELL_KEY.match(key) or len(value) > 1:
                raise ValueError("entries 는 {'행,열': '한 글자'} 형식이어야 해요")
        return entries


class CrosswordCheckResult(BaseModel):
    wrong_cells: list[str]  # 글자를 넣었는데 틀린 칸 ("행,열")
    correct_words: list[int]  # 모든 칸이 채워졌고 맞은 단어 (words 의 위치)
    wrong_words: list[int]  # 모든 칸이 채워졌는데 틀린 단어
    complete: bool  # 모든 칸이 맞았는지


class CrosswordHintRequest(BaseModel):
    word_index: int = Field(ge=0, le=100)
    level: Literal[1, 2]  # 1: 초성, 2: 첫 글자


class CrosswordHintResult(BaseModel):
    choseong: str
    first_char: str | None = None  # level 2 일 때만


class CrosswordRevealResult(BaseModel):
    answers: dict[str, str]  # "행,열" → 정답 글자
