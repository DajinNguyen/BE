"""매일 새 낱말퀴즈: 날마다 다른 3판, 올바른 격자, 같은 id 는 항상 같은 판."""

import json
import re
from datetime import date, datetime, timedelta

import pytest

from app.services import crossword_generator, data_store
from app.services.data_store import DATA_DIR
from app.services.market_clock import KST

DAYS = [date(2026, 1, 1) + timedelta(days=i) for i in range(0, 365, 6)]


def _cells(word):
    n = len(word.answer)
    if word.direction == "across":
        return [(word.row, word.col + i) for i in range(n)]
    return [(word.row + i, word.col) for i in range(n)]


def _check(puzzle):
    """프론트 validatePuzzle 과 같은 규칙 + 따로 생긴 글자 줄이 없는지."""
    rows, cols = puzzle.size.rows, puzzle.size.cols
    letters: dict[tuple[int, int], str] = {}
    owners: dict[tuple[int, int], list[int]] = {}
    for index, word in enumerate(puzzle.words):
        assert re.fullmatch(r"[가-힣]+", word.answer)
        for cell, ch in zip(_cells(word), word.answer, strict=True):
            assert 0 <= cell[0] < rows and 0 <= cell[1] < cols
            assert letters.setdefault(cell, ch) == ch
            owners.setdefault(cell, []).append(index)
    for word in puzzle.words:
        assert any(len(owners[c]) > 1 for c in _cells(word)), f"{word.answer} 교차 없음"
    # 번호: 위→아래, 왼→오른쪽 순서, 같은 칸에서 시작하면 같은 번호
    starts = sorted({(w.row, w.col) for w in puzzle.words})
    for w in puzzle.words:
        assert w.number == starts.index((w.row, w.col)) + 1
    # 가로·세로로 이어진 글자 줄은 모두 단어여야 한다
    words = {(w.direction, w.row, w.col, len(w.answer)) for w in puzzle.words}
    for across in (True, False):
        for line in range(rows if across else cols):
            run: list[tuple[int, int]] = []
            for k in range((cols if across else rows) + 1):
                cell = (line, k) if across else (k, line)
                if cell in letters:
                    run.append(cell)
                    continue
                if len(run) >= 2:
                    key = ("across" if across else "down", run[0][0], run[0][1], len(run))
                    assert key in words, f"{puzzle.id}: 단어가 아닌 줄 {run}"
                run = []
    # 같은 판 열쇠에 다른 정답이 없다, 같은 용어는 한 번
    answers = [w.answer for w in puzzle.words]
    for w in puzzle.words:
        assert w.answer not in w.clue
        assert not any(a in w.clue for a in answers if a != w.answer), (w.answer, w.clue)
    assert len({w.term_id for w in puzzle.words}) == len(puzzle.words)


@pytest.mark.parametrize("day", DAYS)
def test_each_day_has_three_valid_puzzles(day):
    puzzles = data_store.daily_crosswords(day)
    assert [p.difficulty for p in puzzles] == ["easy", "normal", "normal"]
    for puzzle in puzzles:
        _check(puzzle)
        assert len(puzzle.words) >= 5
        limit = 5 if puzzle.difficulty == "easy" else 7
        assert puzzle.size.rows <= limit and puzzle.size.cols <= limit
    answers = [w.answer for p in puzzles for w in p.words]
    assert len(answers) == len(set(answers)), "같은 날 같은 단어가 두 번"


def test_same_id_gives_same_puzzle_and_days_differ():
    day = date(2026, 11, 20)
    first = crossword_generator.build_day(day, *_bank())
    again = crossword_generator.build_day(day, *_bank())
    assert first == again
    tomorrow = crossword_generator.build_day(day + timedelta(days=1), *_bank())
    assert [p.words for p in first] != [p.words for p in tomorrow]


def test_word_bank_is_valid():
    terms = {t.id for t in data_store.get_terms()}
    bank = json.loads((DATA_DIR / "crossword_words.json").read_text(encoding="utf-8"))
    assert len(bank) >= 120
    assert len({w["answer"] for w in bank}) == len(bank)
    for w in bank:
        assert w["term_id"] in terms
        assert re.fullmatch(r"[가-힣]{2,7}", w["answer"])
        assert w["answer"] not in w["clue"]


def test_list_shows_today_and_old_ids_still_open(client, monkeypatch):
    now = datetime(2026, 11, 20, 0, 30, tzinfo=KST)
    monkeypatch.setattr(crossword_generator, "today", lambda _=None: now.date())
    listed = client.get("/api/learn/crosswords").json()
    assert [p["id"] for p in listed] == [f"daily-20261120-{n}" for n in (1, 2, 3)]
    assert listed[0]["title"] == "오늘의 기초 퀴즈"

    assert client.get("/api/learn/crosswords/daily-20261119-2").status_code == 200  # 어제 판
    assert client.get("/api/learn/crosswords/basics-easy").status_code == 200  # 예전 판
    for gone in ("daily-20261121-1", "daily-20261101-1", "daily-20261120-4", "daily-20261340-1"):
        assert client.get(f"/api/learn/crosswords/{gone}").status_code == 404


def test_daily_puzzle_can_be_checked_and_hides_answers(client, monkeypatch):
    monkeypatch.setattr(crossword_generator, "today", lambda _=None: date(2026, 11, 20))
    puzzle = data_store.get_crossword("daily-20261120-1")
    grid = {
        f"{r},{c}": ch for w in puzzle.words for (r, c), ch in zip(_cells(w), w.answer, strict=True)
    }
    res = client.post("/api/learn/crosswords/daily-20261120-1/check", json={"entries": grid})
    assert res.status_code == 200
    assert res.json()["complete"] is True


def _bank():
    bank = tuple(
        crossword_generator.BankWord(**w)
        for w in json.loads((DATA_DIR / "crossword_words.json").read_text(encoding="utf-8"))
    )
    terms = {t.id: (t.category, t.level) for t in data_store.get_terms()}
    return list(bank), terms
