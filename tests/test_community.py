from datetime import timedelta

import pytest

from app.core.db import new_session
from app.services import auth_service, community_service
from app.services.auth_service import GoogleProfile

STOCK = "005930"
COMMENTS = f"/api/companies/{STOCK}/comments"


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
    monkeypatch.setattr(community_service, "WRITE_INTERVAL", timedelta(0))


def _write(client, body="HBM이 뭐예요?", parent_id=None):
    return client.post(COMMENTS, json={"body": body, "parent_id": parent_id})


def _list(client, **params):
    return client.get(COMMENTS, params=params).json()


# --------------------------------------------------------------------------
# 로그인해야 쓸 수 있다
# --------------------------------------------------------------------------


def test_guest_can_read_but_not_write(client, login):
    login(1)
    comment_id = _write(client).json()["id"]
    login(None)

    assert client.get(COMMENTS).status_code == 200

    for method, path, body in [
        ("POST", COMMENTS, {"body": "댓글"}),
        ("POST", COMMENTS, {"body": "답글", "parent_id": comment_id}),
        ("DELETE", f"/api/comments/{comment_id}", None),
        ("POST", f"/api/comments/{comment_id}/like", None),
        ("DELETE", f"/api/comments/{comment_id}/like", None),
        (
            "POST",
            "/api/reports",
            {"target_type": "comment", "target_id": comment_id, "reason": "ad"},
        ),
    ]:
        res = client.request(method, path, json=body)
        assert res.status_code == 401, (method, path)
        assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_write_comment_needs_only_body(client, login):
    user = login(1)
    res = _write(client, body="  앞뒤 공백  ")
    assert res.status_code == 201
    comment = res.json()
    assert comment["id"].isdigit()
    assert comment["stock_code"] == STOCK
    assert comment["parent_id"] is None
    assert comment["body"] == "앞뒤 공백"
    assert comment["author"] == {"id": str(user.id), "nickname": auth_service.nickname_for(user)}
    assert "실명" not in comment["author"]["nickname"]  # 실명 대신 닉네임
    assert comment["created_at"].endswith("+09:00")
    assert comment["is_mine"] is True
    assert (comment["like_count"], comment["reply_count"], comment["replies"]) == (0, 0, [])


def test_write_comment_unknown_company(client, login):
    login(1)
    res = client.post("/api/companies/999999/comments", json={"body": "댓글"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "COMPANY_NOT_FOUND"


# --------------------------------------------------------------------------
# 답글 (대댓글)
# --------------------------------------------------------------------------


def test_replies_are_nested_under_their_comment(client, login):
    login(1)
    root = _write(client, body="원댓글").json()["id"]
    login(2)
    reply = _write(client, body="첫 답글", parent_id=root)
    assert reply.status_code == 201
    assert reply.json()["parent_id"] == root
    # 답글에 단 답글은 같은 원댓글 아래로 (한 단계만)
    nested = _write(client, body="두 번째 답글", parent_id=reply.json()["id"]).json()
    assert nested["parent_id"] == root

    page = _list(client)
    assert [c["body"] for c in page["items"]] == ["원댓글"]  # 목록에는 원댓글만
    item = page["items"][0]
    assert item["reply_count"] == 2
    assert [r["body"] for r in item["replies"]] == ["첫 답글", "두 번째 답글"]  # 오래된 순
    assert all(r["is_mine"] for r in item["replies"])
    assert item["is_mine"] is False
    assert page["total"] == 3


def test_reply_to_missing_or_other_company_comment(client, login):
    login(1)
    res = _write(client, parent_id="999")
    assert (res.status_code, res.json()["error"]["code"]) == (404, "COMMENT_NOT_FOUND")

    other = client.post("/api/companies/000660/comments", json={"body": "다른 기업"}).json()["id"]
    res = _write(client, parent_id=other)
    assert (res.status_code, res.json()["error"]["code"]) == (404, "COMMENT_NOT_FOUND")


# --------------------------------------------------------------------------
# 목록
# --------------------------------------------------------------------------


def test_list_latest_and_popular(client, login):
    login(1)
    first = _write(client, body="첫 댓글").json()["id"]
    second = _write(client, body="두 번째 댓글").json()["id"]
    login(2)
    client.post(f"/api/comments/{first}/like")

    latest = _list(client, sort="latest")
    assert [c["id"] for c in latest["items"]] == [second, first]
    assert latest["page"] == 1 and latest["has_next"] is False

    popular = _list(client, sort="popular")
    assert [c["id"] for c in popular["items"]] == [first, second]
    assert popular["items"][0]["liked_by_me"] is True
    assert popular["items"][0]["is_mine"] is False

    other = client.get("/api/companies/000660/comments").json()
    assert (other["items"], other["total"]) == ([], 0)


def test_list_paging(client, login):
    login(1)
    for i in range(3):
        _write(client, body=f"댓글 {i}")
    page1 = _list(client, size=2)
    page2 = _list(client, size=2, page=2)
    assert (len(page1["items"]), page1["has_next"]) == (2, True)
    assert (len(page2["items"]), page2["has_next"], page2["page"]) == (1, False, 2)


# --------------------------------------------------------------------------
# 규칙: 글자 수 · 금지어 · 도배 방지
# --------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["   ", "가" * 501])
def test_comment_length_rules(client, login, body):
    login(1)
    res = _write(client, body=body)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_LENGTH"


def test_comment_length_limit_is_inclusive(client, login):
    login(1)
    assert _write(client, body="가" * 500).status_code == 201


@pytest.mark.parametrize(
    "text",
    ["리 딩 방 오세요", "open.kakao.com/o/abc", "수익 보장 해드려요", "T.ME/abc", "ㅅㅂ"],
)
def test_banned_words(client, login, text):
    login(1)
    res = _write(client, body=text)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "BANNED_WORD"
    assert "쓸 수 없어요" in res.json()["error"]["message"]


def test_spam_interval(client, login, monkeypatch):
    monkeypatch.setattr(community_service, "WRITE_INTERVAL", timedelta(seconds=10))
    login(1)
    root = _write(client).json()["id"]
    res = _write(client, parent_id=root)  # 답글도 같은 간격
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "TOO_FREQUENT"

    login(2)  # 다른 사람은 바로 쓸 수 있다
    assert _write(client, parent_id=root).status_code == 201


# --------------------------------------------------------------------------
# 삭제
# --------------------------------------------------------------------------


def test_only_author_can_delete(client, login):
    login(1)
    comment_id = _write(client).json()["id"]

    login(2)
    res = client.delete(f"/api/comments/{comment_id}")
    assert (res.status_code, res.json()["error"]["code"]) == (403, "NOT_OWNER")

    login(1)
    assert client.delete(f"/api/comments/{comment_id}").status_code == 204
    assert _list(client)["items"] == []


def test_delete_reply_updates_count(client, login):
    login(1)
    root = _write(client).json()["id"]
    reply = _write(client, body="답글", parent_id=root).json()["id"]
    assert client.delete(f"/api/comments/{reply}").status_code == 204

    item = _list(client)["items"][0]
    assert (item["reply_count"], item["replies"]) == (0, [])


def test_delete_comment_removes_replies(client, login):
    login(1)
    root = _write(client).json()["id"]
    login(2)
    reply = _write(client, body="답글", parent_id=root).json()["id"]
    client.post(f"/api/comments/{reply}/like")

    login(1)
    assert client.delete(f"/api/comments/{root}").status_code == 204
    assert _list(client)["total"] == 0
    login(2)
    res = client.delete(f"/api/comments/{reply}")
    assert res.json()["error"]["code"] == "COMMENT_NOT_FOUND"


# --------------------------------------------------------------------------
# 좋아요
# --------------------------------------------------------------------------


def test_like_is_idempotent_and_works_on_replies(client, login):
    login(1)
    root = _write(client).json()["id"]
    reply = _write(client, body="답글", parent_id=root).json()["id"]
    login(2)
    for target in (root, reply):
        for _ in range(2):
            res = client.post(f"/api/comments/{target}/like")
            assert res.json() == {"like_count": 1, "liked_by_me": True}
    for _ in range(2):
        res = client.delete(f"/api/comments/{root}/like")
        assert res.json() == {"like_count": 0, "liked_by_me": False}
    assert _list(client)["items"][0]["replies"][0]["liked_by_me"] is True


# --------------------------------------------------------------------------
# 신고
# --------------------------------------------------------------------------


def _report(client, target_id, reason="solicitation", target_type="comment"):
    return client.post(
        "/api/reports", json={"target_type": target_type, "target_id": target_id, "reason": reason}
    )


def test_report_once_and_hide_after_three(client, login):
    login(1)
    comment_id = _write(client).json()["id"]

    login(2)
    assert _report(client, comment_id).status_code == 204
    res = _report(client, comment_id)
    assert (res.status_code, res.json()["error"]["code"]) == (409, "ALREADY_REPORTED")
    assert _list(client)["items"][0]["reported_by_me"] is True

    login(3)
    _report(client, comment_id)
    assert _list(client)["items"][0]["is_hidden"] is False
    login(4)
    _report(client, comment_id)

    comment = _list(client)["items"][0]
    assert (comment["is_hidden"], comment["body"]) == (True, "")


def test_report_reply_hides_it(client, login):
    login(1)
    root = _write(client).json()["id"]
    reply = _write(client, body="답글", parent_id=root).json()["id"]
    for n in (2, 3, 4):
        login(n)
        assert _report(client, reply, "abuse").status_code == 204

    item = _list(client)["items"][0]
    assert item["is_hidden"] is False
    assert (item["replies"][0]["is_hidden"], item["replies"][0]["body"]) == (True, "")


def test_report_missing_target(client, login):
    login(1)
    res = _report(client, "999")
    assert (res.status_code, res.json()["error"]["code"]) == (404, "TARGET_NOT_FOUND")


@pytest.mark.parametrize("overrides", [{"reason": "spam"}, {"target_type": "post"}])
def test_report_invalid_input(client, login, overrides):
    login(1)
    comment_id = _write(client).json()["id"]
    assert _report(client, comment_id, **overrides).status_code == 422
