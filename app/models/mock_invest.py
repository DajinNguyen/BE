from datetime import datetime

from sqlmodel import Field, SQLModel


class MockTrade(SQLModel, table=True):
    """모의투자 체결 1건. 보유 종목은 따로 저장하지 않고 체결 내역을 더해서 구한다.

    돈은 환전한 가상 현금(원)이다. 사면 가상 현금이 줄고 팔면 늘어난다 (paid_with="won").
    환전 기능 전의 체결(paid_with=None)은 앰똥깡 지갑으로 바로 사고팔았다 (amount_adk).
    """

    __tablename__ = "mock_trades"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    stock_code: str = Field(max_length=6)
    name: str = Field(max_length=100)  # 체결할 때의 회사 이름 (거래 내역에 그대로 보여준다)
    side: str = Field(max_length=4)  # buy | sell
    quantity: int
    price: int  # 체결 가격 (원, 지연 시세)
    amount: int  # 체결 금액 = price × quantity (원)
    amount_adk: float  # 환전 기능 전: 빠지거나 돌아온 ADK = amount ÷ 10,000. 그 뒤로는 0
    # "won": 가상 현금으로 사고팔았다. None: 환전 기능 전 (앰똥깡으로 사고팔아 가상 현금과 무관)
    paid_with: str | None = Field(default=None, max_length=4)
    created_at: datetime


class MockExchange(SQLModel, table=True):
    """앰똥깡 → 가상 현금 환전 1건 (1 ADK = 가상 1만 원). 되돌릴 수 없다."""

    __tablename__ = "mock_exchanges"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    adk: int  # 빠진 ADK (정수)
    won: int  # 들어온 가상 현금 = adk × 10,000
    created_at: datetime
