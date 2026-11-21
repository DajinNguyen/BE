"""매일 새 낱말퀴즈를 만든다 (AI 없이, 단어 은행 `crossword_words.json` 에서).

- 하루에 3판: 기초(쉬움 5×5) · 재무(보통 7×7) · 시장(보통 7×7). id 는 `daily-YYYYMMDD-N`.
- 같은 id 는 언제 만들어도 똑같은 판이 나온다 (id 를 씨앗으로 쓰는 고정 난수).
  그래서 서버를 다시 켜도, 서버가 여러 대여도 같은 퀴즈·같은 정답이다.
- 같은 날 3판에는 같은 단어가 두 번 나오지 않고, 한 판에는 같은 용어(예: 매출·매출액)가 한 번만 나온다.
  열쇠에 같은 판의 다른 정답이 들어 있는 단어끼리는 같이 넣지 않는다.
- 지난 퀴즈는 DAYS_KEPT 일까지 열 수 있다 (자정 넘어 풀던 판을 마저 끝낼 수 있게). 미래 날짜는 열 수 없다.
"""

import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

from app.schemas.crossword import CrosswordPuzzle, CrosswordWord, PuzzleSize
from app.services.market_clock import KST

DAYS_KEPT = 7
ATTEMPTS = 80  # 한 판을 만들 때 배치를 몇 번 시도해 볼지 (가장 단어가 많은 배치를 쓴다)
_DAILY_ID = re.compile(r"^daily-(\d{8})-([1-9])$")


@dataclass(frozen=True)
class BankWord:
    answer: str
    term_id: str
    clue: str


@dataclass(frozen=True)
class Slot:
    title: str
    difficulty: str  # easy | normal
    size: int  # 정사각형 격자 한 변 (이 안에 들어가게 만든다)
    target: int  # 이만큼 놓으면 멈춘다
    minimum: int  # 이보다 적으면 단어 범위를 넓혀 다시 만든다
    max_len: int
    categories: tuple[str, ...]  # 먼저 쓰는 용어 분류 (모자라면 전체에서)
    beginner_only: bool


SLOTS = (
    Slot("오늘의 기초 퀴즈", "easy", 5, 8, 6, 4, ("basic", "market"), True),
    Slot("오늘의 재무 퀴즈", "normal", 7, 8, 6, 7, ("financial_statement", "indicator"), False),
    Slot("오늘의 시장 퀴즈", "normal", 7, 8, 6, 7, ("market", "industry"), False),
)


@dataclass
class _Placed:
    word: BankWord
    across: bool
    row: int
    col: int

    def cells(self) -> list[tuple[int, int]]:
        n = len(self.word.answer)
        if self.across:
            return [(self.row, self.col + i) for i in range(n)]
        return [(self.row + i, self.col) for i in range(n)]


class _Grid:
    def __init__(self, size: int) -> None:
        self.size = size
        self.letters: dict[tuple[int, int], str] = {}
        self.dirs: dict[tuple[int, int], set[bool]] = {}
        self.placed: list[_Placed] = []

    def _free(self, cell: tuple[int, int]) -> bool:
        return cell not in self.letters

    def crossings(self, word: str, across: bool, row: int, col: int) -> int | None:
        """놓을 수 있으면 교차 칸 수, 못 놓으면 None."""
        n = len(word)
        dr, dc = (0, 1) if across else (1, 0)
        end = (row + dr * (n - 1), col + dc * (n - 1))
        if row < 0 or col < 0 or end[0] >= self.size or end[1] >= self.size:
            return None
        # 단어 앞뒤 칸은 비어 있어야 한다 (붙어서 더 긴 줄이 생기지 않게)
        if not self._free((row - dr, col - dc)) or not self._free((end[0] + dr, end[1] + dc)):
            return None
        crossed = 0
        for i, ch in enumerate(word):
            cell = (row + dr * i, col + dc * i)
            have = self.letters.get(cell)
            if have is not None:
                if have != ch or across in self.dirs[cell]:
                    return None
                crossed += 1
                continue
            # 새로 채우는 칸의 양옆(가로 단어면 위아래)은 비어 있어야 한다
            side = ((cell[0] - 1, cell[1]), (cell[0] + 1, cell[1]))
            if not across:
                side = ((cell[0], cell[1] - 1), (cell[0], cell[1] + 1))
            if any(not self._free(s) for s in side):
                return None
        if crossed == n:
            return None
        return crossed

    def place(self, placed: _Placed) -> None:
        for cell, ch in zip(placed.cells(), placed.word.answer, strict=True):
            self.letters[cell] = ch
            self.dirs.setdefault(cell, set()).add(placed.across)
        self.placed.append(placed)

    def options(self, word: str) -> list[tuple[bool, int, int, int]]:
        """이미 놓인 글자와 교차하는 자리들 (가로?, 행, 열, 교차 수)."""
        found = set()
        for (r, c), ch in self.letters.items():
            for i, wc in enumerate(word):
                if wc != ch:
                    continue
                for across in (True, False):
                    row, col = (r, c - i) if across else (r - i, c)
                    found.add((across, row, col))
        result = []
        for across, row, col in sorted(found):
            crossed = self.crossings(word, across, row, col)
            if crossed:
                result.append((across, row, col, crossed))
        return result


def _attempt(rng: random.Random, pool: list[BankWord], slot: Slot) -> list[_Placed]:
    grid = _Grid(slot.size)
    order = pool[:]
    rng.shuffle(order)
    first = next((w for w in order if len(w.answer) >= 3), order[0])
    n = len(first.answer)
    grid.place(_Placed(first, True, slot.size // 2, rng.randint(0, slot.size - n)))
    terms = {first.term_id}
    progress = True

    def gives_away(word: BankWord) -> bool:
        """열쇠에 같은 판의 다른 정답이 들어 있으면 답을 알려 주는 셈이라 함께 쓰지 않는다."""
        return any(p.word.answer in word.clue or word.answer in p.word.clue for p in grid.placed)

    while progress and len(grid.placed) < slot.target:
        progress = False
        rng.shuffle(order)
        for word in order:
            if word.term_id in terms or gives_away(word):
                continue
            spots = grid.options(word.answer)
            if not spots:
                continue
            best = max(s[3] for s in spots)
            across, row, col, _ = rng.choice([s for s in spots if s[3] == best])
            grid.place(_Placed(word, across, row, col))
            terms.add(word.term_id)
            progress = True
            break
    return grid.placed


def _score(placed: list[_Placed]) -> tuple[int, int]:
    cells = {cell for p in placed for cell in p.cells()}
    return len(placed), sum(len(p.word.answer) for p in placed) - len(cells)


def _layout(seed: str, pool: list[BankWord], slot: Slot) -> list[_Placed]:
    rng = random.Random(seed)
    best: list[_Placed] = []
    for _ in range(ATTEMPTS):
        placed = _attempt(rng, pool, slot)
        if _score(placed) > _score(best):
            best = placed
        if len(best) >= slot.target:
            break
    return best


def _to_puzzle(puzzle_id: str, slot: Slot, placed: list[_Placed]) -> CrosswordPuzzle:
    cells = [cell for p in placed for cell in p.cells()]
    top = min(r for r, _ in cells)
    left = min(c for _, c in cells)
    rows = max(r for r, _ in cells) - top + 1
    cols = max(c for _, c in cells) - left + 1
    starts = sorted({(p.row - top, p.col - left) for p in placed})
    number = {cell: i for i, cell in enumerate(starts, start=1)}
    ordered = sorted(placed, key=lambda p: (p.row, p.col, not p.across))
    words = [
        CrosswordWord(
            number=number[(p.row - top, p.col - left)],
            direction="across" if p.across else "down",
            row=p.row - top,
            col=p.col - left,
            answer=p.word.answer,
            term_id=p.word.term_id,
            clue=p.word.clue,
        )
        for p in ordered
    ]
    return CrosswordPuzzle(
        id=puzzle_id,
        title=slot.title,
        difficulty=slot.difficulty,  # type: ignore[arg-type]
        size=PuzzleSize(rows=rows, cols=cols),
        words=words,
    )


def _pool(
    bank: list[BankWord], terms: dict[str, tuple[str, str]], slot: Slot, used: set[str], wide: bool
) -> list[BankWord]:
    def fits(w: BankWord) -> bool:
        category, level = terms[w.term_id]
        if w.answer in used or len(w.answer) > slot.max_len:
            return False
        if slot.beginner_only and level != "beginner":
            return False
        return wide or category in slot.categories

    return [w for w in bank if fits(w)]


def build_day(
    day: date, bank: list[BankWord], terms: dict[str, tuple[str, str]]
) -> list[CrosswordPuzzle]:
    """그날의 퀴즈 3판 (같은 날짜·같은 단어 은행이면 항상 같은 결과)."""
    used: set[str] = set()
    puzzles = []
    for n, slot in enumerate(SLOTS, start=1):
        puzzle_id = f"daily-{day:%Y%m%d}-{n}"
        placed = _layout(puzzle_id, _pool(bank, terms, slot, used, wide=False), slot)
        if len(placed) < slot.minimum:
            placed = _layout(puzzle_id + "-wide", _pool(bank, terms, slot, used, wide=True), slot)
        used.update(p.word.answer for p in placed)
        puzzles.append(_to_puzzle(puzzle_id, slot, placed))
    return puzzles


def today(now: datetime | None = None) -> date:
    return (now or datetime.now(KST)).astimezone(KST).date()


def parse_id(puzzle_id: str) -> tuple[date, int] | None:
    """`daily-YYYYMMDD-N` → (날짜, N). 형식이 아니면 None."""
    match = _DAILY_ID.match(puzzle_id)
    if not match:
        return None
    try:
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None
    return day, int(match.group(2))


def is_open(day: date, now: datetime | None = None) -> bool:
    """열 수 있는 날짜인지: 오늘부터 DAYS_KEPT 일 전까지."""
    current = today(now)
    return current - timedelta(days=DAYS_KEPT) <= day <= current


@lru_cache(maxsize=DAYS_KEPT + 2)
def cached_day(day: date, bank: tuple[BankWord, ...], terms: tuple[tuple[str, str, str], ...]):
    return build_day(day, list(bank), {t[0]: (t[1], t[2]) for t in terms})
