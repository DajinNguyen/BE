"""낱말퀴즈 서버 검증: 정답을 내려주지 않고, 서버가 채운 칸을 확인해서 보상을 센다."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.core.db import new_session
from app.services import credit_service, crossword_service, data_store
from tests.conftest import login_as

PUZZLES = ["basics-easy", "financials-normal", "market-normal"]


@pytest.fixture(autouse=True)
def hidden_answers(monkeypatch):
    """운영처럼 정답을 감춘다 (예전 프론트와의 호환은 별도 테스트에서 켠다)."""
    monkeypatch.setattr(get_settings(), "crossword_expose_answers", False)


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(credit_service, "MIN_SOLVE_SECONDS", 0)


def _answers(puzzle_id: str) -> dict[str, str]:
    return crossword_service.answer_grid(data_store.get_crossword(puzzle_id))


def _open(client, puzzle_id):
    return client.get(f"/api/learn/crosswords/{puzzle_id}")


def _complete(client, puzzle_id, entries=None, revealed=False):
    body = {"puzzle_id": puzzle_id, "used_reveal": revealed}
    if entries is not None:
        body["entries"] = entries
    return client.post("/api/me/puzzle-completions", json=body).json()


def _progress(client) -> int:
    return client.get("/api/me/wallet").json()["puzzle_progress"]["done"]


# ------------------------------------------------------------------ 정답을 내려주지 않는다


def test_answers_are_not_in_the_puzzle_response(client):
    body = _open(client, "basics-easy").json()
    assert all("answer" not in w for w in body["words"])
    assert [w["answer_length"] for w in body["words"]] == [
        len(w.answer) for w in data_store.get_crossword("basics-easy").words
    ]


def test_answers_stay_for_the_old_frontend_while_the_switch_is_on(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "crossword_expose_answers", True)
    word = _open(client, "basics-easy").json()["words"][0]
    assert word["answer"] == "주가" and word["answer_length"] == 2


# ------------------------------------------------------------------ 확인·힌트·정답 보기


def test_check_marks_wrong_cells_and_judges_full_words(client):
    puzzle = data_store.get_crossword("basics-easy")
    grid = _answers("basics-easy")
    first = puzzle.words[0]
    keys = crossword_service.cells(first)
    entries = {k: grid[k] for k in keys}
    entries[keys[0]] = "뭐" if grid[keys[0]] != "뭐" else "가"  # 첫 칸을 틀리게

    res = client.post("/api/learn/crosswords/basics-easy/check", json={"entries": entries}).json()
    assert res["wrong_cells"] == [keys[0]]
    assert 0 in res["wrong_words"] and 0 not in res["correct_words"]
    assert res["complete"] is False

    res = client.post("/api/learn/crosswords/basics-easy/check", json={"entries": grid}).json()
    assert res["wrong_cells"] == [] and res["complete"] is True
    assert sorted(res["correct_words"]) == list(range(len(puzzle.words)))


def test_check_works_without_login_and_validates_input(client):
    ok = client.post("/api/learn/crosswords/basics-easy/check", json={"entries": {"0,2": "주"}})
    assert ok.status_code == 200
    for bad in (
        {"entries": {"x": "주"}},
        {"entries": {"0,2": "주가"}},
        {"entries": {"0,2,3": "주"}},
    ):
        assert client.post("/api/learn/crosswords/basics-easy/check", json=bad).status_code == 422
    assert client.post("/api/learn/crosswords/nope/check", json={"entries": {}}).status_code == 404


def test_hint_gives_choseong_then_first_char(client):
    first = data_store.get_crossword("basics-easy").words[0]  # 주가
    h1 = client.post("/api/learn/crosswords/basics-easy/hint", json={"word_index": 0, "level": 1})
    assert h1.json() == {"choseong": "ㅈㄱ", "first_char": None}
    h2 = client.post("/api/learn/crosswords/basics-easy/hint", json={"word_index": 0, "level": 2})
    assert h2.json() == {"choseong": "ㅈㄱ", "first_char": first.answer[0]}
    missing = client.post(
        "/api/learn/crosswords/basics-easy/hint", json={"word_index": 99, "level": 1}
    )
    assert missing.status_code == 400


def test_reveal_returns_every_answer(client):
    res = client.post("/api/learn/crosswords/basics-easy/reveal")
    assert res.json() == {"answers": _answers("basics-easy")}


def test_choseong_matches_the_frontend_rule():
    assert crossword_service.choseong("영업이익") == "ㅇㅇㅇㅇ"
    assert crossword_service.choseong("PER배수") == "PERㅂㅅ"  # 한글이 아닌 글자는 그대로


# ------------------------------------------------------------------ 보상은 서버가 확인한 것만


def test_reward_counts_only_when_the_final_grid_matches_the_server_answers(client, no_wait):
    login_as(client, 1)
    _open(client, "basics-easy")
    wrong = {**_answers("basics-easy"), "0,2": "뭐"}
    assert _complete(client, "basics-easy", wrong)["puzzle_progress"]["done"] == 0
    assert _complete(client, "basics-easy", {})["puzzle_progress"]["done"] == 0
    extra = {**_answers("basics-easy"), "4,4": "가"}  # 정답에 없는 칸
    assert _complete(client, "basics-easy", extra)["puzzle_progress"]["done"] == 0
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 1


def test_three_verified_puzzles_give_one_reward(client, no_wait):
    login_as(client, 1)
    for puzzle_id in PUZZLES:
        _open(client, puzzle_id)
        body = _complete(client, puzzle_id, _answers(puzzle_id))
    assert (body["rewarded"], body["balance"]) == (True, 70)


def test_claiming_without_entries_is_not_counted_once_answers_are_hidden(client, no_wait):
    """예전 요청 모양(`entries` 없음)으로는 서버가 확인할 수 없어서 보상으로 세지 않는다."""
    login_as(client, 1)
    _open(client, "basics-easy")
    assert _complete(client, "basics-easy")["puzzle_progress"]["done"] == 0


def test_old_frontend_still_counts_while_answers_are_exposed(client, no_wait, monkeypatch):
    monkeypatch.setattr(get_settings(), "crossword_expose_answers", True)
    login_as(client, 1)
    _open(client, "basics-easy")
    assert _complete(client, "basics-easy")["puzzle_progress"]["done"] == 1


def test_revealing_removes_the_puzzle_from_todays_reward_even_if_the_client_lies(client, no_wait):
    login_as(client, 1)
    _open(client, "basics-easy")
    client.post("/api/learn/crosswords/basics-easy/reveal")
    # 클라이언트가 used_reveal=false 로 거짓말을 하고 정답을 그대로 보내도 서버가 기록한 정답 보기가 이긴다
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 0


def test_client_saying_it_used_reveal_is_not_counted(client, no_wait):
    login_as(client, 1)
    _open(client, "basics-easy")
    done = _complete(client, "basics-easy", _answers("basics-easy"), revealed=True)
    assert done["puzzle_progress"]["done"] == 0


def test_too_fast_completion_is_not_counted(client):
    """사람이 푸는 데 걸리는 최소 시간(단어당 몇 초)보다 빨리 온 완료는 세지 않는다."""
    login_as(client, 1)
    _open(client, "basics-easy")
    done = _complete(client, "basics-easy", _answers("basics-easy"))
    assert done["puzzle_progress"]["done"] == 0


def test_completion_counts_after_the_minimum_time(client):
    user_id = login_as(client, 1)
    started = datetime.now(UTC) - timedelta(
        seconds=crossword_service.min_solve_seconds(data_store.get_crossword("basics-easy")) + 5
    )
    with new_session() as session:
        credit_service.mark_opened(session, user_id, "basics-easy", now=started)
    # 어제 시각이 아니라 '오늘' 시작한 풀이여야 한다
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 1


def test_guessing_with_too_many_checks_loses_the_reward(client, no_wait):
    """확인을 수십 번 불러 정답을 알아낸 풀이는 보상을 세지 않는다 (확인은 정답을 조금씩 알려 주는 API)."""
    login_as(client, 1)
    puzzle = data_store.get_crossword("basics-easy")
    for _ in range(crossword_service.call_budget(puzzle) + 1):
        client.post("/api/learn/crosswords/basics-easy/check", json={"entries": {"0,2": "주"}})
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 0


def test_normal_play_stays_within_the_check_budget(client, no_wait):
    """단어를 채울 때마다 확인하고 [확인]·힌트를 몇 번 눌러도 보상을 받는다."""
    login_as(client, 1)
    puzzle = data_store.get_crossword("basics-easy")
    grid = _answers("basics-easy")
    entries: dict[str, str] = {}
    for word in puzzle.words:
        for key in crossword_service.cells(word):
            entries[key] = grid[key]
        client.post("/api/learn/crosswords/basics-easy/check", json={"entries": entries})
    for _ in range(3):
        client.post("/api/learn/crosswords/basics-easy/hint", json={"word_index": 0, "level": 1})
    assert _complete(client, "basics-easy", grid)["puzzle_progress"]["done"] == 1


def test_attempt_restarts_each_day(client, no_wait):
    """어제 열고 정답을 본 풀이가 오늘로 이어지지 않는다. 오늘 다시 열어야 오늘 풀이가 시작된다."""
    user_id = login_as(client, 1)
    yesterday = datetime.now(UTC) - timedelta(days=1)
    with new_session() as session:
        credit_service.mark_opened(session, user_id, "basics-easy", now=yesterday)
    # 오늘은 아직 열지 않았다 → 어제 열어 둔 것으로는 오늘 완료를 셀 수 없다
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 0
    _open(client, "basics-easy")  # 오늘 열기
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 1


def test_yesterdays_reveal_does_not_block_today(client, no_wait):
    from app.models.learning import PuzzleOpenRecord

    user_id = login_as(client, 1)
    _open(client, "basics-easy")
    client.post("/api/learn/crosswords/basics-easy/reveal")
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 0
    # 하루가 지난 것으로 만든 뒤 다시 열면, 정답 보기·호출 수가 새로 시작한다
    with new_session() as session:
        record = session.get(PuzzleOpenRecord, (user_id, "basics-easy"))
        record.attempt_day = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        session.add(record)
        session.commit()
    _open(client, "basics-easy")
    assert _complete(client, "basics-easy", _answers("basics-easy"))["puzzle_progress"]["done"] == 1


def test_replaying_the_same_completion_counts_once(client, no_wait):
    login_as(client, 1)
    _open(client, "basics-easy")
    for _ in range(5):
        body = _complete(client, "basics-easy", _answers("basics-easy"))
    assert body["puzzle_progress"]["done"] == 1


def test_completion_record_does_not_keep_the_filled_grid(client, no_wait):
    login_as(client, 1)
    _open(client, "basics-easy")
    _complete(client, "basics-easy", _answers("basics-easy"))
    completions = client.get("/api/me/usage-credits").json()["completions"]
    assert completions and all("entries" not in c for c in completions)


def test_guest_can_play_but_cannot_claim(client):
    assert (
        client.post("/api/learn/crosswords/basics-easy/check", json={"entries": {}}).status_code
        == 200
    )
    res = client.post(
        "/api/me/puzzle-completions", json={"puzzle_id": "basics-easy", "used_reveal": False}
    )
    assert res.status_code == 401
