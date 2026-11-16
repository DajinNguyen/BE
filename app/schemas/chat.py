from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChatPage = Literal["main", "company", "report", "compare", "industry", "learn", "other"]

MESSAGE_MAX = 500


class ChatContext(BaseModel):
    """사용자가 지금 보고 있는 화면. 프론트 `frontend/src/types/chat.ts` 의 `ChatContext` 와 같다.

    서버는 코드·id 로 화면 자료를 직접 찾는다. 이름 칸(`company_name` 등)은 프론트 표시용이라 받기만 한다.
    """

    model_config = ConfigDict(extra="ignore")

    page: ChatPage = "other"
    # company · report
    stock_code: str | None = Field(None, max_length=6)
    tab: str | None = Field(None, max_length=30)  # 기업 화면의 탭 (예: financials, news)
    report_id: str | None = Field(None, max_length=60)
    # compare (left = 비교할 기업, right = 지금 보는 기업)
    left_code: str | None = Field(None, max_length=6)
    right_code: str | None = Field(None, max_length=6)
    # industry
    industry_id: str | None = Field(None, max_length=50)
    # learn
    term_id: str | None = Field(None, max_length=100)


class ChatRequest(BaseModel):
    message: str = Field(max_length=MESSAGE_MAX)
    context: ChatContext = ChatContext()
    conversation_id: str | None = None  # 첫 질문이면 null, 이어서 물으면 서버가 준 id


class ChatSource(BaseModel):
    """답변이 근거로 든 기사."""

    title: str
    press: str
    date: str  # YYYY-MM-DD
    url: str


class ChatResponse(BaseModel):
    """POST /api/chat 응답."""

    answer: str
    sources: list[ChatSource] = []  # 답변에 나온 기사 (없으면 빈 목록)
    conversation_id: str
    balance: int  # 5 ADK를 뺀 뒤 잔액 (답변을 만든 뒤에만 뺀다)


class ChatMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ChatConversationOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class ChatConversationDetail(ChatConversationOut):
    messages: list[ChatMessageOut]  # 오래된 순
