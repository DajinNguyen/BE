from pydantic import BaseModel


class FinancialLink(BaseModel):
    label: str
    value: str


class NewsItem(BaseModel):
    id: str
    title: str
    url: str | None
    source: str | None
    published_at: str | None
    summary: str
    financial_link: FinancialLink | None = None
    is_placeholder: bool
