from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import current_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.learning import PuzzleCompletion, TermProgress, UsageCredits
from app.schemas.wallet import Balance, PuzzleCompletionResult, SpendRequest, Wallet
from app.services import credit_service, learning_service, wallet_service

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/term-progress", response_model=TermProgress)
def get_term_progress(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 용어 학습 기록. 기록이 없으면 빈 기록(`terms: {}`)."""
    return learning_service.get_progress(session, user.id)


@router.put("/term-progress", response_model=TermProgress)
def put_term_progress(
    body: TermProgress,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """내 용어 학습 기록 전체를 한 번에 저장하고, 저장한 값을 돌려준다."""
    return learning_service.save_progress(session, user.id, body)


@router.get("/usage-credits", response_model=UsageCredits)
def get_usage_credits(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 낱말퀴즈 보상 기록 (예전 프론트용. 지금 프론트는 `GET /api/me/wallet` 을 쓴다)."""
    return credit_service.get_credits(session, user.id)


@router.post("/puzzle-completions", response_model=PuzzleCompletionResult)
def post_puzzle_completion(
    body: PuzzleCompletion,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """낱말퀴즈를 다 풀었다고 알린다 (`{ puzzle_id, used_reveal }`).

    보상은 서버 날짜 기준으로 판단하고, 진행도·보상 여부·잔액을 돌려준다.
    """
    return credit_service.complete_puzzle(session, user.id, body)


@router.get("/wallet", response_model=Wallet)
def get_wallet(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 앰똥깡(ADK) 지갑: 잔액, 낱말퀴즈 진행, 최근 내역 10개. 처음 보면 가입 선물 ADK를 받는다."""
    return wallet_service.get_wallet(session, user.id)


SPEND = {
    "report": (wallet_service.REPORT_PRICE, "AI 리포트"),
    "chat": (wallet_service.CHAT_PRICE, "AI 챗봇 질문"),
}


@router.post("/wallet/spend", response_model=Balance)
def spend(
    body: SpendRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """ADK를 바로 뺀다. 같은 `reason`·`target_id` 로 다시 보내면 한 번만 뺀다.

    리포트 만들기와 챗봇은 각 API 안에서 서버가 알아서 빼므로 이 API를 같이 부르면 안 된다 (두 번 빠진다).
    결과와 상관없이 차감이 필요한 다른 곳을 위한 API다. 잔액이 모자라면 400 `INSUFFICIENT_BALANCE`.
    """
    price, title = SPEND[body.reason]
    ref = f"spend:{body.reason}:{body.target_id}"
    return Balance(balance=wallet_service.spend(session, user.id, price, body.reason, title, ref))
