"""회원 탈퇴: 사용자와 그 사람이 남긴 기록을 모두 지운다 (개인정보 파기).

- 내가 쓴 글·댓글은 지운다. 내 글에 다른 사람이 단 댓글·좋아요·신고도 함께 지워진다 (글이 없어지므로).
- 다른 사람 글에 내가 단 댓글·좋아요·신고, 챗봇 대화, 지갑(ADK) 내역, 학습 기록, 관심 기업도 지운다.
- 올린 사진 파일도 지운다 (실패해도 탈퇴는 끝낸다).
"""

import logging

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.models.chat import ChatConversation, ChatMessage
from app.models.community import ContentReport, Post, PostComment, PostLike
from app.models.learning import PuzzleOpenRecord, TermProgressRecord, UsageCreditsRecord
from app.models.mock_invest import MockExchange, MockTrade
from app.models.user import User
from app.models.wallet import AdkEntry
from app.models.watchlist import WatchlistRecord
from app.services import upload_service

logger = logging.getLogger(__name__)


def _photo_names(urls: list[str | None]) -> list[str]:
    return [u.rsplit("/", 1)[-1] for u in urls if u]


def delete_account(session: Session, user: User) -> None:
    uid = user.id
    my_posts = list(session.exec(select(Post.id).where(Post.user_id == uid)).all())
    # 지워질 댓글: 내가 쓴 것 + 내 글에 달린 것
    comment_rows = session.exec(
        select(PostComment.id, PostComment.image_url).where(
            (PostComment.user_id == uid) | col(PostComment.post_id).in_(my_posts)
        )
    ).all()
    comment_ids = [r[0] for r in comment_rows]
    post_images = session.exec(select(Post.image_url).where(Post.user_id == uid)).all()
    photos = _photo_names([*post_images, *(r[1] for r in comment_rows)])

    # 신고: 내가 한 신고 + 지워지는 글·댓글에 대한 신고
    session.exec(delete(ContentReport).where(ContentReport.user_id == uid))
    if my_posts:
        session.exec(
            delete(ContentReport).where(
                (ContentReport.target_type == "post") & col(ContentReport.target_id).in_(my_posts)
            )
        )
    if comment_ids:
        session.exec(
            delete(ContentReport).where(
                (ContentReport.target_type == "comment")
                & col(ContentReport.target_id).in_(comment_ids)
            )
        )

    # 내 댓글에 달린 답글은 원댓글이 사라져 갈 곳이 없으니 같이 지운다
    reply_ids = session.exec(
        select(PostComment.id).where(col(PostComment.parent_id).in_(comment_ids))
    ).all()
    gone = [*comment_ids, *reply_ids]
    # 숫자를 다시 세야 하는 남의 글: 내가 좋아요·댓글을 남긴 글
    touched = {
        pid
        for pid in [
            *session.exec(select(PostLike.post_id).where(PostLike.user_id == uid)).all(),
            *session.exec(select(PostComment.post_id).where(col(PostComment.id).in_(gone))).all(),
        ]
        if pid not in my_posts
    }
    session.exec(
        delete(PostLike).where((PostLike.user_id == uid) | col(PostLike.post_id).in_(my_posts))
    )
    if reply_ids:
        session.exec(
            delete(ContentReport).where(
                (ContentReport.target_type == "comment")
                & col(ContentReport.target_id).in_(reply_ids)
            )
        )
    session.exec(delete(PostComment).where(col(PostComment.id).in_(gone)))
    for post in session.exec(select(Post).where(col(Post.id).in_(touched))).all():
        post.like_count = len(
            session.exec(select(PostLike).where(PostLike.post_id == post.id)).all()
        )
        post.comment_count = len(
            session.exec(
                select(PostComment).where(
                    (PostComment.post_id == post.id)
                    & (PostComment.is_deleted == False)  # noqa: E712
                )
            ).all()
        )
        session.add(post)
    session.exec(delete(Post).where(Post.user_id == uid))

    session.exec(delete(ChatMessage).where(ChatMessage.user_id == uid))
    session.exec(delete(ChatConversation).where(ChatConversation.user_id == uid))
    for model in (
        AdkEntry,
        MockTrade,
        MockExchange,
        TermProgressRecord,
        UsageCreditsRecord,
        PuzzleOpenRecord,
        WatchlistRecord,
    ):
        session.exec(delete(model).where(model.user_id == uid))
    session.delete(user)
    session.commit()

    for name in photos:
        try:
            (upload_service.upload_dir() / name).unlink(missing_ok=True)
        except OSError as e:  # 사진 파일을 못 지워도 탈퇴는 끝낸다
            logger.warning("탈퇴한 사용자의 사진을 지우지 못했어요: %s (%s)", name, e)
