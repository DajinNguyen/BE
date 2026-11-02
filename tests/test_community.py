from datetime import timedelta

import pytest

from app.core.db import new_session
from app.services import auth_service, community_service
from app.services.auth_service import GoogleProfile

STOCK = "005930"
POST = {"category": "question", "title": "HBM이 뭐예요?", "body": "쉽게 알려주세요."}


def _make_user(n: int):
    with new_session() as session:
        return auth_service.upsert_user(
            session, GoogleProfile(sub=f"sub-{n}", email=f"u{n}@example.com", name=f"실명{n}")
        )


@pytest.fixture
def login(client):
    """login(n): n번 사용자로 로그인한 상태로 바꾼다. login(None)은 로그아웃."""
    users = {}

    def _login(n: int | None):
        client.cookies.clear()
        if n is None:
            return None
        if n not in users:
            users[n] = _make_user(n)
        client.cookies.set(auth_service.SESSION_COOKIE, auth_service.issue_session(users[n]))
        return users[n]

    return _login


@pytest.fixture(autouse=True)
def no_spam_wait(monkeypatch):
    """도배 방지 간격은 해당 테스트에서만 켠다."""
    monkeypatch.setattr(community_service, "POST_INTERVAL", timedelta(0))


def _write(client, **overrides):
    return client.post(f"/api/companies/{STOCK}/posts", json={**POST, **overrides})


# --------------------------------------------------------------------------
# 로그인해야 쓸 수 있다
# --------------------------------------------------------------------------


def test_guest_can_read_but_not_write(client, login):
    login(1)
    post_id = _write(client).json()["id"]
    login(None)

    assert client.get(f"/api/companies/{STOCK}/posts").status_code == 200
    assert client.get(f"/api/posts/{post_id}").status_code == 200
    assert client.get(f"/api/posts/{post_id}/comments").status_code == 200

    for method, path, body in [
        ("POST", f"/api/companies/{STOCK}/posts", POST),
        ("PUT", f"/api/posts/{post_id}", POST),
        ("DELETE", f"/api/posts/{post_id}", None),
        ("POST", f"/api/posts/{post_id}/comments", {"body": "댓글"}),
        ("POST", f"/api/posts/{post_id}/like", None),
        ("DELETE", f"/api/posts/{post_id}/like", None),
        ("POST", "/api/reports", {"target_type": "post", "target_id": post_id, "reason": "ad"}),
    ]:
        res = client.request(method, path, json=body)
        assert res.status_code == 401, (method, path)
        assert res.json()["code"] == "LOGIN_REQUIRED"


def test_write_post(client, login):
    user = login(1)
    res = _write(client, title="  앞뒤 공백  ")
    assert res.status_code == 201
    post = res.json()
    assert post["id"].isdigit()
    assert post["stock_code"] == STOCK
    assert post["title"] == "앞뒤 공백"
    assert post["author"] == {"id": str(user.id), "nickname": auth_service.nickname_for(user)}
    assert "실명" not in post["author"]["nickname"]  # 실명 대신 닉네임
    assert post["created_at"].endswith("+09:00")
    assert post["updated_at"] is None
    assert post["is_mine"] is True
    assert (post["like_count"], post["comment_count"]) == (0, 0)


def test_write_post_unknown_company(client, login):
    login(1)
    res = client.post("/api/companies/999999/posts", json=POST)
    assert res.status_code == 404
    assert res.json()["code"] == "COMPANY_NOT_FOUND"


# --------------------------------------------------------------------------
# 목록 · 글 보기
# --------------------------------------------------------------------------


def test_list_latest_popular_and_category(client, login):
    login(1)
    first = _write(client, title="첫 글", category="free").json()["id"]
    second = _write(client, title="두 번째 글").json()["id"]
    login(2)
    client.post(f"/api/posts/{first}/like")

    latest = client.get(f"/api/companies/{STOCK}/posts", params={"sort": "latest"}).json()
    assert [p["id"] for p in latest["items"]] == [second, first]
    assert latest["page"] == 1 and latest["has_next"] is False

    popular = client.get(f"/api/companies/{STOCK}/posts", params={"sort": "popular"}).json()
    assert [p["id"] for p in popular["items"]] == [first, second]
    assert popular["items"][0]["liked_by_me"] is True
    assert popular["items"][0]["is_mine"] is False

    free = client.get(f"/api/companies/{STOCK}/posts", params={"category": "free"}).json()
    assert [p["id"] for p in free["items"]] == [first]

    other = client.get("/api/companies/000660/posts").json()
    assert other["items"] == []


def test_list_paging(client, login):
    login(1)
    for i in range(3):
        _write(client, title=f"글 {i}")
    page1 = client.get(f"/api/companies/{STOCK}/posts", params={"size": 2}).json()
    page2 = client.get(f"/api/companies/{STOCK}/posts", params={"size": 2, "page": 2}).json()
    assert (len(page1["items"]), page1["has_next"]) == (2, True)
    assert (len(page2["items"]), page2["has_next"], page2["page"]) == (1, False, 2)


def test_post_not_found(client):
    for post_id in ["999", "p1"]:
        res = client.get(f"/api/posts/{post_id}")
        assert res.status_code == 404
        assert res.json()["code"] == "POST_NOT_FOUND"


# --------------------------------------------------------------------------
# 규칙: 글자 수 · 금지어 · 도배 방지
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [{"title": "   "}, {"title": "가" * 51}, {"body": ""}, {"body": "가" * 2001}],
)
def test_post_length_rules(client, login, overrides):
    login(1)
    res = _write(client, **overrides)
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_LENGTH"


def test_post_length_limits_are_inclusive(client, login):
    login(1)
    assert _write(client, title="가" * 50, body="가" * 2000).status_code == 201


@pytest.mark.parametrize(
    "text",
    ["리 딩 방 오세요", "open.kakao.com/o/abc", "수익 보장 해드려요", "T.ME/abc", "ㅅㅂ"],
)
def test_banned_words(client, login, text):
    login(1)
    res = _write(client, body=text)
    assert res.status_code == 400
    assert res.json()["code"] == "BANNED_WORD"
    assert "쓸 수 없어요" in res.json()["detail"]


def test_post_spam_interval(client, login, monkeypatch):
    monkeypatch.setattr(community_service, "POST_INTERVAL", timedelta(seconds=30))
    login(1)
    assert _write(client).status_code == 201
    res = _write(client)
    assert res.status_code == 429
    assert res.json()["code"] == "TOO_FREQUENT"

    login(2)  # 다른 사람은 바로 쓸 수 있다
    assert _write(client).status_code == 201


# --------------------------------------------------------------------------
# 수정 · 삭제
# --------------------------------------------------------------------------


def test_only_author_can_edit_and_delete(client, login):
    login(1)
    post_id = _write(client).json()["id"]

    login(2)
    edit = {**POST, "title": "남의 글"}
    assert client.put(f"/api/posts/{post_id}", json=edit).json()["code"] == "NOT_OWNER"
    res = client.delete(f"/api/posts/{post_id}")
    assert (res.status_code, res.json()["code"]) == (403, "NOT_OWNER")

    login(1)
    res = client.put(f"/api/posts/{post_id}", json={**edit, "category": "free"})
    assert res.status_code == 200
    assert (res.json()["title"], res.json()["category"]) == ("남의 글", "free")
    assert res.json()["updated_at"] is not None


def test_delete_post_removes_comments(client, login):
    login(1)
    post_id = _write(client).json()["id"]
    comment_id = client.post(f"/api/posts/{post_id}/comments", json={"body": "댓글"}).json()["id"]

    assert client.delete(f"/api/posts/{post_id}").status_code == 204
    assert client.get(f"/api/posts/{post_id}").status_code == 404
    assert (
        client.delete(f"/api/comments/{comment_id}").json()["code"] == "COMMENT_NOT_FOUND"
    )


# --------------------------------------------------------------------------
# 댓글
# --------------------------------------------------------------------------


def test_comments(client, login):
    login(1)
    post_id = _write(client).json()["id"]
    login(2)
    first = client.post(f"/api/posts/{post_id}/comments", json={"body": " 첫 댓글 "})
    assert first.status_code == 201
    assert first.json()["body"] == "첫 댓글"
    assert first.json()["is_mine"] is True
    client.post(f"/api/posts/{post_id}/comments", json={"body": "두 번째"})

    login(None)
    comments = client.get(f"/api/posts/{post_id}/comments").json()
    assert [c["body"] for c in comments] == ["첫 댓글", "두 번째"]  # 오래된 순
    assert comments[0]["is_mine"] is False
    assert client.get(f"/api/posts/{post_id}").json()["comment_count"] == 2

    login(1)  # 남의 댓글은 못 지운다
    res = client.delete(f"/api/comments/{first.json()['id']}")
    assert (res.status_code, res.json()["code"]) == (403, "NOT_OWNER")

    login(2)
    assert client.delete(f"/api/comments/{first.json()['id']}").status_code == 204
    assert client.get(f"/api/posts/{post_id}").json()["comment_count"] == 1


@pytest.mark.parametrize("body", ["  ", "가" * 501, "오픈채팅으로 오세요"])
def test_comment_rules(client, login, body):
    login(1)
    post_id = _write(client).json()["id"]
    res = client.post(f"/api/posts/{post_id}/comments", json={"body": body})
    assert res.status_code == 400
    assert res.json()["code"] in ("INVALID_LENGTH", "BANNED_WORD")


def test_comment_on_missing_post(client, login):
    login(1)
    res = client.post("/api/posts/999/comments", json={"body": "댓글"})
    assert (res.status_code, res.json()["code"]) == (404, "POST_NOT_FOUND")


# --------------------------------------------------------------------------
# 좋아요
# --------------------------------------------------------------------------


def test_like_is_idempotent(client, login):
    login(1)
    post_id = _write(client).json()["id"]
    login(2)
    for _ in range(2):
        res = client.post(f"/api/posts/{post_id}/like")
        assert res.json() == {"like_count": 1, "liked_by_me": True}
    for _ in range(2):
        res = client.delete(f"/api/posts/{post_id}/like")
        assert res.json() == {"like_count": 0, "liked_by_me": False}


# --------------------------------------------------------------------------
# 신고
# --------------------------------------------------------------------------


def _report(client, target_type, target_id, reason="solicitation"):
    return client.post(
        "/api/reports", json={"target_type": target_type, "target_id": target_id, "reason": reason}
    )


def test_report_once_and_hide_after_three(client, login):
    login(1)
    post_id = _write(client).json()["id"]

    login(2)
    assert _report(client, "post", post_id).status_code == 204
    res = _report(client, "post", post_id)
    assert (res.status_code, res.json()["code"]) == (409, "ALREADY_REPORTED")
    assert client.get(f"/api/posts/{post_id}").json()["reported_by_me"] is True

    login(3)
    _report(client, "post", post_id)
    assert client.get(f"/api/posts/{post_id}").json()["is_hidden"] is False
    login(4)
    _report(client, "post", post_id)

    post = client.get(f"/api/posts/{post_id}").json()
    assert post["is_hidden"] is True
    assert (post["title"], post["body"]) == ("", "")


def test_report_comment_hides_it(client, login):
    login(1)
    post_id = _write(client).json()["id"]
    comment_id = client.post(f"/api/posts/{post_id}/comments", json={"body": "댓글"}).json()["id"]
    for n in (2, 3, 4):
        login(n)
        assert _report(client, "comment", comment_id, "abuse").status_code == 204

    comment = client.get(f"/api/posts/{post_id}/comments").json()[0]
    assert (comment["is_hidden"], comment["body"]) == (True, "")


def test_report_missing_target(client, login):
    login(1)
    res = _report(client, "comment", "999")
    assert (res.status_code, res.json()["code"]) == (404, "TARGET_NOT_FOUND")


def test_report_invalid_reason(client, login):
    login(1)
    post_id = _write(client).json()["id"]
    assert _report(client, "post", post_id, "spam").status_code == 422
