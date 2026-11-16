from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class AdkEntry(SQLModel, table=True):
    """앰똥깡(ADK) 지갑 내역 1건. 잔액은 따로 저장하지 않고 내역을 더해서 구한다.

    - amount: 얻으면 +, 쓰면 -
    - status: `done`(확정) | `pending`(AI 결과를 기다리는 동안 잡아 둔 금액. 실패하면 지운다)
    - ref: 같은 일로 두 번 주거나 빼지 않게 하는 열쇠. 사용자마다 한 번만 쓸 수 있다
      (예: `signup`, `puzzle:2026-11-16:1`, `report:005930:…`)
    """

    __tablename__ = "adk_entries"
    __table_args__ = (UniqueConstraint("user_id", "ref", name="uq_adk_entries_user_ref"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    amount: int
    reason: str = Field(max_length=20)  # signup | puzzle_reward | report | chat
    title: str = Field(max_length=100)  # 내역에 보여줄 쉬운 말
    ref: str = Field(max_length=120)
    status: str = Field(default="done", max_length=10)
    created_at: datetime
