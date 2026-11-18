"""앰똥깡(ADK) 지갑 응답. 프론트 `frontend/src/types/wallet.ts` 와 같다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ADK 금액. 모의투자 때문에 소수가 생긴다 (25.7 같은 값). 딱 떨어지면 정수로 보낸다.
Adk = int | float


class PuzzleProgress(BaseModel):
    done: int  # 다음 보상까지 센 퀴즈 수
    required: int  # 보상 1회에 필요한 퀴즈 수


class WalletHistoryItem(BaseModel):
    id: str
    type: Literal["earn", "spend"]
    amount: Adk  # 양수 (얻었는지 썼는지는 type 으로 구분한다)
    reason: str  # 화면에 그대로 보여주는 이유 (예: "낱말퀴즈 보상", "AI 리포트 · 삼성전자")
    created_at: datetime


class Wallet(BaseModel):
    balance: Adk  # 지금 쓸 수 있는 ADK (AI 결과를 기다리며 잡아 둔 금액은 뺀 값)
    puzzle_progress: PuzzleProgress
    reward_count_today: int  # 오늘 받은 낱말퀴즈 보상 횟수
    reward_daily_limit: int  # 하루 최대 보상 횟수
    history: list[WalletHistoryItem]  # 최신 순, 최근 10개


class PuzzleCompletionResult(BaseModel):
    """POST /api/me/puzzle-completions 응답."""

    puzzle_progress: PuzzleProgress
    rewarded: bool  # 이번 완료로 보상(20 ADK)을 받았는지
    balance: Adk
    reward_count_today: int
    reward_daily_limit: int


class SpendRequest(BaseModel):
    reason: Literal["report", "chat"]
    target_id: str = Field(min_length=1, max_length=80)  # 같은 값으로 다시 보내면 한 번만 뺀다


class Balance(BaseModel):
    balance: Adk
