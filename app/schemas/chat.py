from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ChatPage = Literal["home", "company", "industry", "term", "compare", "other"]
CompanyTab = Literal["overview", "report", "financials", "news", "community"]

MESSAGE_MAX = 500


class ChatContext(BaseModel):
    """사용자가 지금 보고 있는 화면. 서버가 이 화면의 자료를 찾아 답변 근거로 쓴다."""

    page: ChatPage = "other"
    stock_code: str | None = Field(None, max_length=6)  # 기업 화면이면 종목코드
    tab: CompanyTab | None = None  # 기업 화면의 탭
    industry_id: str | None = Field(None, max_length=50)  # 산업 화면이면 산업 id
    term_id: str | None = Field(None, max_length=100)  # 용어 카드면 용어 id
    compare_stock_code: str | None = Field(None, max_length=6)  # 비교 화면이면 견주는 회사


class ChatRequest(BaseModel):
    message: str = Field(max_length=MESSAGE_MAX)
    context: ChatContext = ChatContext()
    conversation_id: str | None = None  # 이어서 묻는 대화. 없으면 새 대화를 만든다


class ChatMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ChatReply(BaseModel):
    conversation_id: str
    question: ChatMessageOut
    answer: ChatMessageOut
    charged: int  # 이 질문으로 빠진 ADK (투자 추천을 거절한 답은 0)
    balance: int  # 지금 내 ADK 잔액


class ChatConversationOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class ChatConversationDetail(ChatConversationOut):
    messages: list[ChatMessageOut]  # 오래된 순
