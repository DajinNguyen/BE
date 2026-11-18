from datetime import datetime

from sqlmodel import Field, SQLModel


class MockTrade(SQLModel, table=True):
    """모의투자 체결 1건. 보유 종목은 따로 저장하지 않고 체결 내역을 더해서 구한다.

    돈(가상 현금)은 앰똥깡(ADK) 지갑 그대로다. 사면 지갑에서 빠지고 팔면 지갑에 돌아온다.
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
    amount_adk: float  # 빠지거나 돌아온 ADK = amount ÷ 10,000
    created_at: datetime
