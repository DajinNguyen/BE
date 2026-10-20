def test_crossword_list(client):
    res = client.get("/api/learn/crosswords")
    assert res.status_code == 200
    puzzles = res.json()
    assert [p["id"] for p in puzzles] == ["basics-easy", "financials-normal", "market-normal"]
    assert puzzles[0] == {
        "id": "basics-easy",
        "title": "주식 기초",
        "difficulty": "easy",
        "size": {"rows": 5, "cols": 5},
        "word_count": 8,
    }


def test_crossword_detail(client):
    puzzle = client.get("/api/learn/crosswords/basics-easy").json()
    assert puzzle["size"] == {"rows": 5, "cols": 5}
    assert puzzle["words"][0] == {
        "number": 1,
        "direction": "down",
        "row": 0,
        "col": 2,
        "answer": "주가",
        "term_id": "stock_price",
        "clue": "주식 1주의 가격",
    }


def test_crossword_words_fit_grid_and_link_to_terms(client):
    term_ids = {t["id"] for t in client.get("/api/terms").json()}
    for summary in client.get("/api/learn/crosswords").json():
        puzzle = client.get(f"/api/learn/crosswords/{summary['id']}").json()
        rows, cols = puzzle["size"]["rows"], puzzle["size"]["cols"]
        cells: dict[tuple[int, int], str] = {}
        for word in puzzle["words"]:
            assert word["term_id"] in term_ids
            dr, dc = (0, 1) if word["direction"] == "across" else (1, 0)
            for i, ch in enumerate(word["answer"]):
                r, c = word["row"] + dr * i, word["col"] + dc * i
                assert 0 <= r < rows and 0 <= c < cols
                # 가로·세로가 겹치는 칸은 같은 글자여야 한다
                assert cells.setdefault((r, c), ch) == ch


def test_crossword_not_found(client):
    res = client.get("/api/learn/crosswords/nope")
    assert res.status_code == 404
    assert res.json()["code"] == "PUZZLE_NOT_FOUND"
