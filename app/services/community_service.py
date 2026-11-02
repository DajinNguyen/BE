"""기업별 커뮤니티: 글 · 댓글 · 좋아요 · 신고.

규칙 값은 프론트 `frontend/src/config/communityRules.ts` 와 같다. 프론트도 같은 검사를 하지만
쉽게 우회할 수 있어서 서버에서 한 번 더 검사한다.
보기는 누구나, 쓰기(글·댓글·좋아요·신고)는 로그인한 사람만 (라우터에서 `current_user`로 막는다).
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import (
    already_reported,
    banned_word,
    comment_not_found,
    company_not_found,
    invalid_length,
    not_owner,
    post_not_found,
    report_target_not_found,
    too_frequent,
)
from app.models.community import ContentReport, Post, PostComment, PostLike
from app.models.user import User
from app.schemas.community import (
    Author,
    CommentInput,
    CommentOut,
    LikeResult,
    PostInput,
    PostOut,
    PostPage,
    PostSort,
    ReportInput,
)
from app.services import data_store
from app.services.auth_service import nickname_for
from app.services.market_clock import KST

TITLE_MAX = 50
BODY_MAX = 2000
COMMENT_MAX = 500
POST_INTERVAL = timedelta(seconds=30)  # 같은 사람이 글을 연달아 쓸 수 있는 간격 (도배 방지)
HIDE_AFTER_REPORTS = 3  # 서로 다른 사람의 신고가 이만큼 쌓이면 숨긴다

# 띄어쓰기·대소문자를 무시하고 찾는다. 두 번째 값은 안내 문구에 보여줄 이름 (욕설은 그대로 보여주지 않는다)
_BANNED_RULES = [
    (re.compile(r"리딩방|리딩룸"), '"리딩방" 같은 유료 추천방 홍보'),
    (
        re.compile(r"오픈채팅|오픈톡|open\.kakao\.com|카톡방|텔레그램|t\.me/"),
        "오픈채팅·메신저 방 링크",
    ),
    (
        re.compile(r"수익보장|원금보장|무료추천주|급등주추천|종목추천해드"),
        "수익 보장·종목 추천 표현",
    ),
    (re.compile(r"씨발|시발|ㅅㅂ|병신|ㅂㅅ|개새|좆|존나|지랄"), "욕설"),
]


def find_banned_word(text: str) -> str | None:
    normalized = re.sub(r"\s+", "", text).lower()
    return next((label for pattern, label in _BANNED_RULES if pattern.search(normalized)), None)


def _check_banned(text: str) -> None:
    label = find_banned_word(text)
    if label:
        raise banned_word(label)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """SQLite는 시간대를 버리고 저장해서, 읽어 온 값은 UTC로 다시 붙인다."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return _as_utc(dt).astimezone(KST).isoformat(timespec="seconds") if dt else None


def _parse_id(raw: str) -> int | None:
    """프론트는 id를 문자열로 다룬다. 숫자가 아니면 없는 글로 본다."""
    return int(raw) if raw.isdigit() and len(raw) <= 18 else None


# --------------------------------------------------------------------------
# 응답 만들기
# --------------------------------------------------------------------------


def _authors(session: Session, user_ids: set[int]) -> dict[int, Author]:
    if not user_ids:
        return {}
    users = session.exec(select(User).where(col(User.id).in_(user_ids))).all()
    return {u.id: Author(id=str(u.id), nickname=nickname_for(u)) for u in users}


def _posts_out(session: Session, posts: list[Post], viewer: User | None) -> list[PostOut]:
    ids = [p.id for p in posts]
    authors = _authors(session, {p.user_id for p in posts})
    liked: set[int] = set()
    reported: set[int] = set()
    if viewer and ids:
        liked = set(
            session.exec(
                select(PostLike.post_id).where(
                    PostLike.user_id == viewer.id, col(PostLike.post_id).in_(ids)
                )
            ).all()
        )
        reported = set(
            session.exec(
                select(ContentReport.target_id).where(
                    ContentReport.target_type == "post",
                    ContentReport.user_id == viewer.id,
                    col(ContentReport.target_id).in_(ids),
                )
            ).all()
        )
    return [
        PostOut(
            id=str(p.id),
            stock_code=p.stock_code,
            category=p.category,
            title="" if p.is_hidden else p.title,
            body="" if p.is_hidden else p.body,
            author=authors[p.user_id],
            created_at=_iso(p.created_at),
            updated_at=_iso(p.updated_at),
            like_count=p.like_count,
            comment_count=p.comment_count,
            liked_by_me=p.id in liked,
            reported_by_me=p.id in reported,
            is_mine=viewer is not None and p.user_id == viewer.id,
            is_hidden=p.is_hidden,
        )
        for p in posts
    ]


def _comments_out(
    session: Session, comments: list[PostComment], viewer: User | None
) -> list[CommentOut]:
    authors = _authors(session, {c.user_id for c in comments})
    return [
        CommentOut(
            id=str(c.id),
            post_id=str(c.post_id),
            body="" if c.is_hidden else c.body,
            author=authors[c.user_id],
            created_at=_iso(c.created_at),
            is_mine=viewer is not None and c.user_id == viewer.id,
            is_hidden=c.is_hidden,
        )
        for c in comments
    ]


# --------------------------------------------------------------------------
# 글
# --------------------------------------------------------------------------


def _get_post(session: Session, post_id: str) -> Post:
    pid = _parse_id(post_id)
    post = session.get(Post, pid) if pid is not None else None
    if post is None:
        raise post_not_found()
    return post


def _clean_post(data: PostInput) -> tuple[str, str]:
    title, body = data.title.strip(), data.body.strip()
    if not 1 <= len(title) <= TITLE_MAX or not 1 <= len(body) <= BODY_MAX:
        raise invalid_length(f"제목은 1~{TITLE_MAX}자, 본문은 1~{BODY_MAX:,}자로 써 주세요.")
    _check_banned(f"{title} {body}")
    return title, body


def list_posts(
    session: Session,
    stock_code: str,
    sort: PostSort,
    category: str | None,
    page: int,
    size: int,
    viewer: User | None,
) -> PostPage:
    query = select(Post).where(Post.stock_code == stock_code)
    if category:
        query = query.where(Post.category == category)
    newest = (col(Post.created_at).desc(), col(Post.id).desc())
    if sort == "popular":  # 좋아요 많은 순, 같으면 최신순
        query = query.order_by(col(Post.like_count).desc(), *newest)
    else:
        query = query.order_by(*newest)
    rows = list(session.exec(query.offset((page - 1) * size).limit(size + 1)).all())
    return PostPage(
        items=_posts_out(session, rows[:size], viewer), page=page, has_next=len(rows) > size
    )


def get_post(session: Session, post_id: str, viewer: User | None) -> PostOut:
    return _posts_out(session, [_get_post(session, post_id)], viewer)[0]


def create_post(session: Session, stock_code: str, data: PostInput, user: User) -> PostOut:
    if not data_store.company_exists(stock_code):
        raise company_not_found()
    title, body = _clean_post(data)
    now = _now()
    last = session.exec(
        select(Post.created_at)
        .where(Post.user_id == user.id)
        .order_by(col(Post.created_at).desc())
        .limit(1)
    ).first()
    if last and now - _as_utc(last) < POST_INTERVAL:
        raise too_frequent(int(POST_INTERVAL.total_seconds()))

    post = Post(
        stock_code=stock_code,
        user_id=user.id,
        category=data.category,
        title=title,
        body=body,
        created_at=now,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return _posts_out(session, [post], user)[0]


def update_post(session: Session, post_id: str, data: PostInput, user: User) -> PostOut:
    post = _get_post(session, post_id)
    if post.user_id != user.id:
        raise not_owner("내 글만 고칠 수 있어요.")
    post.title, post.body = _clean_post(data)
    post.category = data.category
    post.updated_at = _now()
    session.add(post)
    session.commit()
    session.refresh(post)
    return _posts_out(session, [post], user)[0]


def _reports_on(session: Session, target_type: str, target_ids: list[int]) -> list[ContentReport]:
    if not target_ids:
        return []
    return list(
        session.exec(
            select(ContentReport).where(
                ContentReport.target_type == target_type,
                col(ContentReport.target_id).in_(target_ids),
            )
        ).all()
    )


def delete_post(session: Session, post_id: str, user: User) -> None:
    """글을 지우면 댓글·좋아요·신고 기록도 함께 지운다."""
    post = _get_post(session, post_id)
    if post.user_id != user.id:
        raise not_owner("내 글만 지울 수 있어요.")
    comments = session.exec(select(PostComment).where(PostComment.post_id == post.id)).all()
    likes = session.exec(select(PostLike).where(PostLike.post_id == post.id)).all()
    reports = _reports_on(session, "post", [post.id]) + _reports_on(
        session, "comment", [c.id for c in comments]
    )
    for row in [*reports, *likes, *comments]:
        session.delete(row)
    session.flush()  # 댓글·좋아요를 먼저 지워야 글을 지울 수 있다 (외래 키)
    session.delete(post)
    session.commit()


# --------------------------------------------------------------------------
# 댓글
# --------------------------------------------------------------------------


def list_comments(session: Session, post_id: str, viewer: User | None) -> list[CommentOut]:
    """오래된 순."""
    post = _get_post(session, post_id)
    comments = session.exec(
        select(PostComment)
        .where(PostComment.post_id == post.id)
        .order_by(col(PostComment.created_at), col(PostComment.id))
    ).all()
    return _comments_out(session, list(comments), viewer)


def create_comment(session: Session, post_id: str, data: CommentInput, user: User) -> CommentOut:
    post = _get_post(session, post_id)
    body = data.body.strip()
    if not 1 <= len(body) <= COMMENT_MAX:
        raise invalid_length(f"댓글은 1~{COMMENT_MAX}자로 써 주세요.")
    _check_banned(body)

    comment = PostComment(post_id=post.id, user_id=user.id, body=body, created_at=_now())
    post.comment_count += 1
    session.add_all([comment, post])
    session.commit()
    session.refresh(comment)
    return _comments_out(session, [comment], user)[0]


def delete_comment(session: Session, comment_id: str, user: User) -> None:
    cid = _parse_id(comment_id)
    comment = session.get(PostComment, cid) if cid is not None else None
    if comment is None:
        raise comment_not_found()
    if comment.user_id != user.id:
        raise not_owner("내 댓글만 지울 수 있어요.")
    for report in _reports_on(session, "comment", [comment.id]):
        session.delete(report)
    post = session.get(Post, comment.post_id)
    post.comment_count = max(post.comment_count - 1, 0)
    session.add(post)
    session.delete(comment)
    session.commit()


# --------------------------------------------------------------------------
# 좋아요 · 신고
# --------------------------------------------------------------------------


def set_like(session: Session, post_id: str, user: User, liked: bool) -> LikeResult:
    """같은 요청을 여러 번 보내도 결과가 같다."""
    post = _get_post(session, post_id)
    existing = session.get(PostLike, (post.id, user.id))
    if liked and existing is None:
        session.add(PostLike(post_id=post.id, user_id=user.id))
    elif not liked and existing is not None:
        session.delete(existing)
    session.flush()
    post.like_count = session.exec(
        select(func.count()).select_from(PostLike).where(PostLike.post_id == post.id)
    ).one()
    session.add(post)
    try:
        session.commit()
    except IntegrityError:  # 같은 사람이 동시에 두 번 누른 경우: 이미 반영됐다
        session.rollback()
        session.refresh(post)
    return LikeResult(like_count=post.like_count, liked_by_me=liked)


def report(session: Session, data: ReportInput, user: User) -> None:
    """같은 사람은 같은 대상을 한 번만. 서로 다른 사람의 신고가 쌓이면 숨긴다."""
    tid = _parse_id(data.target_id)
    model = Post if data.target_type == "post" else PostComment
    target = session.get(model, tid) if tid is not None else None
    if target is None:
        raise report_target_not_found()

    same = (ContentReport.target_type == data.target_type, ContentReport.target_id == target.id)
    if session.exec(select(ContentReport).where(*same, ContentReport.user_id == user.id)).first():
        raise already_reported()
    session.add(
        ContentReport(
            target_type=data.target_type,
            target_id=target.id,
            user_id=user.id,
            reason=data.reason,
            created_at=_now(),
        )
    )
    try:
        session.flush()
    except IntegrityError:  # 동시에 두 번 보낸 경우
        session.rollback()
        raise already_reported() from None
    count = session.exec(select(func.count()).select_from(ContentReport).where(*same)).one()
    if count >= HIDE_AFTER_REPORTS:
        target.is_hidden = True
        session.add(target)
    session.commit()
