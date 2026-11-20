"""낱말퀴즈 서버 검증: 정답은 서버만 알고, 프론트는 칸 수와 열쇠만 받는다.

예전에는 퀴즈 응답에 정답이 그대로 들어 있어서 누구나 정답을 읽을 수 있었고, 서버는 "풀었는지"를
알 수 없어서 클라이언트가 보낸 `used_reveal=false` 와 대기 시간만 보고 보상을 줬다.
이제 프론트는 채운 칸을 서버에 보내 맞고 틀림을 받고(`check`), 힌트·정답 보기도 서버가 준다.
보상은 마지막에 채운 칸이 서버의 정답과 모두 같고, 정답 보기를 쓰지 않았고, 풀 만한 시간이 지났고,
답을 추측하느라 확인을 너무 많이 부르지 않았을 때만 센다 (`credit_service`).
"""

from app.core.config import get_settings
from app.schemas.crossword import (
    CrosswordCheckResult,
    CrosswordHintResult,
    CrosswordPuzzle,
    CrosswordPuzzleOut,
    CrosswordRevealResult,
    CrosswordWord,
    CrosswordWordOut,
)

MIN_SECONDS_PER_WORD = (
    4  # 단어 하나를 풀고 입력하는 데 걸리는 최소 시간 (이보다 빠른 완료는 사람이 푼 게 아니다)
)
CALLS_PER_WORD = (
    3  # 단어 하나당 허용하는 확인·힌트 호출 수 (한 단어를 칸마다 채우고 확인해도 넉넉하다)
)
CALLS_BASE = 10  # 단어 수와 상관없이 더해 주는 호출 수 ([확인] 버튼을 몇 번 눌러도 되게)

_CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_HANGUL_START, _HANGUL_END = 0xAC00, 0xD7A3


def cells(word: CrosswordWord) -> list[str]:
    """단어가 지나는 칸 ("행,열") 순서대로."""
    return [
        f"{word.row},{word.col + i}" if word.direction == "across" else f"{word.row + i},{word.col}"
        for i in range(len(word.answer))
    ]


def answer_grid(puzzle: CrosswordPuzzle) -> dict[str, str]:
    """칸 → 정답 글자 (교차하는 칸은 같은 글자)."""
    grid: dict[str, str] = {}
    for word in puzzle.words:
        for key, char in zip(cells(word), word.answer, strict=True):
            grid[key] = char
    return grid


def public_puzzle(puzzle: CrosswordPuzzle) -> CrosswordPuzzleOut:
    """퀴즈 응답. 정답은 설정이 켜져 있을 때만 (예전 프론트와의 호환 기간)."""
    expose = get_settings().crossword_expose_answers
    return CrosswordPuzzleOut(
        id=puzzle.id,
        title=puzzle.title,
        difficulty=puzzle.difficulty,
        size=puzzle.size,
        words=[
            CrosswordWordOut(
                number=w.number,
                direction=w.direction,
                row=w.row,
                col=w.col,
                answer_length=len(w.answer),
                term_id=w.term_id,
                clue=w.clue,
                answer=w.answer if expose else None,
            )
            for w in puzzle.words
        ],
    )


def check(puzzle: CrosswordPuzzle, entries: dict[str, str]) -> CrosswordCheckResult:
    """채운 칸이 정답과 같은지. 틀린 칸과, 다 채운 단어의 맞고 틀림을 돌려준다."""
    grid = answer_grid(puzzle)
    wrong_cells = sorted(k for k, v in entries.items() if v and grid.get(k) != v)
    correct_words: list[int] = []
    wrong_words: list[int] = []
    for index, word in enumerate(puzzle.words):
        keys = cells(word)
        if not all(entries.get(k) for k in keys):
            continue
        right = all(entries[k] == c for k, c in zip(keys, word.answer, strict=True))
        (correct_words if right else wrong_words).append(index)
    return CrosswordCheckResult(
        wrong_cells=wrong_cells,
        correct_words=correct_words,
        wrong_words=wrong_words,
        complete=is_solved(puzzle, entries),
    )


def is_solved(puzzle: CrosswordPuzzle, entries: dict[str, str]) -> bool:
    """모든 칸이 정답이고, 정답에 없는 칸은 없다."""
    grid = answer_grid(puzzle)
    filled = {k: v for k, v in entries.items() if v}
    return filled == grid


def choseong(text: str) -> str:
    """ "영업이익" → "ㅇㅇㅇㅇ" (한글 음절이 아닌 글자는 그대로)."""
    return "".join(
        _CHOSEONG[(ord(c) - _HANGUL_START) // 588] if _HANGUL_START <= ord(c) <= _HANGUL_END else c
        for c in text
    )


def hint(puzzle: CrosswordPuzzle, word_index: int, level: int) -> CrosswordHintResult | None:
    """1단계: 초성, 2단계: 첫 글자까지. 없는 단어면 None."""
    if word_index >= len(puzzle.words):
        return None
    answer = puzzle.words[word_index].answer
    return CrosswordHintResult(
        choseong=choseong(answer), first_char=answer[0] if level >= 2 else None
    )


def reveal(puzzle: CrosswordPuzzle) -> CrosswordRevealResult:
    return CrosswordRevealResult(answers=answer_grid(puzzle))


def min_solve_seconds(puzzle: CrosswordPuzzle) -> float:
    return MIN_SECONDS_PER_WORD * len(puzzle.words)


def call_budget(puzzle: CrosswordPuzzle) -> int:
    """보상을 받으려면 이 퀴즈에서 부른 확인·힌트가 이 수를 넘지 않아야 한다 (정답 추측을 막는다)."""
    return CALLS_PER_WORD * len(puzzle.words) + CALLS_BASE
