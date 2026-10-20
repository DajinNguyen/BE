from typing import Literal

from pydantic import BaseModel

TermCategory = Literal["basic", "financial_statement", "indicator", "market", "industry"]
TermLevel = Literal["beginner", "intermediate"]


class Term(BaseModel):
    id: str
    term: str
    aliases: list[str]
    category: TermCategory
    level: TermLevel
    easy: str
    example: str
