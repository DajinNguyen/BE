from sqlmodel import select

from app.core.db import new_session
from app.models.chat import ChatConversation
from app.models.community import ContentReport, Post, PostComment, PostLike
from app.models.user import User
from app.models.wallet import AdkEntry
from app.services import auth_service, upload_service, wallet_service
from tests.conftest import login_as
from tests.images import make_jpeg

STOCK = "005930"
POST = {"category": "free", "title": "제목", "body": "본문"}


def _post(client):
    res = client.post(f"/api/companies/{STOCK}/posts", json=POST)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _count(model):
    with new_session() as s:
        return len(s.exec(select(model)).all())


def test_delete_requires_login(client):
    assert client.delete("/api/auth/me").status_code == 401


def test_delete_removes_user_and_logs_out(client):
    uid = login_as(client, 1)
    res = client.delete("/api/auth/me")
    assert res.status_code == 204
    with new_session() as s:
        assert s.get(User, uid) is None
    assert client.get("/api/auth/me").status_code == 401  # 쿠키도 지워짐


def test_delete_removes_personal_records(client):
    uid = login_as(client, 1)
    with new_session() as s:
        wallet_service.grant(s, uid, 100, "signup", "테스트", "t1")
        now = auth_service._now()
        s.add(ChatConversation(user_id=uid, title="대화", created_at=now, updated_at=now))
        s.commit()
    client.put("/api/me/watchlist", json={"stock_codes": [STOCK]})
    assert _count(AdkEntry) == 1
    client.delete("/api/auth/me")
    assert _count(AdkEntry) == 0
    assert _count(ChatConversation) == 0


def test_delete_removes_my_posts_and_what_hangs_on_them(client):
    login_as(client, 2)
    other_post = _post(client)  # 다른 사람(2번)의 글
    login_as(client, 1)
    mine = _post(client)
    client.post(f"/api/posts/{other_post}/like")
    c = client.post(f"/api/posts/{other_post}/comments", json={"body": "내 댓글"}).json()
    client.post(
        f"/api/posts/{other_post}/comments", json={"body": "내 댓글2", "parent_id": c["id"]}
    )
    login_as(client, 2)
    client.post(f"/api/posts/{mine}/comments", json={"body": "남이 내 글에 단 댓글"})
    client.post(f"/api/posts/{mine}/like")
    client.post(
        f"/api/posts/{other_post}/comments", json={"body": "2번 댓글", "parent_id": c["id"]}
    )
    client.post(
        "/api/reports", json={"target_type": "post", "target_id": int(mine), "reason": "ad"}
    )

    login_as(client, 1)
    assert client.delete("/api/auth/me").status_code == 204

    with new_session() as s:
        assert s.get(Post, int(mine)) is None
        post = s.get(Post, int(other_post))
        assert post is not None  # 남의 글은 남는다
        assert (post.like_count, post.comment_count) == (0, 0)
        assert s.exec(select(PostLike)).all() == []
        assert s.exec(select(PostComment)).all() == []  # 내 댓글과 거기 달린 답글, 내 글의 댓글
        assert s.exec(select(ContentReport)).all() == []


def test_delete_removes_uploaded_photos(client, tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    login_as(client, 1)
    name = upload_service.save_image(make_jpeg())
    with new_session() as s:
        s.add(
            Post(
                stock_code=STOCK,
                user_id=s.exec(select(User)).first().id,
                category="free",
                title="t",
                body="b",
                image_url=f"http://testserver/uploads/{name}",
                created_at=auth_service._now(),
            )
        )
        s.commit()
    assert (tmp_path / name).exists()
    client.delete("/api/auth/me")
    assert not (tmp_path / name).exists()
