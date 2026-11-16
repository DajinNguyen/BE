from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AdkReason = Literal["signup", "puzzle_reward", "report", "chat"]


class AdkPrices(BaseModel):
    report: int  # AI 리포트 만들기 1회
    chat: int  # 챗봇 질문 1회


class AdkRewards(BaseModel):
    signup: int  # 가입하면 한 번
    puzzle: int  # 낱말퀴즈 보상 1회
    puzzles_per_reward: int  # 보상 1회에 필요한 퀴즈 수
    puzzle_daily_max: int  # 하루 최대 퀴즈 보상 횟수


class AdkHistoryItem(BaseModel):
    id: str
    amount: int  # 얻으면 +, 쓰면 -
    reason: AdkReason
    title: str  # 화면에 그대로 보여줄 수 있는 쉬운 말
    created_at: datetime


class Wallet(BaseModel):
    """내 앰똥깡(ADK) 지갑."""

    balance: int
    unit: str = "ADK"
    prices: AdkPrices
    rewards: AdkRewards
    history: list[AdkHistoryItem]  # 최근 내역 (최신 순, 최대 50개)
