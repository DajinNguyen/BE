"""앰똥깡(ADK) 지갑: 가입·낱말퀴즈로 얻고, AI 리포트·챗봇에 쓴다.

얻고 쓰는 판단은 모두 서버가 한다. 프론트가 "차감해 주세요"라고 보내는 API는 없다.
- 쓰기: AI를 부르기 전에 금액을 잡아 두고(`hold`), 결과가 제대로 나오면 확정(`settle`),
  실패하면 돌려놓는다(`release`). 그래서 결과가 나왔을 때만 빠지고, 잔액보다 많이 쓸 수 없다.
- 같은 일로 두 번 주거나 빼지 않는다: 내역마다 `ref` 가 있고 사용자마다 한 번만 쓸 수 있다.
- 잔액은 저장하지 않고 내역을 더해서 구한다 (동시에 요청이 와도 잔액이 어긋나지 않는다).
- 모의투자(1 ADK = 가상 1만 원) 때문에 ADK에 소수가 생긴다. 소수 넷째 자리(= 가상 1원)까지 쓴다.
"""

import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import insufficient_adk
from app.models.user import User
from app.models.wallet import AdkEntry
from app.schemas.wallet import PuzzleProgress, Wallet, WalletHistoryItem

SIGNUP_BONUS = 50
PUZZLE_REWARD = 20
REPORT_PRICE = 7
CHAT_PRICE = 5
HISTORY_COUNT = 10
# 잡아 둔 금액은 이 시간이 지나면 없던 일로 한다 (서버가 꺼져서 확정도 취소도 못 한 경우)
HOLD_TTL = timedelta(minutes=10)

SIGNUP_REF = "signup"

# 같은 사용자의 "잔액 확인 → 잡아 두기"가 겹치지 않게 한다 (버튼을 연달아 눌러도 한 번만)
_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)


ADK_DECIMALS = 4  # 소수 넷째 자리 = 가상 1원 (모의투자)


def adk(value: float) -> int | float:
    """ADK 금액을 소수 넷째 자리로 맞춘다. 딱 떨어지면 정수로 돌려준다 (응답에 50.0 대신 50)."""
    value = round(float(value), ADK_DECIMALS) + 0.0  # -0.0 → 0.0
    return int(value) if value.is_integer() else value


def _now() -> datetime:
    return datetime.now(UTC)


def _sum(session: Session, user_id: int, *, with_pending: bool) -> int | float:
    query = select(func.coalesce(func.sum(AdkEntry.amount), 0)).where(AdkEntry.user_id == user_id)
    if not with_pending:
        query = query.where(AdkEntry.status == "done")
    return adk(session.exec(query).one())


def _drop_stale_holds(session: Session, user_id: int) -> None:
    """결과 없이 10분이 지난 잡아 둔 금액을 돌려놓는다. 한 번의 DELETE 라서 동시에 불러도 안전하다."""
    result = session.exec(
        delete(AdkEntry).where(
            col(AdkEntry.user_id) == user_id,
            col(AdkEntry.status) == "pending",
            col(AdkEntry.created_at) < _now() - HOLD_TTL,
        )
    )
    if result.rowcount:
        session.commit()


def grant(session: Session, user_id: int, amount: float, reason: str, title: str, ref: str) -> bool:
    """ADK를 준다. 같은 `ref` 로 이미 줬으면 주지 않고 False."""
    exists = session.exec(
        select(AdkEntry.id).where(AdkEntry.user_id == user_id, AdkEntry.ref == ref)
    ).first()
    if exists is not None:
        return False
    session.add(
        AdkEntry(
            user_id=user_id,
            amount=adk(amount),
            reason=reason,
            title=title,
            ref=ref,
            created_at=_now(),
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
    grant(session, user_id, SIGNUP_BONUS, "signup", "가입 선물", SIGNUP_REF)


def balance(session: Session, user_id: int) -> int | float:
    """지금 쓸 수 있는 잔액. AI 결과를 기다리며 잡아 둔 금액은 이미 뺀 값이다.

    (리포트 만들기를 누르면 화면의 잔액이 바로 줄고, 실패하면 돌아온다)
    """
    ensure_signup_bonus(session, user_id)
    _drop_stale_holds(session, user_id)
    return _sum(session, user_id, with_pending=True)


def spend(
    session: Session, user_id: int, price: float, reason: str, title: str, ref: str
) -> int | float:
    """바로 뺀다 (결과를 기다리지 않는 차감). 같은 `ref` 로 이미 뺐으면 다시 빼지 않는다. 잔액을 돌려준다."""
    exists = session.exec(
        select(AdkEntry.id).where(AdkEntry.user_id == user_id, AdkEntry.ref == ref)
    ).first()
    if exists is None:
        try:
            settle(session, hold(session, user_id, price, reason, title, ref))
        except IntegrityError:
            # 같은 ref 의 요청이 동시에 와서 다른 쪽이 먼저 뺐다: 한 번만 빠지므로 정상으로 본다
            session.rollback()
    return balance(session, user_id)


def hold(session: Session, user_id: int, price: float, reason: str, title: str, ref: str) -> int:
    """쓸 금액을 잡아 둔다. 잔액이 모자라면 402 `INSUFFICIENT_ADK`. 잡아 둔 내역의 id 를 돌려준다."""
    with _locks[user_id]:
        ensure_signup_bonus(session, user_id)
        _drop_stale_holds(session, user_id)
        # 다른 서버 프로세스와도 겹치지 않게 사용자 행을 잠근다 (SQLite 에서는 효과가 없다)
        session.exec(select(User.id).where(User.id == user_id).with_for_update()).first()
        price = adk(price)
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
    """결과가 제대로 나왔다: 잡아 둔 금액을 확정한다.

    "아직 잡아 둔 상태일 때만" 바꾸는 한 번의 UPDATE 라서, 같은 건을 동시에 확정·환불해도 오류 없이
    둘 중 하나로 끝나고, 이미 돌려준(또는 확정한) 건에는 아무 일도 하지 않는다.
    """
    session.exec(
        update(AdkEntry)
        .where(col(AdkEntry.id) == hold_id, col(AdkEntry.status) == "pending")
        .values(status="done", created_at=_now())
    )
    session.commit()


def release(session: Session, hold_id: int) -> None:
    """결과가 나오지 않았다: 잡아 둔 금액을 돌려놓는다. (한 번의 조건부 DELETE 라서 여러 번 불러도 한 번만)"""
    session.exec(
        delete(AdkEntry).where(col(AdkEntry.id) == hold_id, col(AdkEntry.status) == "pending")
    )
    session.commit()


def get_wallet(session: Session, user_id: int) -> Wallet:
    # 순환 import 를 피하려고 여기서 가져온다 (credit_service 가 이 모듈을 쓴다)
    from app.services import credit_service

    total = balance(session, user_id)
    progress, rewards_today = credit_service.progress(session, user_id)
    entries = session.exec(
        select(AdkEntry)
        .where(AdkEntry.user_id == user_id, AdkEntry.status == "done")
        .order_by(col(AdkEntry.created_at).desc(), col(AdkEntry.id).desc())
        .limit(HISTORY_COUNT)
    ).all()
    return Wallet(
        balance=total,
        puzzle_progress=PuzzleProgress(done=progress, required=credit_service.PUZZLES_PER_REWARD),
        reward_count_today=rewards_today,
        reward_daily_limit=credit_service.DAILY_MAX_REWARDS,
        history=[
            WalletHistoryItem(
                id=str(e.id),
                type="earn" if e.amount >= 0 else "spend",
                amount=adk(abs(e.amount)),
                reason=e.title,
                created_at=(
                    e.created_at if e.created_at.tzinfo else e.created_at.replace(tzinfo=UTC)
                ),
            )
            for e in entries
        ],
    )
