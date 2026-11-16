"""앰똥깡(ADK) 지갑: 가입·낱말퀴즈로 얻고, AI 리포트·챗봇에 쓴다.

얻고 쓰는 판단은 모두 서버가 한다. 프론트가 "차감해 주세요"라고 보내는 API는 없다.
- 쓰기: AI를 부르기 전에 금액을 잡아 두고(`hold`), 결과가 제대로 나오면 확정(`settle`),
  실패하면 돌려놓는다(`release`). 그래서 결과가 나왔을 때만 빠지고, 잔액보다 많이 쓸 수 없다.
- 같은 일로 두 번 주거나 빼지 않는다: 내역마다 `ref` 가 있고 사용자마다 한 번만 쓸 수 있다.
- 잔액은 저장하지 않고 내역을 더해서 구한다 (동시에 요청이 와도 잔액이 어긋나지 않는다).
"""

import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import insufficient_adk
from app.models.user import User
from app.models.wallet import AdkEntry
from app.schemas.wallet import AdkHistoryItem, AdkPrices, AdkRewards, Wallet

SIGNUP_BONUS = 50
PUZZLE_REWARD = 20
REPORT_PRICE = 7
CHAT_PRICE = 5
HISTORY_COUNT = 50
# 잡아 둔 금액은 이 시간이 지나면 없던 일로 한다 (서버가 꺼져서 확정도 취소도 못 한 경우)
HOLD_TTL = timedelta(minutes=10)

SIGNUP_REF = "signup"

# 같은 사용자의 "잔액 확인 → 잡아 두기"가 겹치지 않게 한다 (버튼을 연달아 눌러도 한 번만)
_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)


def _now() -> datetime:
    return datetime.now(UTC)


def _sum(session: Session, user_id: int, *, with_pending: bool) -> int:
    query = select(func.coalesce(func.sum(AdkEntry.amount), 0)).where(AdkEntry.user_id == user_id)
    if not with_pending:
        query = query.where(AdkEntry.status == "done")
    return int(session.exec(query).one())


def _drop_stale_holds(session: Session, user_id: int) -> None:
    stale = session.exec(
        select(AdkEntry).where(
            AdkEntry.user_id == user_id,
            AdkEntry.status == "pending",
            col(AdkEntry.created_at) < _now() - HOLD_TTL,
        )
    ).all()
    for entry in stale:
        session.delete(entry)
    if stale:
        session.commit()


def grant(session: Session, user_id: int, amount: int, reason: str, title: str, ref: str) -> bool:
    """ADK를 준다. 같은 `ref` 로 이미 줬으면 주지 않고 False."""
    exists = session.exec(
        select(AdkEntry.id).where(AdkEntry.user_id == user_id, AdkEntry.ref == ref)
    ).first()
    if exists is not None:
        return False
    session.add(
        AdkEntry(
            user_id=user_id, amount=amount, reason=reason, title=title, ref=ref, created_at=_now()
        )
    )
    try:
        session.commit()
    except IntegrityError:  # 동시에 들어온 같은 요청이 먼저 저장했다
        session.rollback()
        return False
    return True


def ensure_signup_bonus(session: Session, user_id: int) -> None:
    """가입 축하 ADK를 한 번만 준다. (이 기능 전에 가입한 사용자도 지갑을 처음 볼 때 받는다)"""
    grant(session, user_id, SIGNUP_BONUS, "signup", "가입 축하", SIGNUP_REF)


def balance(session: Session, user_id: int) -> int:
    """확정된 잔액."""
    ensure_signup_bonus(session, user_id)
    return _sum(session, user_id, with_pending=False)


def hold(session: Session, user_id: int, price: int, reason: str, title: str, ref: str) -> int:
    """쓸 금액을 잡아 둔다. 잔액이 모자라면 402 `INSUFFICIENT_ADK`. 잡아 둔 내역의 id 를 돌려준다."""
    with _locks[user_id]:
        ensure_signup_bonus(session, user_id)
        _drop_stale_holds(session, user_id)
        # 다른 서버 프로세스와도 겹치지 않게 사용자 행을 잠근다 (SQLite 에서는 효과가 없다)
        session.exec(select(User.id).where(User.id == user_id).with_for_update()).first()
        available = _sum(session, user_id, with_pending=True)
        if available < price:
            session.rollback()
            raise insufficient_adk(price, available)
        entry = AdkEntry(
            user_id=user_id,
            amount=-price,
            reason=reason,
            title=title,
            ref=ref,
            status="pending",
            created_at=_now(),
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry.id


def settle(session: Session, hold_id: int) -> None:
    """결과가 제대로 나왔다: 잡아 둔 금액을 확정한다."""
    entry = session.get(AdkEntry, hold_id)
    if entry is not None and entry.status == "pending":
        entry.status = "done"
        entry.created_at = _now()
        session.add(entry)
        session.commit()


def release(session: Session, hold_id: int) -> None:
    """결과가 나오지 않았다: 잡아 둔 금액을 돌려놓는다."""
    entry = session.get(AdkEntry, hold_id)
    if entry is not None and entry.status == "pending":
        session.delete(entry)
        session.commit()


def get_wallet(session: Session, user_id: int) -> Wallet:
    # 순환 import 를 피하려고 여기서 가져온다 (credit_service 가 이 모듈을 쓴다)
    from app.services.credit_service import DAILY_MAX_REWARDS, PUZZLES_PER_REWARD

    total = balance(session, user_id)
    entries = session.exec(
        select(AdkEntry)
        .where(AdkEntry.user_id == user_id, AdkEntry.status == "done")
        .order_by(col(AdkEntry.created_at).desc(), col(AdkEntry.id).desc())
        .limit(HISTORY_COUNT)
    ).all()
    return Wallet(
        balance=total,
        prices=AdkPrices(report=REPORT_PRICE, chat=CHAT_PRICE),
        rewards=AdkRewards(
            signup=SIGNUP_BONUS,
            puzzle=PUZZLE_REWARD,
            puzzles_per_reward=PUZZLES_PER_REWARD,
            puzzle_daily_max=DAILY_MAX_REWARDS,
        ),
        history=[
            AdkHistoryItem(
                id=str(e.id),
                amount=e.amount,
                reason=e.reason,
                title=e.title,
                created_at=(
                    e.created_at if e.created_at.tzinfo else e.created_at.replace(tzinfo=UTC)
                ),
            )
            for e in entries
        ],
    )
