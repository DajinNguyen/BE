"""기업별 커뮤니티: 기업에 다는 댓글 · 답글(대댓글) · 좋아요 · 신고.

규칙 값은 프론트 `frontend/src/config/communityRules.ts` 와 같다. 프론트도 같은 검사를 하지만
쉽게 우회할 수 있어서 서버에서 한 번 더 검사한다.
보기는 누구나, 쓰기(댓글·답글·좋아요·신고)는 로그인한 사람만 (라우터에서 `current_user`로 막는다).
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
    report_target_not_found,
    too_frequent,
)
from app.models.community import CommentLike, CompanyComment, ContentReport
from app.models.user import User
from app.schemas.community import (
    Author,
    CommentInput,
    CommentOut,
    CommentPage,
    CommentSort,
    LikeResult,
    ReportInput,
)
from app.services import data_store
from app.services.auth_service import nickname_for
from app.services.market_clock import KST

COMMENT_MAX = 500
WRITE_INTERVAL = timedelta(seconds=10)  # 같은 사람이 댓글을 연달아 쓸 수 있는 간격 (도배 방지)
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


def _iso(dt: datetime) -> str:
    return _as_utc(dt).astimezone(KST).isoformat(timespec="seconds")


def _parse_id(raw: str) -> int | None:
    """프론트는 id를 문자열로 다룬다. 숫자가 아니면 없는 댓글로 본다."""
    return int(raw) if raw.isdigit() and len(raw) <= 18 else None


# --------------------------------------------------------------------------
# 응답 만들기
# --------------------------------------------------------------------------


def _authors(session: Session, user_ids: set[int]) -> dict[int, Author]:
    if not user_ids:
        return {}
    users = session.exec(select(User).where(col(User.id).in_(user_ids))).all()
    return {u.id: Author(id=str(u.id), nickname=nickname_for(u)) for u in users}


def _comments_out(
    session: Session,
    comments: list[CompanyComment],
    viewer: User | None,
    replies: dict[int, list[CompanyComment]] | None = None,
) -> list[CommentOut]:
    """replies 를 주면 원댓글마다 답글을 붙여서 돌려준다."""
    replies = replies or {}
    every = [*comments, *(r for rs in replies.values() for r in rs)]
    ids = [c.id for c in every]
    authors = _authors(session, {c.user_id for c in every})
    liked: set[int] = set()
    reported: set[int] = set()
    if viewer and ids:
        liked = set(
            session.exec(
                select(CommentLike.comment_id).where(
                    CommentLike.user_id == viewer.id, col(CommentLike.comment_id).in_(ids)
                )
            ).all()
        )
        reported = set(
            session.exec(
                select(ContentReport.target_id).where(
                    ContentReport.target_type == "comment",
                    ContentReport.user_id == viewer.id,
                    col(ContentReport.target_id).in_(ids),
                )
            ).all()
        )

    def out(c: CompanyComment, children: list[CommentOut]) -> CommentOut:
        return CommentOut(
            id=str(c.id),
            stock_code=c.stock_code,
            parent_id=str(c.parent_id) if c.parent_id else None,
            body="" if c.is_hidden else c.body,
            author=authors[c.user_id],
            created_at=_iso(c.created_at),
            like_count=c.like_count,
            reply_count=c.reply_count,
            liked_by_me=c.id in liked,
            reported_by_me=c.id in reported,
            is_mine=viewer is not None and c.user_id == viewer.id,
            is_hidden=c.is_hidden,
            replies=children,
        )

    return [out(c, [out(r, []) for r in replies.get(c.id, [])]) for c in comments]


# --------------------------------------------------------------------------
# 댓글 · 답글
# --------------------------------------------------------------------------


def _get_comment(session: Session, comment_id: str | None) -> CompanyComment:
    cid = _parse_id(comment_id) if comment_id else None
    comment = session.get(CompanyComment, cid) if cid is not None else None
    if comment is None:
        raise comment_not_found()
    return comment


def list_comments(
    session: Session,
    stock_code: str,
    sort: CommentSort,
    page: int,
    size: int,
    viewer: User | None,
) -> CommentPage:
    """원댓글을 한 페이지씩, 답글은 원댓글마다 오래된 순으로 모두 붙인다."""
    query = select(CompanyComment).where(
        CompanyComment.stock_code == stock_code, col(CompanyComment.parent_id).is_(None)
    )
    newest = (col(CompanyComment.created_at).desc(), col(CompanyComment.id).desc())
    if sort == "popular":  # 좋아요 많은 순, 같으면 최신순
        query = query.order_by(col(CompanyComment.like_count).desc(), *newest)
    else:
        query = query.order_by(*newest)
    rows = list(session.exec(query.offset((page - 1) * size).limit(size + 1)).all())
    roots = rows[:size]

    replies: dict[int, list[CompanyComment]] = {}
    if roots:
        children = session.exec(
            select(CompanyComment)
            .where(col(CompanyComment.parent_id).in_([c.id for c in roots]))
            .order_by(col(CompanyComment.created_at), col(CompanyComment.id))
        ).all()
        for child in children:
            replies.setdefault(child.parent_id, []).append(child)

    total = session.exec(
        select(func.count())
        .select_from(CompanyComment)
        .where(CompanyComment.stock_code == stock_code)
    ).one()
    return CommentPage(
        items=_comments_out(session, roots, viewer, replies),
        page=page,
        has_next=len(rows) > size,
        total=total,
    )


def create_comment(session: Session, stock_code: str, data: CommentInput, user: User) -> CommentOut:
    if not data_store.company_exists(stock_code):
        raise company_not_found()
    body = data.body.strip()
    if not 1 <= len(body) <= COMMENT_MAX:
        raise invalid_length(f"댓글은 1~{COMMENT_MAX}자로 써 주세요.")
    _check_banned(body)

    parent = None
    if data.parent_id is not None:
        parent = _get_comment(session, data.parent_id)
        if parent.stock_code != stock_code:
            raise comment_not_found()
        if parent.parent_id is not None:  # 답글에 단 답글은 같은 원댓글 아래로
            parent = session.get(CompanyComment, parent.parent_id)

    now = _now()
    last = session.exec(
        select(CompanyComment.created_at)
        .where(CompanyComment.user_id == user.id)
        .order_by(col(CompanyComment.created_at).desc())
        .limit(1)
    ).first()
    if last and now - _as_utc(last) < WRITE_INTERVAL:
        raise too_frequent(int(WRITE_INTERVAL.total_seconds()))

    comment = CompanyComment(
        stock_code=stock_code,
        parent_id=parent.id if parent else None,
        user_id=user.id,
        body=body,
        created_at=now,
    )
    session.add(comment)
    if parent:
        parent.reply_count += 1
        session.add(parent)
    session.commit()
    session.refresh(comment)
    return _comments_out(session, [comment], user)[0]


def _reports_on(session: Session, target_ids: list[int]) -> list[ContentReport]:
    if not target_ids:
        return []
    return list(
        session.exec(
            select(ContentReport).where(
                ContentReport.target_type == "comment",
                col(ContentReport.target_id).in_(target_ids),
            )
        ).all()
    )


def delete_comment(session: Session, comment_id: str, user: User) -> None:
    """원댓글을 지우면 답글·좋아요·신고 기록도 함께 지운다."""
    comment = _get_comment(session, comment_id)
    if comment.user_id != user.id:
        raise not_owner("내 댓글만 지울 수 있어요.")

    replies = list(
        session.exec(select(CompanyComment).where(CompanyComment.parent_id == comment.id)).all()
    )
    ids = [comment.id, *(r.id for r in replies)]
    likes = session.exec(select(CommentLike).where(col(CommentLike.comment_id).in_(ids))).all()
    for row in [*_reports_on(session, ids), *likes, *replies]:
        session.delete(row)
    if comment.parent_id is not None:
        parent = session.get(CompanyComment, comment.parent_id)
        parent.reply_count = max(parent.reply_count - 1, 0)
        session.add(parent)
    session.flush()  # 답글·좋아요를 먼저 지워야 댓글을 지울 수 있다 (외래 키)
    session.delete(comment)
    session.commit()


# --------------------------------------------------------------------------
# 좋아요 · 신고
# --------------------------------------------------------------------------


def set_like(session: Session, comment_id: str, user: User, liked: bool) -> LikeResult:
    """같은 요청을 여러 번 보내도 결과가 같다."""
    comment = _get_comment(session, comment_id)
    existing = session.get(CommentLike, (comment.id, user.id))
    if liked and existing is None:
        session.add(CommentLike(comment_id=comment.id, user_id=user.id))
    elif not liked and existing is not None:
        session.delete(existing)
    session.flush()
    comment.like_count = session.exec(
        select(func.count()).select_from(CommentLike).where(CommentLike.comment_id == comment.id)
    ).one()
    session.add(comment)
    try:
        session.commit()
    except IntegrityError:  # 같은 사람이 동시에 두 번 누른 경우: 이미 반영됐다
        session.rollback()
        session.refresh(comment)
    return LikeResult(like_count=comment.like_count, liked_by_me=liked)


def report(session: Session, data: ReportInput, user: User) -> None:
    """같은 사람은 같은 댓글을 한 번만. 서로 다른 사람의 신고가 쌓이면 숨긴다."""
    tid = _parse_id(data.target_id)
    target = session.get(CompanyComment, tid) if tid is not None else None
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
